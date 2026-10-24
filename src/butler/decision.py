"""Filename generation, sanitization, collision resolution, and routing logic."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from pathlib import Path

from butler.config import ButlerConfig
from butler.schemas import ClassificationResult

# Windows reserved device names
WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}


def sanitize_title(title: str) -> str:
    """Normalize and convert title to lowercase kebab-case, safe across OSes."""
    # Unicode normalize
    text = unicodedata.normalize("NFKC", title)
    # Remove illegal characters across Windows/Linux/Mac: <>:"/\|?* and control chars
    text = re.sub(r'[\x00-\x1f<>:"/\\|?*]', " ", text)
    # Convert punctuation and whitespace into hyphens
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"[^\w\-]", "", text, flags=re.UNICODE)
    text = re.sub(r"-+", "-", text).strip("-").lower()
    return text or "document"


def build_filename(
    suggested_title: str,
    original_suffix: str,
    doc_date: str | None = None,
    file_mtime: float | None = None,
    max_length: int = 80,
) -> str:
    """Construct YYYY-MM-DD_kebab-case-title.ext, max 80 chars, preserving extension."""
    ext = original_suffix.lower()
    if not ext.startswith("."):
        ext = f".{ext}" if ext else ""

    # Determine date prefix
    date_str = ""
    if doc_date and re.match(r"^\d{4}-\d{2}-\d{2}$", doc_date.strip()):
        date_str = doc_date.strip()
    elif file_mtime:
        date_str = datetime.fromtimestamp(file_mtime).strftime("%Y-%m-%d")
    else:
        date_str = datetime.now().strftime("%Y-%m-%d")

    date_prefix = f"{date_str}_"
    clean_title = sanitize_title(suggested_title)

    # Check if title is a Windows reserved name
    if clean_title.upper() in WINDOWS_RESERVED:
        clean_title = f"{clean_title}-doc"

    # Enforce max 80 characters length
    # len(date_prefix) + len(clean_title) + len(ext) <= max_length
    available_title_len = max_length - len(date_prefix) - len(ext)
    if available_title_len < 3:
        # Extreme fallback if extension/date is unusually long
        available_title_len = 3

    truncated_title = clean_title[:available_title_len].rstrip("-")
    if not truncated_title:
        truncated_title = "doc"

    final_name = f"{date_prefix}{truncated_title}{ext}"
    return final_name


def resolve_collision(target_dir: Path, filename: str, max_length: int = 80) -> Path:
    """Resolve destination file collision by appending -2, -3, respecting 80-char limit."""
    target = target_dir / filename
    if not target.exists():
        return target

    p = Path(filename)
    stem = p.stem
    ext = p.suffix
    counter = 2

    while True:
        suffix_str = f"-{counter}"
        # Ensure stem + suffix_str + ext <= max_length
        allowed_stem_len = max_length - len(suffix_str) - len(ext)
        adjusted_stem = stem[:allowed_stem_len].rstrip("-")
        candidate_name = f"{adjusted_stem}{suffix_str}{ext}"
        candidate_path = target_dir / candidate_name
        if not candidate_path.exists():
            return candidate_path
        counter += 1


def decide_destination(
    source_path: Path,
    classification: ClassificationResult,
    config: ButlerConfig,
    is_duplicate: bool = False,
) -> tuple[Path, str, str]:
    """Decide final destination folder and filename.

    Returns:
        (target_path, action, reason)
        where action is one of: 'move', 'duplicate', 'review'
    """
    p = Path(source_path)
    output_dir = config.output_dir

    # 1. Duplicate
    if is_duplicate:
        dup_dir = output_dir / "Duplicates"
        dup_dir.mkdir(parents=True, exist_ok=True)
        target = resolve_collision(dup_dir, p.name)
        return target, "duplicate", "Duplicate file (matching sha256 already recorded)"

    # 2. Low confidence -> _Review/
    if classification.confidence < config.confidence_threshold:
        review_dir = output_dir / "_Review"
        review_dir.mkdir(parents=True, exist_ok=True)
        # Preserve original name in _Review to prevent confusing user
        target = resolve_collision(review_dir, p.name)
        return (
            target,
            "review",
            f"Confidence {classification.confidence:.2f} below threshold {config.confidence_threshold:.2f}",
        )

    # 3. High confidence -> Organized/<Category>/
    category = classification.category
    cat_dir = output_dir / category
    cat_dir.mkdir(parents=True, exist_ok=True)

    mtime = p.stat().st_mtime if p.exists() else None
    new_filename = build_filename(
        suggested_title=classification.suggested_name,
        original_suffix=p.suffix,
        doc_date=classification.document_date,
        file_mtime=mtime,
        max_length=80,
    )

    target = resolve_collision(cat_dir, new_filename, max_length=80)
    reason = f"Classified as {category} ({classification.document_type}, confidence {classification.confidence:.2f})"
    return target, "move", reason
