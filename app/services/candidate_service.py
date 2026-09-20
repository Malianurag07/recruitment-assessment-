"""Ties the pipeline together: file -> text -> profile -> verify -> canonical skills -> dedupe -> score -> SQLite.

All LLM callables are injectable (extract_llm, verify_llm, canon_llm, score_llm) so tests need no API keys.
"""
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass

from app.config import SEMANTIC_INDEXING
from app.llm import client
from app.llm.extraction import extract_profile
from app.llm.jd_parsing import extract_job
from app.llm.retrieval import chunk_text, pack
from app.llm.scoring import ScoreResult, score_candidate
from app.llm.skill_canonicalizer import canonicalize_profile, canonicalize_skills, save_learned
from app.llm.verification import verify_profile
from app.models import CandidateProfile, JobProfile
from app.parsing.document_extractor import extract_document
from app.services import dedupe
from app.services.skill_normalizer import load_aliases


@dataclass
class Outcome:
    filename: str
    status: str            # stored | duplicate_ignored | conflict_pending | needs_review | rejected_file
    message: str = ""
    application_id: int | None = None
    candidate_id: int | None = None
    score: float | None = None
    recommendation: str | None = None
    verification_status: str | None = None


@contextmanager
def _write_lock(conn: sqlite3.Connection):
    """Take SQLite's write lock up front (BEGIN IMMEDIATE), so the duplicate check and the insert happen atomically.

    Without it, two uploads of the same person could both see "no active resume yet" and both insert.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _kw(llm):
    return {} if llm is None else {"llm": llm}


# ---------------------------------------------------------------- jobs
def create_job(conn: sqlite3.Connection, text: str, canon_llm=None, jd_llm=None) -> tuple[int | None, str]:
    """Parse a job description and store it. Returns (job_id, message); job_id is None on failure."""
    res = extract_job(text, **_kw(jd_llm))
    if res.status != "ok":
        return None, res.error or "Could not understand the job description"
    job = res.job
    aliases = load_aliases(conn)
    mapping, learned = canonicalize_skills(job.required_skills + job.preferred_skills, aliases, **_kw(canon_llm))
    with conn:
        save_learned(conn, learned)
        cur = conn.execute(
            "INSERT INTO job_descriptions (title, raw_text, min_experience_years, soft_skills, summary) VALUES (?,?,?,?,?)",
            (job.title, text, job.min_experience_years, json.dumps(job.soft_skills), job.summary))
        job_id, seen = cur.lastrowid, set()
        for importance, skills in (("required", job.required_skills), ("preferred", job.preferred_skills)):
            for s in skills:
                canon = mapping[s]
                if canon.lower() not in seen:
                    seen.add(canon.lower())
                    conn.execute("INSERT INTO job_required_skills (job_description_id, skill_name, importance) VALUES (?,?,?)",
                                 (job_id, canon, importance))
    return job_id, "ok"


def get_job(conn: sqlite3.Connection, job_id: int) -> JobProfile | None:
    row = conn.execute("SELECT * FROM job_descriptions WHERE id=?", (job_id,)).fetchone()
    if row is None:
        return None
    skills = conn.execute("SELECT skill_name, importance FROM job_required_skills WHERE job_description_id=? ORDER BY id",
                          (job_id,)).fetchall()
    return JobProfile(
        title=row["title"], min_experience_years=row["min_experience_years"], summary=row["summary"],
        soft_skills=json.loads(row["soft_skills"] or "[]"),
        required_skills=[r["skill_name"] for r in skills if r["importance"] == "required"],
        preferred_skills=[r["skill_name"] for r in skills if r["importance"] == "preferred"])


# ---------------------------------------------------------------- resumes
def _store_analysis(conn, app_id: int, job_id: int, s: ScoreResult) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO analysis_results
           (application_id, job_description_id, match_score, skill_match_ratio, component_scores, skill_breakdown,
            llm_status, matching_skills, missing_skills, strengths, weaknesses, summary, interview_questions, recommendation)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (app_id, job_id, s.match_score, s.skill_match_ratio, json.dumps(s.components),
         json.dumps([r.__dict__ for r in s.skill_breakdown]), s.llm_status, json.dumps(s.matching_skills),
         json.dumps(s.missing_skills), json.dumps(s.strengths), json.dumps(s.weaknesses), s.summary,
         json.dumps(s.interview_questions), s.recommendation))


def process_resume(conn: sqlite3.Connection, data: bytes, filename: str, job_id: int, *,
                   extract_llm=None, verify_llm=None, canon_llm=None, score_llm=None, embed_llm=None) -> Outcome:
    job = get_job(conn, job_id)
    if job is None:
        return Outcome(filename, "rejected_file", f"Unknown job id {job_id}")

    doc = extract_document(data, filename)
    if not doc.ok:
        return Outcome(filename, "rejected_file", doc.message)

    ext = extract_profile(doc.text, **_kw(extract_llm))
    if ext.profile is None:
        return Outcome(filename, "needs_review", f"Could not extract data: {ext.error}")

    aliases = load_aliases(conn)
    ver = verify_profile(ext.profile, doc.text, aliases, **_kw(verify_llm))
    profile, pairs, learned = canonicalize_profile(ver.profile, aliases, **_kw(canon_llm))
    aliases.update(learned)

    # Fast path (no lock): skip needless AI scoring for obvious duplicates and conflicts.
    early = dedupe.decide(conn, profile.email, profile.phone, job_id, doc.text)
    if early.kind == dedupe.IDENTICAL:
        return Outcome(filename, "duplicate_ignored", "Identical resume already submitted for this job",
                       early.existing_application_id, early.candidate_id)
    scorable = early.kind != dedupe.CONFLICT and ext.status == "ok" and ver.status != "needs_review"
    result = score_candidate(profile, job, aliases, **_kw(score_llm)) if scorable else None

    with _write_lock(conn):
        # Authoritative check under the write lock: another upload may have won the race while we were scoring.
        decision = dedupe.decide(conn, profile.email, profile.phone, job_id, doc.text)
        if decision.kind == dedupe.IDENTICAL:
            conn.rollback()
            return Outcome(filename, "duplicate_ignored", "Identical resume already submitted for this job",
                           decision.existing_application_id, decision.candidate_id)
        pending = decision.kind == dedupe.CONFLICT
        if pending:
            result = None                      # a pending resume is not scored until the applicant chooses it
        save_learned(conn, learned)
        cid = decision.candidate_id or conn.execute(
            "INSERT INTO candidates (name, email, phone) VALUES (?,?,?)",
            (profile.name, profile.email, profile.phone)).lastrowid
        app_id = conn.execute(
            """INSERT INTO applications
               (candidate_id, job_description_id, resume_filename, resume_hash, raw_text, education, experience_years,
                experience_detail, internships, soft_skills, profile_json, projects, certifications,
                extraction_status, verification_status, application_status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (cid, job_id, filename, dedupe.resume_hash(doc.text), doc.text,
             json.dumps([e.model_dump() for e in profile.education]), profile.experience_years,
             json.dumps([j.model_dump() for j in profile.experience]),
             json.dumps([j.model_dump() for j in profile.internships]), json.dumps(profile.soft_skills),
             profile.model_dump_json(), json.dumps(profile.projects), json.dumps(profile.certifications),
             ext.status, ver.status, "pending_choice" if pending else "active")).lastrowid
        levels = {k.lower(): v for k, v in profile.skill_levels.items()}
        conn.executemany(
            "INSERT INTO application_skills (application_id, skill_name, raw_skill, level) VALUES (?,?,?,?)",
            [(app_id, canon, raw, levels.get(canon.lower())) for canon, raw in pairs])
        conn.executemany(
            "INSERT INTO verification_log (application_id, field, old_value, new_value, evidence_quote) VALUES (?,?,?,?,?)",
            [(app_id, c.field, c.old, c.new, f"[{c.source}] {c.evidence}") for c in ver.changes])
        if doc.hidden_text:                        # auditable: shown with the other automatic changes
            conn.execute("INSERT INTO verification_log (application_id, field, old_value, new_value, evidence_quote) VALUES (?,?,?,?,?)",
                         (app_id, "hidden_text", None, None, f"[deterministic] {len(doc.hidden_text)} characters of invisible text ignored: {doc.hidden_text[:200]!r}"))
        if result:
            _store_analysis(conn, app_id, job_id, result)

    _index_chunks(conn, app_id, doc.text, embed_llm)      # best-effort search index; never blocks an upload

    if pending:
        return Outcome(filename, "conflict_pending",
                       "A different resume already exists for this job; the applicant must choose which to keep",
                       app_id, cid, verification_status=ver.status)
    if ext.status != "ok" or ver.status == "needs_review":
        why = ext.error or "; ".join(ver.notes) or "verification flagged this resume"
        return Outcome(filename, "needs_review", why, app_id, cid, verification_status=ver.status)
    note = f"Ignored {len(doc.hidden_text)} characters of invisible text (possible keyword stuffing)." if doc.hidden_text else ""
    return Outcome(filename, "stored", note, app_id, cid, result.match_score, result.recommendation, ver.status)


