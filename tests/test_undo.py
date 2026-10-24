"""Tests for undo operations: normal, batch, dry-run refusal, edited conflict, and occupied-source."""

from pathlib import Path

import pytest

from butler.journal import Journal, compute_sha256, safe_filesystem_move


@pytest.fixture
def temp_env(tmp_path: Path):
    db_file = tmp_path / "test_butler.db"
    watch_dir = tmp_path / "watch"
    organized_dir = tmp_path / "organized"
    watch_dir.mkdir()
    organized_dir.mkdir()
    journal = Journal(db_file)
    return {
        "journal": journal,
        "watch_dir": watch_dir,
        "organized_dir": organized_dir,
    }


def test_normal_undo(temp_env):
    journal: Journal = temp_env["journal"]
    src = temp_env["watch_dir"] / "test_file.txt"
    src.write_text("Hello World", encoding="utf-8")
    dst = temp_env["organized_dir"] / "Misc" / "2026-10-23_hello.txt"
    h = compute_sha256(src)

    b_id = journal.start_batch()
    fid, _ = journal.get_or_create_file(h, "test_file.txt", str(src), src.stat().st_size)
    mid = journal.record_move_pending(b_id, fid, str(src), str(dst), h)

    safe_filesystem_move(src, dst)
    journal.mark_move_done(mid, str(dst))
    journal.finish_batch(b_id)

    assert not src.exists()
    assert dst.exists()

    # Undo
    res = journal.undo(batch_id=b_id)
    assert len(res) == 1
    assert res[0]["status"] == "undone"
    assert src.exists()
    assert not dst.exists()
    assert src.read_text(encoding="utf-8") == "Hello World"


def test_refuse_undo_dry_run(temp_env):
    journal: Journal = temp_env["journal"]
    b_id = journal.start_batch(dry_run=True)
    journal.finish_batch(b_id)

    with pytest.raises(ValueError, match="Cannot undo a dry-run batch"):
        journal.undo(batch_id=b_id)


def test_undo_edited_file_conflict(temp_env):
    journal: Journal = temp_env["journal"]
    src = temp_env["watch_dir"] / "invoice.txt"
    src.write_text("Amount: 100", encoding="utf-8")
    dst = temp_env["organized_dir"] / "Finance" / "2026-10-23_invoice.txt"
    h = compute_sha256(src)

    b_id = journal.start_batch()
    fid, _ = journal.get_or_create_file(h, "invoice.txt", str(src), src.stat().st_size)
    mid = journal.record_move_pending(b_id, fid, str(src), str(dst), h)
    safe_filesystem_move(src, dst)
    journal.mark_move_done(mid, str(dst))
    journal.finish_batch(b_id)

    # User modifies destination file
    dst.write_text("Amount: 200 (edited by user)", encoding="utf-8")

    # Undo without force should skip
    res = journal.undo(batch_id=b_id, force=False)
    assert len(res) == 1
    assert res[0]["status"] == "skipped"
    assert dst.exists()
    assert not src.exists()

    # Undo with force should succeed
    res_force = journal.undo(batch_id=b_id, force=True)
    assert len(res_force) == 1
    assert res_force[0]["status"] == "undone"
    assert src.exists()
    assert not dst.exists()
    assert "edited by user" in src.read_text(encoding="utf-8")


def test_undo_occupied_source_restore(temp_env):
    """If original source path is now occupied, restore as name (restored).ext without overwriting."""
    journal: Journal = temp_env["journal"]
    src = temp_env["watch_dir"] / "notes.txt"
    src.write_text("Original Notes", encoding="utf-8")
    dst = temp_env["organized_dir"] / "Misc" / "2026-10-23_notes.txt"
    h = compute_sha256(src)

    b_id = journal.start_batch()
    fid, _ = journal.get_or_create_file(h, "notes.txt", str(src), src.stat().st_size)
    mid = journal.record_move_pending(b_id, fid, str(src), str(dst), h)
    safe_filesystem_move(src, dst)
    journal.mark_move_done(mid, str(dst))
    journal.finish_batch(b_id)

    # Now someone creates a NEW file at the original src location
    src.write_text("New incoming notes occupying src", encoding="utf-8")

    # Perform undo
    res = journal.undo(batch_id=b_id)
    assert len(res) == 1
    assert res[0]["status"] == "undone"

    expected_restored = temp_env["watch_dir"] / "notes (restored).txt"
    assert expected_restored.exists()
    assert expected_restored.read_text(encoding="utf-8") == "Original Notes"
    # Ensure newly created file at src was NOT overwritten
    assert src.read_text(encoding="utf-8") == "New incoming notes occupying src"
