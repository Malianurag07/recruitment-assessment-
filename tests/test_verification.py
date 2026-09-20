import json

from app.llm.client import LLMError
from app.llm.verification import verify_profile
from app.models import CandidateProfile

TEXT = """Jane Doe jane@example.com +91 98765 43210
SKILLS: Python, SQL, RAG & vector databases, Adaptability
Worked at Acme 2020 - 2023. Internship at Beta 2019."""
ALIASES = {"sql": "SQL"}


def prof(**kw):
    base = dict(name="Jane Doe", email="jane@example.com", phone="+91 98765 43210",
                skills=["Python", "SQL", "RAG & vector databases"], experience_years=3)
    return CandidateProfile(**{**base, **kw})


def fake(corrections):
    return lambda s, u: json.dumps({"corrections": corrections})


def test_clean_profile_is_verified():
    r = verify_profile(prof(), TEXT, ALIASES, llm=fake([]))
    assert r.status == "verified" and not r.changes


def test_hallucinated_skill_removed_deterministically():
    r = verify_profile(prof(skills=["Python", "Kubernetes"]), TEXT, ALIASES, llm=fake([]))
    assert r.profile.skills == ["Python"] and r.status == "corrected"
    assert r.changes[0].source == "deterministic"


def test_soft_skill_moved_not_lost():
    r = verify_profile(prof(skills=["Python", "Adaptability"]), TEXT, ALIASES, llm=fake([]))
    assert r.profile.skills == ["Python"] and r.profile.soft_skills == ["Adaptability"]


def test_llm_split_with_valid_quote_applied():
    q = "RAG & vector databases"
    r = verify_profile(prof(), TEXT, ALIASES, llm=fake([
        {"action": "replace_skill", "field": "skills", "old": q, "new": "RAG", "evidence_quote": q},
        {"action": "add_skill", "field": "skills", "old": None, "new": "vector databases", "evidence_quote": q}]))
    assert r.profile.skills == ["Python", "SQL", "RAG", "vector databases"] and r.status == "corrected"


def test_llm_correction_with_fake_quote_rejected():
    r = verify_profile(prof(), TEXT, ALIASES, llm=fake([
        {"action": "set", "field": "experience_years", "old": "3", "new": 9, "evidence_quote": "9 years at Google"}]))
    assert r.profile.experience_years == 3 and len(r.rejected) == 1 and r.status == "verified"


def test_llm_cannot_add_skill_not_in_quote():
    r = verify_profile(prof(), TEXT, ALIASES, llm=fake([
        {"action": "add_skill", "field": "skills", "old": None, "new": "Docker", "evidence_quote": "Worked at Acme"}]))
    assert "Docker" not in r.profile.skills and r.rejected


def test_valid_experience_fix_applied():
    r = verify_profile(prof(experience_years=5), TEXT, ALIASES, llm=fake([
        {"action": "set", "field": "experience_years", "old": "5", "new": 3, "evidence_quote": "Acme 2020 - 2023"}]))
    assert r.profile.experience_years == 3 and r.status == "corrected"


def test_wrong_email_flags_review():
    r = verify_profile(prof(email="jane@other.com"), TEXT, ALIASES, llm=fake([]))
    assert r.status == "needs_review"


def test_llm_down_gives_partial():
    def boom(s, u):
        raise LLMError("rate limited")
    r = verify_profile(prof(), TEXT, ALIASES, llm=boom)
    assert r.status == "partial" and r.profile.skills


def test_garbage_llm_output_partial():
    r = verify_profile(prof(), TEXT, ALIASES, llm=lambda s, u: "not json")
    assert r.status == "partial"


# ---------- found by the QA suite: a verifier "correction" turned 3 real years into 1 ----------
def test_years_supported_by_quote():
    from datetime import date
    from app.llm.verification import years_supported_by
    d = date(2026, 9, 20)
    assert years_supported_by("ML Engineer, Novatech (2023 - 2026)", 3, d)
    assert not years_supported_by("ML Engineer, Novatech (2023 - 2026)", 1, d)        # the exact bad correction seen live
    assert years_supported_by("ML engineer with 3 years of experience", 3, d)
    assert years_supported_by("Engineer 2021 - present", 5, d) and not years_supported_by("Engineer 2021 - present", 1, d)
    assert years_supported_by("2019 - 2021 and 2021 - 2024", 5, d)                     # ranges add up
    assert not years_supported_by("Worked at Acme", 4, d) and not years_supported_by("2 years", "many", d)


def test_llm_cannot_overwrite_years_with_an_unsupported_number():
    text = "Priya Sharma priya@x.com 9000000000 ML Engineer, Novatech (2023 - 2026). " + "Built systems. " * 8
    prof = CandidateProfile(name="Priya Sharma", email="priya@x.com", phone="9000000000", skills=["Python"], experience_years=3)
    bad = json.dumps({"corrections": [{"action": "set", "field": "experience_years", "old": "3", "new": 1, "evidence_quote": "ML Engineer, Novatech (2023 - 2026)"}]})
    r = verify_profile(prof, text, {}, llm=lambda s, u: bad)
    assert r.profile.experience_years == 3 and r.rejected and "not supported" in r.rejected[0]["reason"]
    fine = json.dumps({"corrections": [{"action": "set", "field": "experience_years", "old": "3", "new": 2.5, "evidence_quote": "ML Engineer, Novatech (2023 - 2026)"}]})
    assert verify_profile(prof, text, {}, llm=lambda s, u: fine).profile.experience_years == 2.5     # within a year of the range: allowed
    wild = json.dumps({"corrections": [{"action": "set", "field": "experience_years", "old": "3", "new": 9, "evidence_quote": "ML Engineer, Novatech (2023 - 2026)"}]})
    assert verify_profile(prof, text, {}, llm=lambda s, u: wild).profile.experience_years == 3
