import json

from app.llm.extraction import extract_profile
from app.services.skill_normalizer import normalize_skills

GOOD = json.dumps({"name": "Priya", "email": "Priya@Example.com", "skills": ["Python", "python", "ML"],
                   "education": None, "experience_years": 4})


def test_valid_json_ok_and_cleaned():
    r = extract_profile("text", llm=lambda s, u: GOOD)
    assert r.status == "ok"
    assert r.profile.email == "priya@example.com"     # lowercased
    assert r.profile.skills == ["Python", "ML"]        # deduped
    assert r.profile.education == []                   # null -> []


def test_retry_recovers_from_bad_json():
    calls = []

    def flaky(s, u):
        calls.append(u)
        return "not json" if len(calls) == 1 else GOOD

    r = extract_profile("text", llm=flaky)
    assert r.status == "ok" and len(calls) == 2
    assert "rejected" in calls[1]                      # error fed back to the model


def test_two_failures_flag_for_review():
    r = extract_profile("text", llm=lambda s, u: '{"email": "bad-email"}')
    assert r.status == "needs_review" and r.profile is None


def test_code_fences_stripped():
    r = extract_profile("text", llm=lambda s, u: "```json\n" + GOOD + "\n```")
    assert r.status == "ok"


def test_alias_normalization():
    out = normalize_skills(["ML", "Machine Learning", "FastAPI"], {"ml": "Machine Learning"})
    assert out == [("Machine Learning", "ML"), ("FastAPI", "FastAPI")]


def test_no_contact_details_flagged_but_profile_kept():
    r = extract_profile("text", llm=lambda s, u: json.dumps({"name": "X", "skills": ["Python"]}))
    assert r.status == "needs_review" and r.profile is not None and "email or phone" in r.error


def test_null_skill_levels_dropped():
    r = extract_profile("t", llm=lambda s, u: json.dumps({"email": "a@b.co", "skill_levels": {"SQL": None, "Python": "basic"}}))
    assert r.status == "ok" and r.profile.skill_levels == {"Python": "basic"}


def test_soft_skills_field_cleaned():
    r = extract_profile("t", llm=lambda s, u: json.dumps(
        {"email": "a@b.co", "skills": ["Python"], "soft_skills": ["Teamwork", "teamwork", None, " Leadership "]}))
    assert r.profile.soft_skills == ["Teamwork", "Leadership"] and r.profile.skills == ["Python"]
