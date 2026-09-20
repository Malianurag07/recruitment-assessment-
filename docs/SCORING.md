# Candidate Scoring Logic

Every candidate is scored **per job description**. The score has four components. Two are computed
in plain code (repeatable, explainable), and two come from the LLM (judgment), which is given the code's
facts so it cannot contradict them.

| Component | Weight | Computed by | What it measures |
|---|---|---|---|
| Skills match | 50% | code | Coverage of the JD's required (and preferred) skills, weighted by proficiency |
| Experience | 20% | code | Employment years vs the JD minimum, with partial credit for internships |
| Projects and education | 15% | LLM | Relevance of projects, coursework, degree and certifications to the role |
| Overall fit | 15% | LLM | Holistic judgment, including soft skills the JD asks for |

`final_score = 0.50*skills + 0.20*experience + 0.15*projects_education + 0.15*fit`  (0 to 100)

## 1. Skills match (code)

Skills on both sides are normalized through the alias table first (`ML`, `Deep Learning` -> `Machine Learning`),
then compared case-insensitively. Each JD skill is `required` (weight 1.0) or `preferred` (weight 0.5).

| Candidate has the skill... | Credit | Badge colour |
|---|---|---|
| with no qualifier (solid) | 1.00 | green |
| marked "working knowledge" | 0.75 | yellow |
| marked "basic" | 0.50 | orange |
| implied by a related tool it lists (e.g. TensorFlow implies Machine Learning) | 0.75 | yellow ("inferred") |
| not at all | 0.00 | red |

`skills_score = 100 * sum(credit * weight) / sum(weight)`.
`skill_match_ratio` = required skills matched (at any level) / required skills. This is the "9 out of 10" figure
used in explanations. Soft skills are NOT part of this component.

## 2. Experience (code)

`effective_years = employment_years + 0.5 * internship_years`. Internships are parsed from their date ranges
(3 months assumed if unparseable) and count half, because they are real work but shorter and supervised.
Academic projects do not count.

- JD minimum > 0: `score = min(100, effective_years / minimum * 100)`
- No minimum (entry level): `score = min(100, 60 + 20 * effective_years)`. A fresher with no internships scores 60.

## 3 and 4. LLM judgment

The LLM receives the JD summary, the candidate profile and the computed facts (matched, missing, levels).
It returns two 0-100 scores, strengths, weaknesses, a summary and interview questions. Temperature is 0.
If the LLM is unavailable, both LLM scores fall back to the skills score and the result is marked
`llm_status = "unavailable"` so a recruiter knows the analysis text is missing.

## Recommendation

| Final score | Recommendation |
|---|---|
| 70 or above | Shortlist |
| 45 to 69 | Consider |
| below 45 | Reject |

Thresholds are constants in `app/llm/scoring.py` and can be tuned.

## Known limitations

- Skill matching is exact after alias normalization, plus a small table of implications (`IMPLIED_BY` in `scoring.py`). A related-but-different skill (e.g. `PyTorch` for `TensorFlow`) earns no credit.
  The LLM fit score can acknowledge it in text, and the alias table can be extended.
- Proficiency comes only from wording on the resume ("basic", "working knowledge"). Unstated depth is treated as solid.
- Weights are judgment calls, chosen so that verifiable skills dominate.
