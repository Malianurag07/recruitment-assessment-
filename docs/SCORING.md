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
| shows it in other words, proven by a verbatim quote from the resume (see below) | 0.75 | yellow ("by meaning") |
| shows only related or adjacent experience, with a verbatim quote | 0.40 | orange ("partial") |
| not at all | 0.00 | red |

`skills_score = 100 * sum(credit * weight) / sum(weight)`.
`skill_match_ratio` = required skills matched (at any level) / required skills. This is the "9 out of 10" figure
used in explanations. Soft skills are NOT part of this component.

### Matching by meaning (added after testing a non-technical resume)

Word-for-word matching scored a strong medical-representative resume at 0% skills, because it says "Physician Detailing"
where the job says something else. So after the word-for-word pass, the requirements still marked missing go to **one**
extra AI call (`semantic_cover` in `scoring.py`, prompt `SEMANTIC_SYSTEM`). The AI must return a **verbatim quote** from the
resume for every match, and the code checks the quote really appears in the resume text; an invented quote is discarded.
So the AI can point at evidence but cannot create it, the same rule the verifier follows. If the call fails, the
word-for-word result stands. The quote is shown as a tooltip on the skill badge.

Measured on one pharma resume, PDF and Word, twice each: 38.0 / 32.0 / 35.0 / 30.5 before, 86.6 / 93.8 / 88.1 / 85.8 after.
The job-description prompt was also rewritten: it used to ask only for "technical skills/tools", so a sales job's real
requirements (territory management, call reports, product detailing) were dropped in favour of vague traits.

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

- Skill matching is exact after alias normalization, plus a small table of implications (`IMPLIED_BY` in `scoring.py`), plus the quote-backed
  meaning pass above. The meaning pass costs one extra AI call per resume and is only as good as the model's reading; chat questions such as
  "who knows X?" still look at the skills list, not at meaning matches.
- The job description is parsed once per job, and the AI's list of requirements can differ a little between two separate parses of the same text
  (measured: 6 to 8 items). Within one job every resume is compared with the same list, so the ranking there is consistent.
- Proficiency comes only from wording on the resume ("basic", "working knowledge"). Unstated depth is treated as solid.
- Weights are judgment calls, chosen so that verifiable skills dominate.
