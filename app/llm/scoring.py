"""Hybrid candidate scoring. The rubric is documented in docs/SCORING.md.

Skills and experience are computed in code (repeatable). Project/education relevance and overall fit
come from the LLM, which is handed the computed facts so it cannot contradict them.
"""
import json
import re
import statistics
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from pydantic import ValidationError

from app.config import JUDGE_SAMPLES
from app.llm import client, prompts
from app.models import CandidateProfile, JobProfile, ScoreJudgment

LLMFn = Callable[[str, str], str]

WEIGHTS = {"skills": 0.50, "experience": 0.20, "projects_education": 0.15, "fit": 0.15}
SHORTLIST_AT, CONSIDER_AT = 70, 45
PREFERRED_WEIGHT = 0.5
CREDIT = {"solid": 1.0, "working": 0.75, "inferred": 0.75, "basic": 0.5, "missing": 0.0}
COLOUR = {"solid": "green", "working": "yellow", "inferred": "yellow", "basic": "orange", "missing": "red"}

# A resume rarely lists every umbrella term ("Machine Learning") even when it lists the tools that imply it.
# Owning any listed tool earns partial credit ("inferred", yellow) for the umbrella skill.
IMPLIED_BY = {
    "machine learning": {"deep learning", "tensorflow", "pytorch", "scikit-learn", "keras", "xgboost", "lstm", "cnn", "yolo", "predictive modeling"},
    "deep learning": {"tensorflow", "pytorch", "keras", "lstm", "cnn", "yolo", "resnet"},
    "computer vision": {"opencv", "yolo", "cnn", "resnet"},
    "large language models": {"gemini api", "openai api", "langchain", "gemini", "openrouter", "llm api integration", "llms"},
    "vector databases": {"pinecone", "chroma", "chromadb", "faiss", "pgvector", "milvus", "vector embeddings"},
    "python": {"pandas", "numpy", "scikit-learn", "tensorflow", "pytorch", "flask", "django", "fastapi"},
    "rest apis": {"fastapi", "flask", "django", "rest api design & integration", "rest api testing"},
    "sql": {"mysql", "postgresql", "sqlite", "oracle"},
}
DEFAULT_INTERNSHIP_MONTHS = 3


@dataclass
class SkillResult:
    skill: str
    importance: str          # required | preferred
    status: str              # solid | working | basic | missing
    colour: str


@dataclass
class ScoreResult:
    match_score: float
    recommendation: str
    skill_match_ratio: float
    components: dict
    skill_breakdown: list[SkillResult]
    matching_skills: list[str]
    missing_skills: list[str]
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    summary: str = ""
    interview_questions: list[str] = field(default_factory=list)
    llm_status: str = "ok"           # ok | unavailable
    llm_error: str = ""


# ---------- skills (code) ----------
def _canon(skill: str, aliases: dict[str, str]) -> str:
    return aliases.get(skill.strip().lower(), skill.strip()).lower()


def _level_status(level: str | None) -> str:
    lv = (level or "").strip().lower()
    if lv in ("basic", "beginner", "familiar"):
        return "basic"
    if lv in ("working", "intermediate", "working knowledge"):
        return "working"
    return "solid"


def _implied_tools(canon_skill: str, aliases: dict[str, str]) -> set[str]:
    """Tools implying this skill. Keys are canonicalized so 'large language models' and 'LLMs' share one entry."""
    out: set[str] = set()
    for umbrella, tools in IMPLIED_BY.items():
        if _canon(umbrella, aliases) == canon_skill:
            out |= {_canon(t, aliases) for t in tools}
    return out


def skill_match(profile: CandidateProfile, job: JobProfile, aliases: dict[str, str]):
    """Return (skills_score 0-100, required_match_ratio, breakdown)."""
    have = {_canon(s, aliases) for s in profile.skills}
    levels = {_canon(k, aliases): v for k, v in profile.skill_levels.items()}
    rows: list[SkillResult] = []
    for importance, skills in (("required", job.required_skills), ("preferred", job.preferred_skills)):
        for s in skills:
            c = _canon(s, aliases)
            if c in have:
                status = _level_status(levels.get(c))
            elif have & _implied_tools(c, aliases):
                status = "inferred"
            else:
                status = "missing"
            rows.append(SkillResult(s, importance, status, COLOUR[status]))

    total = sum(1.0 if r.importance == "required" else PREFERRED_WEIGHT for r in rows)
    earned = sum(CREDIT[r.status] * (1.0 if r.importance == "required" else PREFERRED_WEIGHT) for r in rows)
    score = 100 * earned / total if total else 0.0
    required = [r for r in rows if r.importance == "required"]
    ratio = sum(r.status != "missing" for r in required) / len(required) if required else 0.0
    return score, ratio, rows


# ---------- experience (code) ----------
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_DATE = r"(?:([A-Za-z]{3})[A-Za-z]*\.?\s+)?(\d{4})"
_RANGE = re.compile(rf"{_DATE}\s*(?:-|–|—|to)\s*(?:{_DATE}|(present|current|now))", re.I)


def duration_months(text: str | None, today: date | None = None) -> int:
    """Parse 'Oct 2025 – May 2026', '2022 - 2024' or 'Jan 2026 - Present' into months; default if unparseable."""
    today = today or date.today()
    m = _RANGE.search(text or "")
    if not m:
        return DEFAULT_INTERNSHIP_MONTHS
    m1, y1, m2, y2, now = m.groups()
    start = int(y1) * 12 + (_MONTHS.get((m1 or "jan")[:3].lower(), 1) - 1)
    if now:
        end = today.year * 12 + today.month - 1
    elif not m1 and not m2:
        end = int(y2) * 12                       # year-only range "2022 - 2024": whole years, no month padding
    else:
        end = int(y2) * 12 + (_MONTHS.get((m2 or "dec")[:3].lower(), 12) - 1)
    months = end - start + (1 if (m1 or m2 or now) else 0)
    return months if 0 < months <= 120 else DEFAULT_INTERNSHIP_MONTHS


