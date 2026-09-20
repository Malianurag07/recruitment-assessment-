"""Add a job description to the EXISTING database and match every sample resume against it.

Run from the project root:   python scripts/match_jd.py data/job_descriptions/ai_ml_intern.txt
Unlike demo_pipeline.py this never wipes the database, so several jobs can live side by side.
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.database import get_connection, init_db  # noqa: E402
from app.parsing.document_extractor import extract_document  # noqa: E402
from app.services import candidate_service as svc  # noqa: E402

jd_path = Path(sys.argv[1])
init_db()
conn = get_connection()

jd = extract_document(jd_path.read_bytes(), jd_path.name) if jd_path.suffix.lower() in (".pdf", ".docx") else None
jd_text = jd.text if jd else jd_path.read_text(encoding="utf-8")
job_id, msg = svc.create_job(conn, jd_text)
if not job_id:
    sys.exit(f"Could not read the job description: {msg}")
job = svc.get_job(conn, job_id)
print(f"JOB #{job_id}: {job.title}  (min experience: {job.min_experience_years})")
print(f"  required ({len(job.required_skills)}): {job.required_skills}")
print(f"  preferred ({len(job.preferred_skills)}): {job.preferred_skills}")
print(f"  soft skills (not scored as technical): {job.soft_skills}\n", flush=True)

for path in sorted((ROOT / "data" / "sample_resumes").iterdir()):
    t = time.time()
    o = svc.process_resume(conn, path.read_bytes(), path.name, job_id)
    score = f"{o.score:5.1f} {o.recommendation}" if o.score is not None else "-"
    print(f"{path.name:32} {o.status:17} {score:16} {time.time() - t:3.0f}s  {o.message[:55]}", flush=True)

print(f"\n=== RANKING for job #{job_id} ===")
for i, r in enumerate(svc.ranking(conn, job_id), 1):
    print(f"{i:2}. {r['match_score']:5.1f}  {r['recommendation']:9} {r['name']}  [{r['resume_filename']}]")

from app.llm.query_tools import Ctx, load_candidates  # noqa: E402
from app.services.skill_normalizer import load_aliases  # noqa: E402

ctx = Ctx(conn, job_id, load_aliases(conn), job)
print("\n=== WHY (skills matched, by colour) ===")
for c in load_candidates(ctx):
    by = {}
    for b in c["breakdown"]:
        by.setdefault(b["colour"], []).append(b["skill"])
    req = [b for b in c["breakdown"] if b["importance"] == "required"]
    got = sum(b["status"] != "missing" for b in req)
    print(f"\n{c['rank']}. {c['name']} - {c['score']} {c['recommendation']} | required {got}/{len(req)} | components {c['components']}")
    for colour in ("green", "yellow", "orange", "red"):
        if by.get(colour):
            shown = by[colour] if colour != "red" else by[colour][:12]
            print(f"   {colour:6}: {shown}")
    if c["strengths"]:
        print(f"   strengths : {c['strengths'][:2]}")
    if c["weaknesses"]:
        print(f"   weaknesses: {c['weaknesses'][:2]}")

print("\n=== PENDING (applicant must choose) ===")
for p in svc.pending_conflicts(conn, job_id):
    print(f"  {p['name']}: keep '{p['current_file']}' or switch to '{p['new_file']}'?")
