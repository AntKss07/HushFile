"""Text and source code extractor."""

from __future__ import annotations

from pathlib import Path

from butler.config import ButlerConfig
from butler.extractors import ExtractionResult


def extract_text(path: Path, config: ButlerConfig) -> ExtractionResult:
    """Extract text from text and source code files."""
    try:
        max_chars = min(config.extract.max_chars, 4000)
        content = ""
        # Try UTF-8 first, fallback to latin-1
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(max_chars)
        except Exception:
            with open(path, "r", encoding="latin-1", errors="replace") as f:
                content = f.read(max_chars)

        return ExtractionResult(
            text=content.strip(),
            metadata={
                "filename": path.name,
                "extension": path.suffix.lower(),
                "size_bytes": path.stat().st_size if path.exists() else 0,
            },
        )
    except Exception as e:
        return ExtractionResult(
            text="",
            metadata={"filename": path.name, "extension": path.suffix.lower()},
            warning=f"Failed to read text file: {e}",
        )
