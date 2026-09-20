import json

import pytest
from fastapi.testclient import TestClient

from app import deps
from app.main import app
from conftest import _judge, make_resume_pdf


@pytest.fixture
def client(pool):
    conn, job_id = pool
    app.dependency_overrides[deps.get_db] = lambda: conn
    deps.JOB_LLMS.update(jd_llm=_judge, canon_llm=_judge)
    deps.CHAT_LLMS.update(plan_llm=lambda s, u: json.dumps({"calls": [{"tool": "top_candidates", "args": {"n": 2}}]}),
                          answer_llm=lambda s, u: "Top two shown.")
    with TestClient(app) as c:
        c.job_id = job_id
        yield c
    app.dependency_overrides.clear()
    for d in (deps.JOB_LLMS, deps.CHAT_LLMS, deps.PIPELINE_LLMS):
        d.clear()


def test_health_and_docs(client):
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.get("/openapi.json").status_code == 200


def test_list_and_detail(client):
    jobs = client.get("/api/jobs").json()
    assert jobs[0]["id"] == client.job_id and jobs[0]["scored"] == 5
    d = client.get(f"/api/jobs/{client.job_id}").json()
    assert d["scored"] == 5 and sum(d["by_recommendation"].values()) == 5 and d["required_skills"]
    assert client.get("/api/jobs/999").status_code == 404


def test_create_job_from_text_and_file(client):
    text = "AI engineer role requiring Python and Docker and SQL. Freshers welcome, apply now please. " * 2
    r = client.post("/api/jobs", data={"text": text})
    assert r.status_code == 200 and r.json()["title"] == "AI Engineer"
    r = client.post("/api/jobs", files={"file": ("jd.txt", text.encode(), "text/plain")})
    assert r.status_code == 200
    assert len(client.get("/api/jobs").json()) == 3


def test_create_job_rejects_bad_input(client):
    assert client.post("/api/jobs", data={"text": ""}).status_code == 422
    assert client.post("/api/jobs", files={"file": ("jd.exe", b"x", "application/octet-stream")}).status_code == 422
    assert client.post("/api/jobs", files={"file": ("bad.pdf", b"not a pdf", "application/pdf")}).status_code == 422
    assert client.post("/api/jobs", data={"text": "too short"}).status_code == 422


def test_candidate_cards_have_everything_the_ui_needs(client):
    cards = client.get(f"/api/jobs/{client.job_id}/candidates").json()
    top = cards[0]
    assert top["rank"] == 1 and top["name"] == "Jeevan Raj" and top["recommendation"] == "Shortlist"
    assert {"skills", "components", "strengths", "weaknesses", "interview_questions", "verification_log"} <= set(top)
    assert {s["colour"] for s in top["skills"]} <= {"green", "yellow", "orange", "red"}
    assert top["required_matched"] == 3 and top["required_total"] == 3
    assert {"AWS", "Machine Learning"} <= {s["skill"] for s in top["all_skills"]}          # every extracted skill, not just the job's
    assert top["education"] == [] and top["phone"]


def test_upload_multiple_resumes_isolates_failures(client):
    profile = {"name": "New Person", "email": "new@x.com", "phone": "9111111111", "skills": ["Python", "SQL"], "experience_years": 0}
    deps.PIPELINE_LLMS.update(extract_llm=lambda s, u: json.dumps(profile), verify_llm=_judge, canon_llm=_judge, score_llm=_judge)
    good = make_resume_pdf("New Person", "new@x.com", "9111111111", ["Python", "SQL"])
    r = client.post(f"/api/jobs/{client.job_id}/resumes",
                    files=[("files", ("good.pdf", good, "application/pdf")), ("files", ("bad.pdf", b"junk", "application/pdf")),
                           ("files", ("notes.txt", b"hello", "text/plain"))])
    statuses = [o["status"] for o in r.json()["outcomes"]]
    assert statuses == ["stored", "rejected_file", "rejected_file"]
    stored = r.json()["outcomes"][0]
    assert stored["name"] == "New Person" and stored["email"] == "new@x.com" and stored["skills_found"] == 2   # extraction shown live
    assert len(client.get(f"/api/jobs/{client.job_id}/candidates").json()) == 6
    assert client.post("/api/jobs/999/resumes", files=[("files", ("a.pdf", good, "application/pdf"))]).status_code == 404


def test_conflict_flow_over_http(client):
    deps.PIPELINE_LLMS.update(verify_llm=_judge, canon_llm=_judge, score_llm=_judge,
                              extract_llm=lambda s, u: json.dumps({"name": "Priya Sharma", "email": "priya@x.com", "phone": "9000000002",
                                                                     "skills": ["Python", "Docker", "SQL"], "experience_years": 4}))
    pdf = make_resume_pdf("Priya Sharma", "priya@x.com", "9000000002", ["Python", "Docker", "SQL"], extra="A different version.")
    out = client.post(f"/api/jobs/{client.job_id}/resumes", files=[("files", ("priya_v2.pdf", pdf, "application/pdf"))]).json()["outcomes"][0]
    assert out["status"] == "conflict_pending"
    items = client.get(f"/api/jobs/{client.job_id}/conflicts?preview=true").json()
    assert items[0]["new_file"] == "priya_v2.pdf" and items[0]["preview"]["required_matched"] == "3/3"
    r = client.post("/api/conflicts/resolve", json={"keep_application_id": items[0]["pending_id"]}).json()
    assert r["status"] == "stored" and client.get(f"/api/jobs/{client.job_id}/conflicts").json() == []
    assert client.post("/api/conflicts/resolve", json={"keep_application_id": 99999}).status_code == 404


def test_chat_roundtrip_and_history(client):
    r = client.post(f"/api/jobs/{client.job_id}/chat", json={"question": "top 2?"}).json()
    assert r["text"] == "Top two shown." and r["calls"][0]["tool"] == "top_candidates"
    assert [m["role"] for m in client.get(f"/api/jobs/{client.job_id}/chat").json()] == ["user", "assistant"]
    assert client.delete(f"/api/jobs/{client.job_id}/chat").json() == {"cleared": True}
    assert client.get(f"/api/jobs/{client.job_id}/chat").json() == []
    assert client.post(f"/api/jobs/{client.job_id}/chat", json={"question": ""}).status_code == 422
    assert client.post("/api/jobs/999/chat", json={"question": "hi"}).status_code == 404


def test_csv_export(client):
    r = client.get(f"/api/jobs/{client.job_id}/export.csv")
    lines = r.text.strip().splitlines()
    assert r.headers["content-type"].startswith("text/csv") and "attachment" in r.headers["content-disposition"]
    assert lines[0].startswith("rank,name,email") and len(lines) == 6 and "Jeevan Raj" in lines[1]


def test_rescore_and_review_endpoints(client):
    assert client.get(f"/api/jobs/{client.job_id}/review").json() == []
    assert client.post(f"/api/jobs/{client.job_id}/rescore").json() == {"results": [], "reindexed_chunks": 0}
    assert client.post("/api/jobs/999/rescore").status_code == 404


def test_db_viewer_is_whitelisted(client):
    assert client.get("/api/db").json()["candidates"] == 5
    t = client.get("/api/db/candidates?limit=2").json()
    assert t["total"] == 5 and len(t["rows"]) == 2 and "email" in t["columns"]
    assert client.get("/api/db/sqlite_master").status_code == 404
    assert client.get("/api/db/candidates;DROP TABLE candidates").status_code == 404
    assert client.get("/api/db/candidates?limit=99999").status_code == 422
