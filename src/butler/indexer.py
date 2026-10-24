"""Semantic indexing using nomic-embed-text via Ollama and local ChromaDB."""

from __future__ import annotations

import logging
from pathlib import Path

import chromadb
import httpx

from butler.config import ButlerConfig

logger = logging.getLogger(__name__)


def get_embedding(
    text: str,
    model: str = "nomic-embed-text",
    base_url: str = "http://localhost:11434",
    timeout: float = 15.0,
) -> list[float] | None:
    """Compute vector embedding using local Ollama nomic-embed-text."""
    if not text.strip():
        return None
    try:
        with httpx.Client(base_url=base_url, timeout=timeout) as client:
            # Try /api/embeddings then /api/embed
            resp = client.post("/api/embeddings", json={"model": model, "prompt": text})
            if resp.status_code == 200:
                data = resp.json()
                return data.get("embedding")

            # Fallback for newer Ollama /api/embed endpoint
            resp2 = client.post("/api/embed", json={"model": model, "input": text})
            if resp2.status_code == 200:
                data = resp2.json()
                embeddings = data.get("embeddings", [])
                if embeddings:
                    return embeddings[0]
    except Exception as e:
        logger.debug(f"Failed to get embedding from {model}: {e}")
    return None


class DocumentIndexer:
    """Manages ChromaDB vector store for offline semantic search."""

    def __init__(self, config: ButlerConfig, persist_dir: Path | None = None) -> None:
        self.config = config
        base_dir = persist_dir or (config.db_path.parent if config.db_path else Path.home() / ".config" / "butler")
        self.chroma_path = base_dir / "chroma"
        self.chroma_path.mkdir(parents=True, exist_ok=True)

        self.client = chromadb.PersistentClient(path=str(self.chroma_path))
        self.collection = self.client.get_or_create_collection(
            name="butler_documents",
            metadata={"description": "HushFile semantic index"},
        )

    def index_document(
        self,
        file_id: int,
        summary: str,
        text_excerpt: str,
        category: str,
        tags: list[str] | str,
        current_path: str,
    ) -> bool:
        """Embed document summary + excerpt and store in ChromaDB."""
        content_to_embed = f"{summary}\n\n{text_excerpt}".strip()
        embedding = get_embedding(
            text=content_to_embed,
            model=self.config.models.embed,
        )
        if embedding is None:
            return False

        tags_str = ", ".join(tags) if isinstance(tags, list) else str(tags)
        metadata = {
            "file_id": file_id,
            "category": category,
            "tags": tags_str,
            "current_path": str(current_path),
            "summary": summary[:500],
        }

        self.collection.upsert(
            ids=[str(file_id)],
            embeddings=[embedding],
            documents=[content_to_embed[:1500]],
            metadatas=[metadata],
        )
        return True

    def remove_document(self, file_id: int) -> None:
        """Remove document embedding from index (e.g. after undo)."""
        try:
            self.collection.delete(ids=[str(file_id)])
        except Exception:
            pass
