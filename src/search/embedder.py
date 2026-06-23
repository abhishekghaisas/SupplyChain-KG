"""
Embedder — wraps sentence-transformers for local, zero-cost embeddings.

Model: all-MiniLM-L6-v2
  - 384-dimensional vectors
  - ~80 MB on disk
  - Fast on CPU (< 10 ms per text on modern hardware)
  - Good semantic quality for short technical descriptions

The model is loaded once (module-level singleton) and reused across all
calls. First call downloads the model to ~/.cache/huggingface/ if not
already present.

Usage:
    from src.search.embedder import embed, embed_batch

    vec  = embed("servo motor 400W")               # List[float], len=384
    vecs = embed_batch(["part A", "part B"])        # List[List[float]]
"""

from __future__ import annotations

from typing import List

from loguru import logger

_model = None
MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384


def _get_model():
    global _model
    if _model is None:
        logger.info(f"Loading sentence-transformers model: {MODEL_NAME}")
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_NAME)
        logger.info("Model loaded")
    return _model


def embed(text: str) -> List[float]:
    """
    Embed a single text string.

    Args:
        text: The text to embed (part name, description, supplier name, etc.)

    Returns:
        List of 384 floats (unit vector).
    """
    if not text or not text.strip():
        return [0.0] * EMBEDDING_DIM
    model = _get_model()
    vec = model.encode(text.strip(), normalize_embeddings=True)
    return vec.tolist()


def embed_batch(texts: List[str]) -> List[List[float]]:
    """
    Embed multiple texts efficiently in one model pass.

    Args:
        texts: List of strings to embed.

    Returns:
        List of embedding vectors, same order as input.
    """
    if not texts:
        return []
    model = _get_model()
    vecs = model.encode(
        [t.strip() if t else "" for t in texts],
        normalize_embeddings=True,
        batch_size=32,
        show_progress_bar=False,
    )
    return [v.tolist() for v in vecs]


def part_text(part: dict) -> str:
    """
    Build the text to embed for a part node.

    Combines name, description, category, and key specs so that queries
    like "24V stepper driver" find the right part.
    """
    import json
    parts_text = [
        part.get("name", ""),
        part.get("description", ""),
        part.get("category", ""),
        part.get("criticality", ""),
    ]
    # Include spec values (not keys) for richer semantic content
    specs_raw = part.get("specifications_json") or part.get("specifications") or {}
    if isinstance(specs_raw, str):
        try:
            specs_raw = json.loads(specs_raw)
        except Exception:
            specs_raw = {}
    if isinstance(specs_raw, dict):
        parts_text.extend(str(v) for v in specs_raw.values() if v)

    return " ".join(filter(None, parts_text))


def supplier_text(supplier: dict) -> str:
    """Build the text to embed for a supplier node."""
    certs = supplier.get("certifications") or []
    if isinstance(certs, str):
        import json
        try:
            certs = json.loads(certs)
        except Exception:
            certs = [certs]
    return " ".join(filter(None, [
        supplier.get("name", ""),
        supplier.get("location", ""),
        " ".join(certs),
    ]))


def bom_text(bom: dict) -> str:
    """Build the text to embed for a BOM node."""
    return " ".join(filter(None, [
        bom.get("name", ""),
        bom.get("description", ""),
        bom.get("version", ""),
        bom.get("status", ""),
    ]))