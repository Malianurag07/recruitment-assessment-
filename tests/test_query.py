import json

import pytest

from app.llm import query_engine
from app.llm.client import LLMError
from app.llm.query_tools import Ctx, TOOLS, call_tool, load_candidates, resolve_names
from app.services.candidate_service import get_job
from app.services.skill_normalizer import load_aliases


@pytest.fixture
def ctx(pool):
    conn, job_id = pool
    return Ctx(conn, job_id, load_aliases(conn), get_job(conn, job_id))


def names(result):
    return [c["name"] for c in result["candidates"]]


# ---------------------------------------------------------------- tools (the assessment's questions, as SQL)
def test_pool_is_built_and_ranked(ctx):
    cands = load_candidates(ctx)
    assert len(cands) == 5 and cands[0]["name"] == "Jeevan Raj"          # most required skills


def test_q1_top_n(ctx):
    r = call_tool(ctx, "top_candidates", {"n": 3})
    assert names(r) == ["Jeevan Raj", "Priya Sharma", "Rahul Verma"] and r["count"] == 3
    assert [c["rank"] for c in r["candidates"]] == [1, 2, 3]


def test_q2_best_candidate_is_top_one(ctx):
    assert names(call_tool(ctx, "top_candidates", {"n": 1})) == ["Jeevan Raj"]


def test_top_by_recommendation_filter(ctx):
    r = call_tool(ctx, "top_candidates", {"n": 10, "recommendation": "reject"})
    assert all(c["recommendation"] == "Reject" for c in r["candidates"])


def test_q3_who_knows_python(ctx):
    r = call_tool(ctx, "find_by_skill", {"skills": ["Python"]})
    assert set(names(r)) == {"Jeevan Raj", "Priya Sharma", "Rahul Verma", "Ananya Iyer"}


def test_q4_machine_learning_includes_inferred_and_says_so(ctx):
    r = call_tool(ctx, "find_by_skill", {"skills": ["Machine Learning"]})
    by = {c["name"]: c["skills"]["Machine Learning"] for c in r["candidates"]}
    assert by["Jeevan Raj"]["how"] == "direct"
    assert by["Priya Sharma"] == {"how": "inferred", "from": ["TensorFlow"]}


def test_alias_in_question_resolves(ctx):
    assert "Jeevan Raj" in names(call_tool(ctx, "find_by_skill", {"skills": ["ML"]}))


def test_q5_missing_docker(ctx):
    r = call_tool(ctx, "find_missing_skill", {"skills": ["Docker"]})
    assert "Jeevan Raj" not in names(r) and len(r["candidates"]) == 4
    assert all(c["missing"] == ["Docker"] for c in r["candidates"])


def test_q8_more_than_2_years_is_strict(ctx):
    assert names(call_tool(ctx, "find_by_experience", {"min_years": 2})) == ["Priya Sharma"]   # Rahul has exactly 2


def test_experience_inclusive_and_max(ctx):
    assert set(names(call_tool(ctx, "find_by_experience", {"min_years": 2, "inclusive": True}))) == {"Priya Sharma", "Rahul Verma"}
    assert set(names(call_tool(ctx, "find_by_experience", {"max_years": 1}))) == {"Jeevan Raj", "Ananya R K", "Ananya Iyer"}


def test_experience_lists_internships_separately(ctx):
    r = call_tool(ctx, "find_by_experience", {"max_years": 1})
    jeevan = next(c for c in r["candidates"] if c["name"] == "Jeevan Raj")
    assert jeevan["experience_years"] == 0 and "AI Intern at Kinetrix" in jeevan["internship_list"][0]


def test_q9_fastapi(ctx):
    assert set(names(call_tool(ctx, "find_by_skill", {"skills": ["FastAPI"]}))) == {"Jeevan Raj", "Priya Sharma"}


def test_multi_skill_all_vs_any(ctx):
    assert names(call_tool(ctx, "find_by_skill", {"skills": ["Python", "Docker"]})) == ["Jeevan Raj"]
    assert len(call_tool(ctx, "find_by_skill", {"skills": ["Docker", "Selenium"], "match": "any"})["candidates"]) == 2


def test_q6_compare(ctx):
    r = call_tool(ctx, "compare_candidates", {"names": ["Jeevan", "Priya"]})
    assert [c["name"] for c in r["candidates"]] == ["Jeevan Raj", "Priya Sharma"]
    assert "Docker" in r["only_in_first"] and "Docker" not in r["only_in_second"]


def test_q7_explain_ranking(ctx):
    r = call_tool(ctx, "explain_ranking", {"first": "Jeevan", "second": "Priya"})
    assert r["first_is_ranked_higher"] is True and r["score_difference"] > 0
    assert "Docker" in r["skills_only_first_has"] and set(r["weighted_points_by_component"]) == {"skills", "experience", "projects_education", "fit"}


def test_explain_ranking_when_user_has_it_backwards(ctx):
    r = call_tool(ctx, "explain_ranking", {"first": "Ananya R K", "second": "Jeevan"})
    assert r["first_is_ranked_higher"] is False           # the answer must be able to correct the user


