"""Extractor dispatch and common ExtractionResult model."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from butler.config import ButlerConfig

logger = logging.getLogger(__name__)


@dataclass
class ExtractionResult:
    text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    warning: str | None = None
    char_count: int = 0

    def __post_init__(self) -> None:
        self.char_count = len(self.text)


def extract_content(path: Path | str, config: ButlerConfig) -> ExtractionResult:
    """Extract text and metadata from a file based on its extension.

    Guaranteed to never raise an unhandled exception: errors return empty text with warnings.
    """
    p = Path(path)
    suffix = p.suffix.lower()

    # Dynamic imports to keep initial imports lightweight
    try:
        if suffix == ".pdf":
            from butler.extractors.pdf import extract_pdf
            return extract_pdf(p, config)

        elif suffix in [".docx", ".xlsx", ".xls", ".csv"]:
            from butler.extractors.office import extract_office
            return extract_office(p, config)

        elif suffix in [".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".gif"]:
            from butler.extractors.image import extract_image
            return extract_image(p, config)

        elif suffix in [".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"]:
            from butler.extractors.audio import extract_audio
            return extract_audio(p, config)

        elif suffix in [
            ".txt", ".md", ".markdown", ".rst", ".json", ".yaml", ".yml",
            ".py", ".js", ".ts", ".html", ".css", ".sql", ".sh", ".ps1",
            ".bat", ".c", ".cpp", ".h", ".rs", ".go", ".java", ".xml", ".log"
        ]:
            from butler.extractors.text import extract_text
            return extract_text(p, config)

        elif suffix in [".zip", ".tar", ".gz", ".bz2", ".7z", ".rar", ".exe", ".msi", ".dmg", ".pkg", ".iso", ".deb", ".rpm"]:
            # Binary archives / installers: metadata only, rule-classified
            return ExtractionResult(
                text="",
                metadata={
                    "filename": p.name,
                    "extension": suffix,
                    "is_binary_package": True,
                    "size_bytes": p.stat().st_size if p.exists() else 0,
                },
            )

        else:
            # Fallback: attempt reading small utf-8 snippet, else metadata only
            from butler.extractors.text import extract_text
            try:
                res = extract_text(p, config)
                if res.text:
                    return res
            except Exception:
                pass

            return ExtractionResult(
                text="",
                metadata={
                    "filename": p.name,
                    "extension": suffix,
                    "unknown_format": True,
                    "size_bytes": p.stat().st_size if p.exists() else 0,
                },
                warning=f"No specific extractor for extension {suffix}",
            )

    except Exception as e:
        logger.warning(f"Extraction failed gracefully for {p.name}: {e}")
        return ExtractionResult(
            text="",
            metadata={"filename": p.name, "extension": suffix},
            warning=f"Extractor error: {e!s}",
        )
