"""Semantic search engine powered by ChromaDB vector queries."""

from __future__ import annotations

import logging
from typing import Any

from butler.config import ButlerConfig
from butler.indexer import DocumentIndexer, get_embedding

logger = logging.getLogger(__name__)


def search_documents(
    query: str,
    config: ButlerConfig,
    limit: int = 5,
    doc_type: str | None = None,
    since: str | None = None,
    indexer: DocumentIndexer | None = None,
) -> list[dict[str, Any]]:
    """Search indexed documents by natural language query."""
    idx = indexer or DocumentIndexer(config)

    query_embedding = get_embedding(query, model=config.models.embed)
    if not query_embedding:
        logger.warning(
            f"Could not compute embedding for query with {config.models.embed}. Ensure Ollama is running and model is pulled."
        )
        return []

    # Prepare metadata filter where clause if specified
    where_filter: dict[str, Any] = {}
    if doc_type:
        where_filter["category"] = doc_type

    query_kwargs: dict[str, Any] = {
        "query_embeddings": [query_embedding],
        "n_results": min(limit * 2, 20),
    }
    if where_filter:
        query_kwargs["where"] = where_filter

    results = idx.collection.query(**query_kwargs)

    hits: list[dict[str, Any]] = []
    ids = results.get("ids", [[]])[0]
    distances = results.get("distances", [[]])[0] if "distances" in results else [0.0] * len(ids)
    metadatas = results.get("metadatas", [[]])[0] if "metadatas" in results else [{}] * len(ids)
    documents = results.get("documents", [[]])[0] if "documents" in results else [""] * len(ids)

    for i, file_id in enumerate(ids):
        meta = metadatas[i] or {}
        dist = distances[i] if i < len(distances) else 0.0
        # Cosine distance to similarity: sim = max(0.0, 1.0 - dist)
        similarity = max(0.0, 1.0 - float(dist)) if dist is not None else 0.0

        hits.append(
            {
                "file_id": file_id,
                "current_path": meta.get("current_path", ""),
                "category": meta.get("category", ""),
                "tags": meta.get("tags", ""),
                "summary": meta.get("summary", ""),
                "similarity": round(similarity, 3),
                "excerpt": (documents[i] or "")[:300],
            }
        )

    # Sort and slice
    hits.sort(key=lambda x: x["similarity"], reverse=True)
    return hits[:limit]