def test_q10_recommend_for_interview(ctx):
    r = call_tool(ctx, "recommend_for_interview", {"n": 1})
    assert r["candidates"][0]["name"] == "Jeevan Raj" and r["candidates"][0]["interview_questions"]


def test_candidate_profile_and_badges(ctx):
    r = call_tool(ctx, "candidate_profile", {"name": "Priya"})
    assert r["skill_badges"]["Docker"] == "red" and r["skill_badges"]["Python"] == "green"


def test_search_resume_text(ctx):
    r = call_tool(ctx, "search_resume_text", {"keyword": "aws certified"})
    assert names({"candidates": r["matches"]}) == ["Jeevan Raj"] and "Cloud Practitioner" in r["matches"][0]["snippet"]
    assert call_tool(ctx, "search_resume_text", {"keyword": "x"})["error"]


def test_job_summary(ctx):
    r = call_tool(ctx, "job_summary", {})
    assert r["scored_candidates"] == 5 and sum(r["by_recommendation"].values()) == 5 and r["required_skills"]


# ---------------------------------------------------------------- names, safety, edge cases
def test_ambiguous_name_is_reported_not_guessed(ctx):
    found, problems = resolve_names(load_candidates(ctx), ["Ananya"])
    assert not found and problems[0]["status"] == "ambiguous" and len(problems[0]["options"]) == 2


def test_specific_names_and_typos_resolve(ctx):
    cands = load_candidates(ctx)
    assert resolve_names(cands, ["Ananya Iyer"])[0][0]["name"] == "Ananya Iyer"
    assert resolve_names(cands, ["priyaa"])[0][0]["name"] == "Priya Sharma"


def test_placeholder_names_are_not_matched_to_random_people(ctx):
    r = call_tool(ctx, "compare_candidates", {"names": ["Candidate A", "Candidate B"]})
    assert "error" in r and {p["status"] for p in r["problems"]} == {"not_found"} and r["problems"][0]["available"]


def test_unknown_skill_returns_zero_results_cleanly(ctx):
    r = call_tool(ctx, "find_by_skill", {"skills": ["Kubernetes"]})
    assert r["count"] == 0 and r["candidates"] == []


def test_sql_injection_in_arguments_is_harmless(ctx):
    evil = "'; DROP TABLE candidates; --"
    call_tool(ctx, "find_by_skill", {"skills": [evil]})
    call_tool(ctx, "search_resume_text", {"keyword": evil})
    call_tool(ctx, "candidate_profile", {"name": evil})
    assert ctx.conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 5


def test_like_wildcards_are_escaped(ctx):
    assert call_tool(ctx, "search_resume_text", {"keyword": "%%"})["count"] == 0


def test_unknown_tool_and_bad_args(ctx):
    assert "error" in call_tool(ctx, "delete_everything", {})
    assert "error" in call_tool(ctx, "top_candidates", {"n": "many"})
    assert call_tool(ctx, "top_candidates", {"n": 1, "evil_param": "x"})["count"] == 1     # unknown args ignored


def test_empty_job_gives_empty_results(pool):
    conn, _ = pool
    conn.execute("INSERT INTO job_descriptions (title) VALUES ('Empty')")
    ctx = Ctx(conn, 2, load_aliases(conn), get_job(conn, 2))
    assert call_tool(ctx, "top_candidates", {})["candidates"] == []
    assert call_tool(ctx, "job_summary", {})["average_score"] is None


def test_registry_documents_every_tool():
    assert all(t.description and callable(t.fn) for t in TOOLS.values()) and len(TOOLS) == 12


# ---------------------------------------------------------------- engine (fake planner + fake answerer)
def plan(*calls, direct=None):
    return lambda s, u: json.dumps({"calls": [{"tool": t, "args": a} for t, a in calls], "direct_reply": direct})


def echo_answer(system, user):
    return "ANSWER BASED ON: " + user


def test_engine_runs_planned_tools_and_grounds_the_answer(pool):
    conn, job_id = pool
    a = query_engine.ask(conn, job_id, "Which candidates are missing Docker?",
                         plan_llm=plan(("find_missing_skill", {"skills": ["Docker"]})), answer_llm=echo_answer)
    assert a.calls == [{"tool": "find_missing_skill", "args": {"skills": ["Docker"]}}]
    assert "Priya Sharma" in a.text and "Jeevan Raj" not in a.text.split("TOOL RESULTS")[1]      # answer sees real results only


def test_engine_multi_tool_plan(pool):
    conn, job_id = pool
    a = query_engine.ask(conn, job_id, "top 2 and who knows Docker?", remember=False,
                         plan_llm=plan(("top_candidates", {"n": 2}), ("find_by_skill", {"skills": ["Docker"]})), answer_llm=echo_answer)
    assert len(a.results) == 2 and a.results[0]["count"] == 2


