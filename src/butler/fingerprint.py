"""File fingerprinting and checksum utilities."""

from __future__ import annotations

import hashlib
from pathlib import Path


def compute_sha256(path: Path | str, chunk_size: int = 65536) -> str:
    """Compute sha256 checksum of a file in streaming chunks."""
    p = Path(path)
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()