# ---------------------------------------------------------------- search index (hybrid retrieval)
def _embedder(embed_llm):
    """An explicit embedder always wins (tests); otherwise use the real one only if semantic indexing is enabled."""
    if embed_llm is not None:
        return embed_llm
    return client.embed if SEMANTIC_INDEXING else None


def _index_chunks(conn: sqlite3.Connection, application_id: int, text: str, embed_llm=None) -> int:
    """Store the resume as searchable chunks. Embeddings are optional: if the API is down, keyword search still works."""
    chunks = chunk_text(text)
    vecs: list = [None] * len(chunks)
    embed = _embedder(embed_llm)
    if embed and chunks:
        try:
            vecs = embed(chunks, "RETRIEVAL_DOCUMENT")
        except client.LLMError:
            pass
    with conn:
        conn.executemany("INSERT INTO resume_chunks (application_id, chunk_index, text, embedding) VALUES (?,?,?,?)",
                         [(application_id, i, c, pack(v) if v else None) for i, (c, v) in enumerate(zip(chunks, vecs))])
    return len(chunks)


def reindex_missing_embeddings(conn: sqlite3.Connection, job_id: int, embed_llm=None) -> int:
    """Create chunks for resumes that have none, and add embeddings where they are missing. Returns chunks embedded."""
    for row in conn.execute("""SELECT a.id, a.raw_text FROM applications a WHERE a.job_description_id = ?
                               AND NOT EXISTS (SELECT 1 FROM resume_chunks c WHERE c.application_id = a.id)""", (job_id,)).fetchall():
        _index_chunks(conn, row["id"], row["raw_text"] or "", embed_llm=lambda t, k: [None] * len(t))   # chunks first, no vectors yet
    embed = _embedder(embed_llm)
    if embed is None:
        return 0
    rows = conn.execute("""SELECT c.id, c.text FROM resume_chunks c JOIN applications a ON a.id = c.application_id
                           WHERE a.job_description_id = ? AND c.embedding IS NULL""", (job_id,)).fetchall()
    if not rows:
        return 0
    try:
        vecs = embed([r["text"] for r in rows], "RETRIEVAL_DOCUMENT")
    except client.LLMError:
        return 0
    with conn:
        conn.executemany("UPDATE resume_chunks SET embedding = ? WHERE id = ?", [(pack(v), r["id"]) for r, v in zip(rows, vecs)])
    return len(rows)