def test_engine_direct_reply_for_offtopic(pool):
    conn, job_id = pool
    a = query_engine.ask(conn, job_id, "what's the weather", plan_llm=plan(direct="I can only help with these candidates."),
                         answer_llm=lambda s, u: pytest.fail("answer LLM should not run"))
    assert a.text.startswith("I can only help") and a.calls == []


def test_engine_drops_invented_tools(pool):
    conn, job_id = pool
    a = query_engine.ask(conn, job_id, "q", plan_llm=plan(("hack_database", {})), answer_llm=echo_answer)
    assert "Unknown tool" in a.text


def test_engine_caps_tool_calls(pool):
    conn, job_id = pool
    a = query_engine.ask(conn, job_id, "q", plan_llm=plan(*[("job_summary", {})] * 9), answer_llm=echo_answer, remember=False)
    assert len(a.calls) == query_engine.MAX_CALLS


def test_engine_survives_garbage_plan_and_retries_once(pool):
    conn, job_id = pool
    seen = []

    def flaky(s, u):
        seen.append(u)
        return "not json" if len(seen) == 1 else json.dumps({"calls": [{"tool": "job_summary", "args": {}}]})
    a = query_engine.ask(conn, job_id, "overview?", plan_llm=flaky, answer_llm=echo_answer)
    assert len(seen) == 2 and a.calls[0]["tool"] == "job_summary"
    b = query_engine.ask(conn, job_id, "overview?", plan_llm=lambda s, u: "garbage", answer_llm=echo_answer)
    assert "rephrase" in b.text and b.error


def test_engine_reports_llm_outages_politely(pool):
    conn, job_id = pool

    def down(s, u):
        raise LLMError("429")
    assert "unavailable" in query_engine.ask(conn, job_id, "q", plan_llm=down).text
    a = query_engine.ask(conn, job_id, "q", plan_llm=plan(("job_summary", {})), answer_llm=down)
    assert "could not phrase" in a.text and a.results          # data was still retrieved


def test_engine_remembers_conversation_for_follow_ups(pool):
    conn, job_id = pool
    prompts = []

    def spy(s, u):
        prompts.append(u)
        return json.dumps({"calls": [{"tool": "top_candidates", "args": {"n": 1}}]})
    query_engine.ask(conn, job_id, "Who is best?", plan_llm=spy, answer_llm=lambda s, u: "Jeevan Raj is best.")
    query_engine.ask(conn, job_id, "and why?", plan_llm=spy, answer_llm=lambda s, u: "because")
    assert "Jeevan Raj is best." in prompts[1] and "Who is best?" in prompts[1]
    assert conn.execute("SELECT COUNT(*) FROM chat_history").fetchone()[0] == 4


def test_engine_edge_inputs(pool):
    conn, job_id = pool
    assert "type a question" in query_engine.ask(conn, job_id, "   ").text
    assert "does not exist" in query_engine.ask(conn, 999, "hi").text


def test_full_name_with_initials_beats_shared_first_name(ctx):
    found, problems = resolve_names(load_candidates(ctx), ["Ananya R K"])
    assert not problems and found[0]["name"] == "Ananya R K"


def test_internships_come_from_structured_data_not_keywords(ctx):
    r = call_tool(ctx, "find_with_internships", {})
    assert names(r) == ["Jeevan Raj"] and "Kinetrix" in r["candidates"][0]["internship_list"][0]
    assert call_tool(ctx, "find_with_internships", {"min_count": 2})["count"] == 0


# ---------------------------------------------------------------- planner must not silently disambiguate
def test_planner_expanding_an_ambiguous_name_is_undone(pool):
    conn, job_id = pool
    sneaky = lambda s, u: json.dumps({"calls": [{"tool": "compare_candidates", "args": {"names": ["Ananya Iyer", "Priya Sharma"]}}]})  # noqa: E731
    a = query_engine.ask(conn, job_id, "Compare Ananya and Priya", plan_llm=sneaky, answer_llm=lambda s, u: "x", remember=False)
    assert a.calls[0]["args"]["names"] == ["ananya", "Priya Sharma"]
    assert a.results[0]["problems"][0]["status"] == "ambiguous"


def test_names_the_user_really_typed_are_left_alone(pool):
    conn, job_id = pool
    plan_ok = lambda s, u: json.dumps({"calls": [{"tool": "compare_candidates", "args": {"names": ["Ananya Iyer", "Priya Sharma"]}}]})  # noqa: E731
    a = query_engine.ask(conn, job_id, "Compare Ananya Iyer and Priya Sharma", plan_llm=plan_ok, answer_llm=lambda s, u: "x", remember=False)
    assert a.calls[0]["args"]["names"] == ["Ananya Iyer", "Priya Sharma"] and "error" not in a.results[0]
    # a follow-up that names nobody in the question keeps the planner's resolution from the conversation
    a = query_engine.ask(conn, job_id, "compare them", plan_llm=plan_ok, answer_llm=lambda s, u: "x", remember=False)
    assert a.calls[0]["args"]["names"] == ["Ananya Iyer", "Priya Sharma"]
