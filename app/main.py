"""FastAPI entrypoint: JSON API under /api, and the web UI (frontend/) served as static files at /.

Run:  uvicorn app.main:app --reload      then open http://127.0.0.1:8000
API docs are generated automatically at /docs.
"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles

from app import deps
from app.config import LLM_MODE
from app.routes import analysis, auth, query, upload

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(_: FastAPI):
    deps.startup()          # create tables on first run
    yield


app = FastAPI(title="Recruitment Assistant", version="1.0", lifespan=lifespan)
app.include_router(auth.router)                      # login/logout/status are public; user management checks admin itself
protected = [Depends(auth.current_user)]             # no-op while AUTH_ENABLED is off; 401 without a valid login when on
app.include_router(upload.router, dependencies=protected)
app.include_router(analysis.router, dependencies=protected)
app.include_router(query.router, dependencies=protected)


@app.middleware("http")
async def security_headers(request, call_next):
    """Standard browser protections on every response. The CSP is deliberately limited to directives that cannot break
    the page (framing, base tag, forms, plugins); a full script policy would need the Tailwind CDN's inline code allowed."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'")
    return response


@app.get("/api/health")
def health():
    return {"status": "ok", "llm_mode": LLM_MODE}


# Mounted last so /api/* routes win. html=True serves index.html at "/".
if FRONTEND.exists():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="ui")
