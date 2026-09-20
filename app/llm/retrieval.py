"""Hybrid retrieval over resume text: keyword (BM25) plus semantic (embeddings), fused by weighted reciprocal rank.

Why both: keyword search is exact and needs no API, but misses paraphrases ("recognising objects in camera images"
does not share words with "OpenCV, YOLO"). Semantic search finds meaning but can blur exact terms. Fusing them, with
semantic weighted 2:1, matched the best single method on our test queries (see docs/BENCHMARK.md).
"""
import math
import re
import struct

TOKEN = re.compile(r"[a-z0-9+#.]+")
SEMANTIC_WEIGHT = 2.0
RRF_K = 60


def chunk_text(text: str, max_chars: int = 450) -> list[str]:
    """Split on lines, then pack lines into chunks of about max_chars so each chunk stays about one topic."""
    chunks, cur = [], ""
    for line in (ln.strip() for ln in text.splitlines()):
        if not line:
            continue
        if cur and len(cur) + len(line) > max_chars:
            chunks.append(cur)
            cur = ""
        cur += (" " if cur else "") + line
    if cur:
        chunks.append(cur)
    return chunks


def pack(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def unpack(blob: bytes | None) -> list[float] | None:
    return list(struct.unpack(f"{len(blob) // 4}f", blob)) if blob else None


def cosine(a: list[float], b: list[float]) -> float:
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


def bm25_scores(query: str, texts: list[str], k1: float = 1.5, b: float = 0.75) -> list[float]:
    docs = [TOKEN.findall(t.lower()) for t in texts]
    n = len(docs)
    if not n:
        return []
    avg = sum(len(d) for d in docs) / n or 1.0
    df: dict[str, int] = {}
    for d in docs:
        for w in set(d):
            df[w] = df.get(w, 0) + 1
    q = TOKEN.findall(query.lower())
    out = []
    for d in docs:
        tf: dict[str, int] = {}
        for w in d:
            tf[w] = tf.get(w, 0) + 1
        score = 0.0
        for w in q:
            if w in tf:
                idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
                score += idf * tf[w] * (k1 + 1) / (tf[w] + k1 * (1 - b + b * len(d) / avg))
        out.append(score)
    return out


def _best_per_owner(chunks: list[dict], scores: list[float]) -> dict:
    """owner -> (best score, index of the chunk that earned it). Zero-score chunks are not matches."""
    best: dict = {}
    for i, (c, s) in enumerate(zip(chunks, scores)):
        if s > 0 and (c["owner"] not in best or s > best[c["owner"]][0]):
            best[c["owner"]] = (s, i)
    return best


def hybrid_rank(query: str, query_vec: list[float] | None, chunks: list[dict], limit: int = 5) -> list[dict]:
    """chunks: [{owner, text, vec|None}]. Returns owners best-first with their best snippet and per-method evidence.

    With no query vector (embeddings unavailable) this degrades gracefully to keyword-only ranking.
    """
    if not chunks:
        return []
    kw_best = _best_per_owner(chunks, bm25_scores(query, [c["text"] for c in chunks]))
    sem_best: dict = {}
    if query_vec:
        sem_best = _best_per_owner(chunks, [cosine(query_vec, c["vec"]) if c.get("vec") else 0.0 for c in chunks])

    fused: dict = {}
    for weight, best in ((1.0, kw_best), (SEMANTIC_WEIGHT, sem_best)):
        for rank, (owner, _) in enumerate(sorted(best.items(), key=lambda kv: -kv[1][0])):
            fused[owner] = fused.get(owner, 0.0) + weight / (RRF_K + rank + 1)

    out = []
    for owner, score in sorted(fused.items(), key=lambda kv: -kv[1])[:limit]:
        idx = sem_best[owner][1] if owner in sem_best else kw_best[owner][1]      # snippet from the best semantic chunk
        out.append({"owner": owner, "score": round(score * 1000, 2), "snippet": chunks[idx]["text"],
                    "keyword_match": owner in kw_best,
                    "semantic_similarity": round(sem_best[owner][0], 3) if owner in sem_best else None})
    return out
