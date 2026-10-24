"""Tests for naming sanitization, collision handling, 80-char limits, and routing."""

from pathlib import Path

from butler.config import ButlerConfig
from butler.decision import (
    build_filename,
    decide_destination,
    resolve_collision,
    sanitize_title,
)
from butler.schemas import ClassificationResult


def test_sanitize_title_removes_illegal_characters():
    raw = 'My: Cool / Invoice * (Final) <v2> ?'
    cleaned = sanitize_title(raw)
    assert cleaned == "my-cool-invoice-final-v2"
    assert ":" not in cleaned
    assert "/" not in cleaned
    assert "*" not in cleaned


def test_sanitize_title_handles_unicode():
    raw = 'Reçû de Paiement Élémentaire'
    cleaned = sanitize_title(raw)
    assert "recu" in cleaned or "reçû" in cleaned
    assert " " not in cleaned


def test_reserved_windows_names():
    # CON, NUL, AUX, PRN should not be used raw
    filename = build_filename("con", ".txt", doc_date="2026-10-23")
    assert "con-doc.txt" in filename


def test_enforces_80_char_limit():
    long_title = "a" * 150
    filename = build_filename(long_title, ".pdf", doc_date="2026-10-23", max_length=80)
    assert len(filename) <= 80
    assert filename.endswith(".pdf")
    assert filename.startswith("2026-10-23_")


def test_preserves_extension():
    filename = build_filename("Tax Form W2", ".PDF", doc_date="2026-10-23")
    assert filename.endswith(".pdf")


def test_collision_resolution(tmp_path: Path):
    target_dir = tmp_path / "Finance"
    target_dir.mkdir()

    # Create file-1
    f1 = target_dir / "2026-10-23_invoice.pdf"
    f1.write_text("first")

    # Resolve collision
    resolved_2 = resolve_collision(target_dir, "2026-10-23_invoice.pdf")
    assert resolved_2.name == "2026-10-23_invoice-2.pdf"
    resolved_2.write_text("second")

    # Resolve again
    resolved_3 = resolve_collision(target_dir, "2026-10-23_invoice.pdf")
    assert resolved_3.name == "2026-10-23_invoice-3.pdf"


def test_routing_low_confidence_to_review(tmp_path: Path):
    cfg = ButlerConfig(output_dir=tmp_path / "Organized", confidence_threshold=0.6)
    src_file = tmp_path / "vague_file.pdf"
    src_file.write_text("data")

    res = ClassificationResult(
        category="Work",
        suggested_name="some notes",
        document_type="notes",
        summary="summary",
        confidence=0.45,  # below 0.6 threshold
    )

    target, action, reason = decide_destination(src_file, res, cfg, is_duplicate=False)
    assert action == "review"
    assert "_Review" in str(target.parent)
    assert target.name == "vague_file.pdf"


def test_routing_duplicate_to_duplicates(tmp_path: Path):
    cfg = ButlerConfig(output_dir=tmp_path / "Organized")
    src_file = tmp_path / "dup.pdf"
    src_file.write_text("data")

    res = ClassificationResult(
        category="Finance",
        suggested_name="invoice",
        document_type="invoice",
        summary="summary",
        confidence=0.9,
    )

    target, action, reason = decide_destination(src_file, res, cfg, is_duplicate=True)
    assert action == "duplicate"
    assert "Duplicates" in str(target.parent)


def test_routing_confident_move(tmp_path: Path):
    cfg = ButlerConfig(output_dir=tmp_path / "Organized", confidence_threshold=0.6)
    src_file = tmp_path / "bill.pdf"
    src_file.write_text("data")

    res = ClassificationResult(
        category="Finance",
        suggested_name="electric utility bill",
        document_type="bill",
        summary="Monthly power bill",
        document_date="2026-10-01",
        confidence=0.95,
    )

    target, action, reason = decide_destination(src_file, res, cfg, is_duplicate=False)
    assert action == "move"
    assert "Finance" in str(target.parent)
    assert target.name == "2026-10-01_electric-utility-bill.pdf"
