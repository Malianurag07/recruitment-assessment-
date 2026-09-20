"""Job description text -> validated JobProfile (same validate/retry pattern as resume extraction)."""
import json
import re
from dataclasses import dataclass
from typing import Callable

from pydantic import ValidationError

from app.llm import client, prompts
from app.models import JobProfile

LLMFn = Callable[[str, str], str]


@dataclass
class JobResult:
    status: str                      # ok | needs_review
    job: JobProfile | None = None
    error: str = ""


def _default_llm(system: str, user: str) -> str:
    return client.complete_with_fallback(system, user, [("groq", None), ("gemini", None)])


def extract_job(text: str, llm: LLMFn = _default_llm) -> JobResult:
    if len(text.strip()) < 30:
        return JobResult("needs_review", error="Job description is empty or too short")
    user = "Job description:\n\n" + text[:12000]
    error = ""
    for attempt in range(2):
        prompt = user if attempt == 0 else user + prompts.RETRY_SUFFIX.format(error=error)
        try:
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", llm(prompts.JD_SYSTEM, prompt).strip())
            job = JobProfile.model_validate(json.loads(raw))
            if not job.required_skills:
                error = "No required skills were extracted"
                continue
            return JobResult("ok", job)
        except (json.JSONDecodeError, ValueError, ValidationError) as exc:
            error = str(exc)[:300]
        except client.LLMError as exc:
            return JobResult("needs_review", error=str(exc))
    return JobResult("needs_review", error=error)