def resolve_conflict(conn: sqlite3.Connection, keep_application_id: int, *, score_llm=None) -> Outcome:
    """The applicant chose which resume to keep. Activate it and score it if it has not been scored."""
    row = conn.execute("SELECT * FROM applications WHERE id=?", (keep_application_id,)).fetchone()
    if row is None:
        return Outcome("", "rejected_file", "Unknown application")
    with conn:
        dedupe.resolve_conflict(conn, keep_application_id)
    has_score = conn.execute("SELECT 1 FROM analysis_results WHERE application_id=?", (keep_application_id,)).fetchone()
    if not has_score and row["extraction_status"] == "ok" and row["verification_status"] != "needs_review":
        profile = CandidateProfile.model_validate_json(row["profile_json"])
        job = get_job(conn, row["job_description_id"])
        result = score_candidate(profile, job, load_aliases(conn), **_kw(score_llm))
        with conn:
            _store_analysis(conn, keep_application_id, row["job_description_id"], result)
    score = conn.execute("SELECT match_score, recommendation FROM analysis_results WHERE application_id=?",
                         (keep_application_id,)).fetchone()
    return Outcome(row["resume_filename"], "stored", "Applicant's choice applied", keep_application_id,
                   row["candidate_id"], score["match_score"] if score else None,
                   score["recommendation"] if score else None, row["verification_status"])


