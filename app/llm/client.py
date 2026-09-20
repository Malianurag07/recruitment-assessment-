"""Thin LLM wrapper. The rest of the app only calls complete(); providers can be swapped here."""
import os
import random
import threading
import time

import requests

from app.config import EMBED_DIMS, GEMINI_API_KEY, GEMINI_EMBED_MODEL, LLM_MODE, GEMINI_FALLBACK_MODEL, GEMINI_MODEL, GROQ_API_KEY, GROQ_FAST_MODEL, GROQ_MODEL, OLLAMA_MODEL, OLLAMA_URL


class LLMError(Exception):
    pass


# Process-wide cap on simultaneous AI calls. Free tiers reject bursts (HTTP 429); queueing calls here is far cheaper
# than failing them. Consensus scoring alone fires 3 calls at once, so parallel uploads would otherwise stampede.
_SLOTS = threading.BoundedSemaphore(int(os.getenv("LLM_MAX_CONCURRENCY", "1" if LLM_MODE == "local" else "4")))


def complete(system: str, user: str, provider: str = "groq", model: str | None = None,
             json_mode: bool = True, temperature: float = 0.0) -> str:
    """Send one prompt, return the model's text. Temperature 0 for repeatable output."""
    if provider not in ("groq", "gemini", "ollama"):
        raise LLMError(f"Unknown provider: {provider}")
    if LLM_MODE == "local":
        provider, model = "ollama", None          # self-hosted mode: nothing leaves this machine
    with _SLOTS:
        if provider == "groq":
            return _groq(system, user, model or GROQ_FAST_MODEL, json_mode, temperature)
        if provider == "gemini":
            return _gemini(system, user, model or GEMINI_MODEL, json_mode, temperature)
        return _ollama(system, user, model or OLLAMA_MODEL, json_mode, temperature)


def complete_with_fallback(system: str, user: str, attempts: list[tuple[str, str | None]], *, retries: int = 3,
                           backoff: float = 3.0, **kwargs) -> str:
    """Try each (provider, model) in order; if every one fails, wait and go round again.

    Free-tier APIs return 429 under bursts. Waits grow exponentially (about 3-6s, 6-12s, 12-24s) and are jittered so
    parallel workers do not retry in lockstep.
    """
    last: LLMError | None = None
    for round_no in range(retries + 1):
        for provider, model in attempts:
            try:
                return complete(system, user, provider=provider, model=model, **kwargs)
            except LLMError as exc:
                last = exc
        if round_no < retries:
            time.sleep(backoff * (2 ** round_no) * (1 + random.random()))
    raise last or LLMError("No provider configured")


def _groq(system, user, model, json_mode, temperature):
    if not GROQ_API_KEY:
        raise LLMError("GROQ_API_KEY is not set in .env")
    from groq import Groq
    kwargs = {"response_format": {"type": "json_object"}} if json_mode else {}
    if "gpt-oss" in model:
        # Reasoning models spend output tokens on hidden thinking; without these they can return empty text.
        kwargs.update(reasoning_effort="low", max_completion_tokens=4096)
    try:
        # max_retries=0: fail fast on rate limits so we can switch models instead of waiting silently.
        resp = Groq(api_key=GROQ_API_KEY, max_retries=0).chat.completions.create(
            model=model, temperature=temperature,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            **kwargs,
        )
    except Exception as exc:
        if getattr(exc, "status_code", None) == 429 and model != GROQ_MODEL:
            # Each Groq model has its own quota, so a rate-limited model can hand over to the bigger one.
            return _groq(system, user, GROQ_MODEL, json_mode, temperature)
        raise LLMError(f"Groq call failed: {exc}") from exc
    return resp.choices[0].message.content


def _gemini(system, user, model, json_mode, temperature):
    if not GEMINI_API_KEY:
        raise LLMError("GEMINI_API_KEY is not set in .env")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    config = {"temperature": temperature}
    if json_mode:
        config["responseMimeType"] = "application/json"
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": config,
    }
    try:
        r = requests.post(url, params={"key": GEMINI_API_KEY}, json=body, timeout=60)
        if r.status_code in (429, 503) and model != GEMINI_FALLBACK_MODEL:
            # Model busy or rate-limited: retry once on the fallback model instead of failing.
            return _gemini(system, user, GEMINI_FALLBACK_MODEL, json_mode, temperature)
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    except LLMError:
        raise
    except Exception as exc:
        # Strip the URL (it carries the API key as a query parameter) before surfacing the error.
        raise LLMError(f"Gemini call failed: {type(exc).__name__} {getattr(getattr(exc, 'response', None), 'status_code', '')}") from None


def _ollama(system, user, model, json_mode, temperature):
    """Local self-hosted model. Slow on CPU, so the timeout is generous."""
    body = {"model": model, "stream": False, "options": {"temperature": temperature, "num_ctx": 8192},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if json_mode:
        body["format"] = "json"
    try:
        r = requests.post(f"{OLLAMA_URL}/api/chat", json=body, timeout=600)
        r.raise_for_status()
        return r.json()["message"]["content"]
    except requests.ConnectionError as exc:
        raise LLMError("Ollama is not running (start it, or run: ollama serve)") from exc
    except Exception as exc:
        raise LLMError(f"Ollama call failed: {type(exc).__name__}") from exc


def embed(texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT") -> list[list[float]]:
    """Gemini embeddings (768 dims by default). Raises LLMError when unavailable, which callers treat as 'keyword only'."""
    if LLM_MODE == "local":
        raise LLMError("Embeddings are not available in local mode (keyword search is used instead)")
    if not GEMINI_API_KEY:
        raise LLMError("GEMINI_API_KEY is not set in .env")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_EMBED_MODEL}:batchEmbedContents"
    out: list[list[float]] = []
    for i in range(0, len(texts), 90):
        body = {"requests": [{"model": f"models/{GEMINI_EMBED_MODEL}", "taskType": task_type, "outputDimensionality": EMBED_DIMS,
                              "content": {"parts": [{"text": t}]}} for t in texts[i:i + 90]]}
        for attempt in range(3):
            try:
                with _SLOTS:
                    r = requests.post(url, params={"key": GEMINI_API_KEY}, json=body, timeout=60)
                if r.status_code in (429, 503) and attempt < 2:
                    time.sleep(2 ** attempt * (1 + random.random()))
                    continue
                r.raise_for_status()
                out += [e["values"] for e in r.json()["embeddings"]]
                break
            except Exception as exc:
                if attempt == 2:
                    raise LLMError(f"Embedding call failed: {type(exc).__name__}") from None
    return out
