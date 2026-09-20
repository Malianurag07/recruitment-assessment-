import json

from app.llm.client import LLMError
from app.llm.query_tools import Ctx, TOOLS, call_tool
from app.llm.retrieval import TOKEN, bm25_scores, chunk_text, hybrid_rank, pack, unpack
from app.services import candidate_service as svc
from app.services.candidate_service import get_job
from app.services.skill_normalizer import load_aliases
from conftest import _judge, add_candidate, make_resume_pdf

# A tiny stand-in for a real embedding model: words that mean the same thing light up the same dimension.
CONCEPTS = [{"opencv", "yolo", "camera", "footage", "images", "photos", "objects", "detection", "recognising"},
            {"sql", "postgresql", "database", "queries"},
            {"fastapi", "flask", "api"}]


def fake_embed(texts, task_type="RETRIEVAL_DOCUMENT"):
    return [[1.0 if set(TOKEN.findall(t.lower())) & c else 0.0 for c in CONCEPTS] for t in texts]


def down(texts, task_type="RETRIEVAL_DOCUMENT"):
    raise LLMError("embedding service down")


# ---------------------------------------------------------------- building blocks
def test_chunking_keeps_all_text_and_respects_size():
    text = "\n".join(f"line number {i} " + "word " * 20 for i in range(30))
    chunks = chunk_text(text, max_chars=300)
    assert len(chunks) > 3 and all(len(c) <= 420 for c in chunks)
    assert "line number 29" in chunks[-1] and chunk_text("") == [] and chunk_text("  \n \n") == []


def test_vector_roundtrip_through_a_blob():
    v = [0.25, -1.5, 3.0]
    assert unpack(pack(v)) == v and unpack(None) is None


def test_keyword_scores_rank_the_exact_term_first():
    texts = ["Java and Spring Boot backend", "Selenium testing and QA", "Cybersecurity and log analysis"]
    scores = bm25_scores("selenium testing", texts)
    assert scores.index(max(scores)) == 1 and scores[0] == 0


CHUNKS = [
    {"owner": 1, "text": "Built detection pipelines with OpenCV and YOLO on camera footage", "vec": fake_embed(["opencv yolo camera"])[0]},
    {"owner": 2, "text": "Wrote SQL queries against PostgreSQL databases", "vec": fake_embed(["sql postgresql"])[0]},
]


def test_paraphrase_is_found_by_hybrid_but_missed_by_keyword_alone():
    q = "recognising objects in photos"                       # shares no word with candidate 1's text
    assert hybrid_rank(q, None, CHUNKS) == []                   # keyword only: nothing
    hits = hybrid_rank(q, fake_embed([q])[0], CHUNKS)
    assert [h["owner"] for h in hits] == [1] and hits[0]["semantic_similarity"] > 0.9 and hits[0]["keyword_match"] is False


def test_exact_terms_still_win_when_keyword_and_meaning_agree():
    hits = hybrid_rank("PostgreSQL", fake_embed(["postgresql"])[0], CHUNKS)
    assert hits[0]["owner"] == 2 and hits[0]["keyword_match"] is True


def test_no_chunks_and_no_matches_are_empty_not_errors():
    assert hybrid_rank("anything", None, []) == []
    assert hybrid_rank("zzzz", None, CHUNKS) == []


# ---------------------------------------------------------------- indexing on upload
def _upload(conn, job_id, embed):
    profile = {"name": "Vision Person", "email": "vision@x.com", "phone": "9333333333", "skills": ["Python"], "experience_years": 0}
    pdf = make_resume_pdf("Vision Person", "vision@x.com", "9333333333", ["Python"],
                          extra="Built object detection pipelines using OpenCV and YOLO on camera footage.")
    return svc.process_resume(conn, pdf, "vision.pdf", job_id, extract_llm=lambda s, u: json.dumps(profile),
                              verify_llm=_judge, canon_llm=_judge, score_llm=_judge, embed_llm=embed)


def test_upload_stores_chunks_with_embeddings(pool):
    conn, job_id = pool
    o = _upload(conn, job_id, fake_embed)
    rows = conn.execute("SELECT text, embedding FROM resume_chunks WHERE application_id=?", (o.application_id,)).fetchall()
    assert o.status == "stored" and rows and all(r["embedding"] for r in rows)
    assert any("OpenCV" in r["text"] for r in rows)


def test_embedding_outage_never_blocks_an_upload(pool):
    conn, job_id = pool
    o = _upload(conn, job_id, down)
    assert o.status == "stored"
    rows = conn.execute("SELECT embedding FROM resume_chunks WHERE application_id=?", (o.application_id,)).fetchall()
    assert rows and all(r["embedding"] is None for r in rows)          # chunks kept for keyword search


def test_reindex_fills_missing_embeddings_and_missing_chunks(pool):
    conn, job_id = pool
    _upload(conn, job_id, down)
    n = svc.reindex_missing_embeddings(conn, job_id, embed_llm=fake_embed)
    assert n > 0 and conn.execute("SELECT COUNT(*) FROM resume_chunks WHERE embedding IS NULL").fetchone()[0] == 0
    conn.execute("DELETE FROM resume_chunks")                            # simulate resumes stored before the feature existed
    conn.commit()
    assert svc.reindex_missing_embeddings(conn, job_id, embed_llm=fake_embed) > 0
    assert conn.execute("SELECT COUNT(*) FROM resume_chunks").fetchone()[0] > 0


# ---------------------------------------------------------------- the chat tool
def ctx_for(pool, embed=fake_embed):
    conn, job_id = pool
    return Ctx(conn, job_id, load_aliases(conn), get_job(conn, job_id), embed_fn=embed)


def test_tool_finds_the_candidate_by_meaning(pool):
    conn, job_id = pool
    _upload(conn, job_id, fake_embed)
    svc.reindex_missing_embeddings(conn, job_id, embed_llm=fake_embed)
    r = call_tool(ctx_for(pool), "semantic_search", {"query": "recognising objects in camera images"})
    assert r["method"].startswith("hybrid") and r["matches"][0]["name"] == "Vision Person"
    assert r["matches"][0]["semantic_similarity"] > 0.9 and "OpenCV" in r["matches"][0]["snippet"]


def test_tool_falls_back_to_keyword_when_the_embedding_service_is_down(pool):
    conn, job_id = pool
    _upload(conn, job_id, fake_embed)
    r = call_tool(ctx_for(pool, embed=down), "semantic_search", {"query": "OpenCV YOLO"})
    assert r["method"].startswith("keyword only") and r["matches"][0]["name"] == "Vision Person"


def test_tool_reports_nothing_found_honestly_and_rejects_tiny_queries(pool):
    r = call_tool(ctx_for(pool), "semantic_search", {"query": "quantum chromodynamics"})
    assert r["count"] == 0 and "Nothing" in r["note"]
    assert "error" in call_tool(ctx_for(pool), "semantic_search", {"query": "a"})


def test_tool_only_searches_active_resumes(pool):
    conn, job_id = pool
    o = _upload(conn, job_id, fake_embed)
    conn.execute("UPDATE applications SET application_status='superseded' WHERE id=?", (o.application_id,))
    conn.commit()
    r = call_tool(ctx_for(pool), "semantic_search", {"query": "OpenCV YOLO camera"})
    assert all(m["name"] != "Vision Person" for m in r["matches"])


def test_tool_is_registered_with_guidance():
    assert "semantic_search" in TOOLS and "MEANING" in TOOLS["semantic_search"].description