def experience_score(profile: CandidateProfile, job: JobProfile, today: date | None = None):
    intern_years = sum(duration_months(i.duration, today) for i in profile.internships) / 12
    effective = (profile.experience_years or 0) + 0.5 * intern_years
    minimum = job.min_experience_years or 0
    score = min(100.0, effective / minimum * 100) if minimum > 0 else min(100.0, 60 + 20 * effective)
    return score, effective


# ---------- recommendation ----------
def recommend(score: float) -> str:
    return "Shortlist" if score >= SHORTLIST_AT else "Consider" if score >= CONSIDER_AT else "Reject"


# ---------- LLM judgment ----------
def _default_llm(system: str, user: str) -> str:
    return client.complete_with_fallback(system, user, [("groq", client.GROQ_MODEL), ("gemini", None)])


def _candidate_brief(p: CandidateProfile) -> str:
    return json.dumps({
        "experience_years": p.experience_years,
        "experience": [j.model_dump(exclude_none=True) for j in p.experience],
        "internships": [j.model_dump(exclude_none=True) for j in p.internships],
        "education": [e.model_dump(exclude_none=True) for e in p.education],
        "projects": p.projects[:8], "certifications": p.certifications[:6],
        "soft_skills": p.soft_skills, "skills": p.skills,
    }, indent=1)


def judge(profile: CandidateProfile, job: JobProfile, facts: str, llm: LLMFn) -> ScoreJudgment:
    """Ask the LLM, retrying once on invalid output. Raises LLMError/ValueError if it cannot."""
    job_txt = json.dumps({"title": job.title, "summary": job.summary, "min_experience_years": job.min_experience_years,
                          "soft_skills": job.soft_skills})
    user = prompts.SCORE_USER.format(job=job_txt, candidate=_candidate_brief(profile), facts=facts)
    error = ""
    for attempt in range(2):
        try:
            raw = llm(prompts.SCORE_SYSTEM, user if attempt == 0 else user + prompts.RETRY_SUFFIX.format(error=error))
            return ScoreJudgment.model_validate(json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())))
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            error = str(exc)[:200]
    raise ValueError(error)


def judge_consensus(profile: CandidateProfile, job: JobProfile, facts: str, llm: LLMFn, samples: int = JUDGE_SAMPLES) -> ScoreJudgment:
    """Ask the LLM several times in parallel and keep the median, rounded to the nearest 10.

    A single LLM judgment varies from run to run even at temperature 0 (measured: up to ~7 points on the final score).
    The median of 3, rounded, roughly halves that noise; the calls run in parallel so latency is unchanged.
    Text (strengths, summary, questions) comes from the judgment closest to the consensus.
    """
    def one(_):
        try:
            return judge(profile, job, facts, llm)
        except (client.LLMError, ValueError):
            return None

    with ThreadPoolExecutor(max_workers=max(1, samples)) as pool:
        results = [r for r in pool.map(one, range(max(1, samples))) if r is not None]
    if not results:
        raise ValueError("no valid AI judgment was returned")
    consensus = lambda xs: float(round(statistics.median(xs) / 10) * 10)   # noqa: E731
    fit = consensus([r.fit_score for r in results])
    pe = consensus([r.projects_education_score for r in results])
    closest = min(results, key=lambda r: abs(r.fit_score - fit) + abs(r.projects_education_score - pe))
    return closest.model_copy(update={"fit_score": fit, "projects_education_score": pe})


def score_candidate(profile: CandidateProfile, job: JobProfile, aliases: dict[str, str] | None = None,
                    llm: LLMFn = _default_llm, today: date | None = None) -> ScoreResult:
    aliases = aliases or {}
    skills_score, ratio, rows = skill_match(profile, job, aliases)
    exp_score, eff_years = experience_score(profile, job, today)
    matching = [r.skill for r in rows if r.status != "missing"]
    missing = [r.skill for r in rows if r.status == "missing"]
    req = [r for r in rows if r.importance == "required"]

    facts = (f"Required skills matched: {sum(r.status != 'missing' for r in req)} of {len(req)}.\n"
             f"Matched (with level): {[f'{r.skill} ({r.status})' for r in rows if r.status != 'missing']}\n"
             f"Missing: {missing}\n"
             f"Employment years: {profile.experience_years}; effective years incl. internships: {eff_years:.1f}; "
             f"job minimum: {job.min_experience_years}")
    try:
        j = judge_consensus(profile, job, facts, llm)
        pe, fit, llm_status, err = j.projects_education_score, j.fit_score, "ok", ""
    except (client.LLMError, ValueError) as exc:
        # LLM unavailable: fall back to the code-computed skills score so a score still exists, and say so.
        j, pe, fit, llm_status, err = ScoreJudgment(fit_score=0, projects_education_score=0), skills_score, skills_score, "unavailable", str(exc)[:200]

    parts = {"skills": skills_score, "experience": exp_score, "projects_education": pe, "fit": fit}
    final = round(sum(WEIGHTS[k] * v for k, v in parts.items()), 1)
    return ScoreResult(
        match_score=final, recommendation=recommend(final), skill_match_ratio=round(ratio, 3),
        components={k: round(v, 1) for k, v in parts.items()}, skill_breakdown=rows,
        matching_skills=matching, missing_skills=missing, strengths=j.strengths, weaknesses=j.weaknesses,
        summary=j.summary, interview_questions=j.interview_questions, llm_status=llm_status, llm_error=err)
