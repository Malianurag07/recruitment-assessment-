"""Jobs and resume intake: create a job from text/file, upload resumes, resolve duplicate-resume choices."""
import sqlite3
from dataclasses import asdict

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app import deps
from app.routes.access import can_access_job, owned_job
from app.routes.auth import current_user
from app.parsing.document_extractor import extract_document
from app.services import candidate_service as svc
from app.services import read_models

router = APIRouter(prefix="/api", tags=["upload"])


@router.get("/jobs")
def list_jobs(user: dict = Depends(current_user), db: sqlite3.Connection = Depends(deps.get_db)):
    admin = user["role"] == "admin"
    return read_models.list_jobs(db, None if admin else user["id"], include_owner=admin and user["id"] != 0)


@router.post("/jobs")
async def create_job(text: str = Form(""), file: UploadFile | None = File(None), user: dict = Depends(current_user),
                     db: sqlite3.Connection = Depends(deps.get_db)):
    """Create a job from pasted text or an uploaded PDF/DOCX/TXT description."""
    body = text.strip()
    if file is not None and file.filename:
        data = await file.read()
        if file.filename.lower().endswith((".pdf", ".docx")):
            doc = extract_document(data, file.filename)
            if not doc.ok:
                raise HTTPException(422, doc.message)
            body = doc.text
        elif file.filename.lower().endswith(".txt"):
            body = data.decode("utf-8", errors="replace")
        else:
            raise HTTPException(422, "Job description must be PDF, DOCX or TXT.")
    if not body:
        raise HTTPException(422, "Provide the job description as text or a file.")
    job_id, msg = svc.create_job(db, body, owner_id=user["id"] or None, **deps.JOB_LLMS)
    if job_id is None:
        raise HTTPException(422, msg)
    return read_models.job_detail(db, job_id)


@router.get("/jobs/{job_id}")
def get_job(job_id: int = Depends(owned_job), db: sqlite3.Connection = Depends(deps.get_db)):
    detail = read_models.job_detail(db, job_id)
    if detail is None:
        raise HTTPException(404, "Job not found")
    return detail


@router.post("/jobs/{job_id}/resumes")
async def upload_resumes(job_id: int = Depends(owned_job), files: list[UploadFile] = File(...), db: sqlite3.Connection = Depends(deps.get_db)):
    """Process one or more resumes. Each file gets its own outcome, so one bad file never blocks the rest."""
    if svc.get_job(db, job_id) is None:
        raise HTTPException(404, "Job not found")
    outcomes = []
    for f in files:
        o = asdict(svc.process_resume(db, await f.read(), f.filename or "resume", job_id, **deps.PIPELINE_LLMS))
        outcomes.append(_with_extraction_summary(db, o))
    return {"outcomes": outcomes}


def _with_extraction_summary(db: sqlite3.Connection, outcome: dict) -> dict:
    """Add what was extracted (name, contact, skill count) so the UI can show it while the batch runs."""
    if outcome.get("application_id"):
        row = db.execute(
            """SELECT c.name, c.email, (SELECT COUNT(*) FROM application_skills s WHERE s.application_id = a.id) AS skills
               FROM applications a JOIN candidates c ON c.id = a.candidate_id WHERE a.id = ?""", (outcome["application_id"],)).fetchone()
        if row:
            outcome.update(name=row["name"], email=row["email"], skills_found=row["skills"])
    return outcome


@router.get("/jobs/{job_id}/conflicts")
def conflicts(job_id: int = Depends(owned_job), preview: bool = False, db: sqlite3.Connection = Depends(deps.get_db)):
    """Resumes waiting for the applicant's choice. preview=true also scores each pending one (uses the LLM)."""
    items = svc.pending_conflicts(db, job_id)
    if preview and items:
        scores = {p["application_id"]: p for p in svc.preview_pending_scores(db, job_id, **_score_kw())}
        for it in items:
            it["preview"] = scores.get(it["pending_id"])
    return items


class Resolve(BaseModel):
    keep_application_id: int


@router.post("/conflicts/resolve")
def resolve(body: Resolve, user: dict = Depends(current_user), db: sqlite3.Connection = Depends(deps.get_db)):
    row = db.execute("SELECT job_description_id FROM applications WHERE id=?", (body.keep_application_id,)).fetchone()
    if row is None or not can_access_job(db, user, row[0]):
        raise HTTPException(404, "Unknown application")
    outcome = svc.resolve_conflict(db, body.keep_application_id, **_score_kw())
    if outcome.status == "rejected_file":
        raise HTTPException(404, outcome.message)
    return asdict(outcome)


def _score_kw() -> dict:
    return {"score_llm": deps.PIPELINE_LLMS["score_llm"]} if "score_llm" in deps.PIPELINE_LLMS else {}