def rescore_unavailable(conn: sqlite3.Connection, job_id: int, *, score_llm=None) -> list[dict]:
    """Re-run scoring for analyses whose LLM step failed earlier (rate limit, outage). Uses the stored profile."""
    job = get_job(conn, job_id)
    aliases = load_aliases(conn)
    rows = conn.execute(
        """SELECT r.application_id, r.match_score, a.profile_json, c.name FROM analysis_results r
           JOIN applications a ON a.id = r.application_id JOIN candidates c ON c.id = a.candidate_id
           WHERE r.job_description_id = ? AND r.llm_status = 'unavailable'""", (job_id,)).fetchall()
    out = []
    for r in rows:
        result = score_candidate(CandidateProfile.model_validate_json(r["profile_json"]), job, aliases, **_kw(score_llm))
        if result.llm_status == "ok":                      # only replace when the retry actually worked
            with conn:
                _store_analysis(conn, r["application_id"], job_id, result)
        out.append({"name": r["name"], "old": r["match_score"], "new": result.match_score, "llm_status": result.llm_status})
    return out


def preview_pending_scores(conn: sqlite3.Connection, job_id: int, *, score_llm=None) -> list[dict]:
    """Score resumes waiting in `pending_choice` WITHOUT activating them, so the choice can be an informed one."""
    job = get_job(conn, job_id)
    aliases = load_aliases(conn)
    rows = conn.execute(
        """SELECT a.id, a.resume_filename, a.profile_json, a.extraction_status, a.verification_status, c.name
           FROM applications a JOIN candidates c ON c.id = a.candidate_id
           WHERE a.job_description_id = ? AND a.application_status = 'pending_choice'""", (job_id,)).fetchall()
    out = []
    for r in rows:
        if r["extraction_status"] != "ok" or r["verification_status"] == "needs_review":
            continue
        res = score_candidate(CandidateProfile.model_validate_json(r["profile_json"]), job, aliases, **_kw(score_llm))
        req = [b for b in res.skill_breakdown if b.importance == "required"]
        out.append({"application_id": r["id"], "name": r["name"], "file": r["resume_filename"], "score": res.match_score,
                    "recommendation": res.recommendation,
                    "required_matched": f"{sum(b.status != 'missing' for b in req)}/{len(req)}", "llm_status": res.llm_status})
    return sorted(out, key=lambda x: -x["score"])


# ---------------------------------------------------------------- reads
def ranking(conn: sqlite3.Connection, job_id: int, limit: int | None = None) -> list[dict]:
    """Active, scored applications for a job, best first. Pending and unscored ones are excluded."""
    sql = """SELECT a.id AS application_id, c.name, c.email, c.phone, a.resume_filename, a.experience_years,
                    r.match_score, r.recommendation, r.skill_match_ratio, r.summary
             FROM analysis_results r
             JOIN applications a ON a.id = r.application_id AND a.application_status = 'active'
             JOIN candidates c ON c.id = a.candidate_id
             WHERE r.job_description_id = ?
             ORDER BY r.match_score DESC, c.name"""
    rows = conn.execute(sql + (" LIMIT ?" if limit else ""), (job_id, limit) if limit else (job_id,)).fetchall()
    return [dict(r) for r in rows]


def pending_conflicts(conn: sqlite3.Connection, job_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT p.id AS pending_id, p.resume_filename AS new_file, act.id AS active_id, act.resume_filename AS current_file,
                  c.name, c.email
           FROM applications p JOIN candidates c ON c.id = p.candidate_id
           JOIN applications act ON act.candidate_id = p.candidate_id AND act.job_description_id = p.job_description_id
                                 AND act.application_status = 'active'
           WHERE p.job_description_id = ? AND p.application_status = 'pending_choice'""", (job_id,)).fetchall()
    return [dict(r) for r in rows]
