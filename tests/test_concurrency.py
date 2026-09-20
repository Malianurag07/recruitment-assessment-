"""Two uploads for the same person and job at the same moment must not crash or double-activate."""
import json
import sqlite3
import threading
import time

from app.database import SCHEMA, SEED_ALIASES
from app.services import candidate_service as svc
from conftest import JD_TEXT, _judge, make_resume_pdf


def _file_db(path):
    conn = sqlite3.connect(path, check_same_thread=False, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def test_same_person_uploaded_concurrently_yields_one_active_and_one_pending(tmp_path):
    path = tmp_path / "race.db"
    setup = _file_db(path)
    setup.executescript(SCHEMA)
    setup.executemany("INSERT INTO skill_aliases (alias, canonical) VALUES (?, ?)", SEED_ALIASES.items())
    job_id, _ = svc.create_job(setup, JD_TEXT, canon_llm=_judge, jd_llm=_judge)
    setup.close()

    profile = {"name": "Race Person", "email": "race@x.com", "phone": "9222222222", "skills": ["Python", "SQL"], "experience_years": 0}
    barrier = threading.Barrier(2)

    def slow_score(system, user):          # keeps both workers inside scoring together, after each already saw "no active resume"
        barrier.wait(timeout=10)
        time.sleep(0.2)
        return _judge(system, user)

    def upload(extra):
        conn = _file_db(path)
        try:
            pdf = make_resume_pdf("Race Person", "race@x.com", "9222222222", ["Python", "SQL"], extra=extra)
            return svc.process_resume(conn, pdf, f"{extra}.pdf", job_id, extract_llm=lambda s, u: json.dumps(profile),
                                      verify_llm=_judge, canon_llm=_judge, score_llm=slow_score).status
        except Exception as exc:           # a crash here is exactly the bug this test guards against
            return f"CRASH {type(exc).__name__}: {exc}"
        finally:
            conn.close()

    results = []
    threads = [threading.Thread(target=lambda e=e: results.append(upload(e))) for e in ("version one", "version two")]
    [t.start() for t in threads]; [t.join(30) for t in threads]

    assert sorted(results) == ["conflict_pending", "stored"], results
    check = _file_db(path)
    states = [r[0] for r in check.execute("SELECT application_status FROM applications ORDER BY id")]
    assert sorted(states) == ["active", "pending_choice"]
    assert check.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 1          # one person, not two
