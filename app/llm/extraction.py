"""Resume text -> validated CandidateProfile, with one retry and a needs_review fallback."""
import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Callable

from pydantic import ValidationError

from app.llm import client, prompts
from app.models import CandidateProfile

# An LLM function takes (system, user) and returns raw text. Injectable so tests need no API key.
LLMFn = Callable[[str, str], str]
MAX_CHARS = 12000  # keep prompts small; resumes are rarely longer


@dataclass
class ProfileResult:
    status: str                      # ok | needs_review
    profile: CandidateProfile | None = None
    error: str = ""


def _default_llm(system: str, user: str) -> str:
    return client.complete_with_fallback(system, user, [("groq", None), ("gemini", None)])


def _parse_json(raw: str) -> dict:
    """Strip markdown fences some models add, then parse."""
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Top-level JSON must be an object")
    return data


def extract_profile(text: str, llm: LLMFn = _default_llm) -> ProfileResult:
    """Try up to twice. On second failure, flag for manual review instead of crashing."""
    user = prompts.EXTRACTION_USER.format(text=text[:MAX_CHARS], today=date.today().isoformat())
    error = ""
    for attempt in range(2):
        prompt = user if attempt == 0 else user + prompts.RETRY_SUFFIX.format(error=error)
        try:
            raw = llm(prompts.EXTRACTION_SYSTEM, prompt)
            profile = CandidateProfile.model_validate(_parse_json(raw))
            if not profile.email and not profile.phone:
                # Without email/phone we cannot identify the person or detect duplicates.
                return ProfileResult("needs_review", profile, "No email or phone found in resume")
            return ProfileResult("ok", profile)
        except (json.JSONDecodeError, ValueError, ValidationError) as exc:
            # ValidationError subclasses ValueError; listed explicitly for readability.
            error = str(exc)[:300]
        except client.LLMError as exc:
            return ProfileResult("needs_review", error=str(exc))  # API problem: retrying won't help
    return ProfileResult("needs_review", error=error)
