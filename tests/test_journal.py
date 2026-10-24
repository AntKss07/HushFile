"""Tests for Journal crash recovery, batch recording, and deduplication."""

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
        "db_file": db_file,
    }


def test_batch_lifecycle(temp_env):
    journal: Journal = temp_env["journal"]
    b_id = journal.start_batch(trigger="manual", dry_run=False)
    assert b_id > 0
    b_data = journal.get_batch(b_id)
    assert b_data["dry_run"] == 0
    assert b_data["trigger"] == "manual"
    assert b_data["finished_at"] is None

    journal.finish_batch(b_id)
    b_data_finished = journal.get_batch(b_id)
    assert b_data_finished["finished_at"] is not None


def test_duplicate_file_detection(temp_env):
    journal: Journal = temp_env["journal"]
    file1 = temp_env["watch_dir"] / "test1.txt"
    file1.write_text("Unique content 12345", encoding="utf-8")
    h1 = compute_sha256(file1)

    fid1, is_dup1 = journal.get_or_create_file(h1, "test1.txt", str(file1), file1.stat().st_size)
    assert not is_dup1
    assert fid1 > 0

    # Same content, different name
    fid2, is_dup2 = journal.get_or_create_file(h1, "test2.txt", str(file1), file1.stat().st_size)
    assert is_dup2
    assert fid2 == fid1


def test_crash_recovery_file_intact_at_source(temp_env):
    """Crash simulation: move logged as pending, but crash happened before file was moved."""
    journal: Journal = temp_env["journal"]
    watch_file = temp_env["watch_dir"] / "doc.pdf"
    watch_file.write_text("PDF content", encoding="utf-8")
    target_file = temp_env["organized_dir"] / "Finance" / "2026-10-23_doc.pdf"
    h = compute_sha256(watch_file)

    b_id = journal.start_batch()
    fid, _ = journal.get_or_create_file(h, "doc.pdf", str(watch_file), watch_file.stat().st_size)
    mid = journal.record_move_pending(b_id, fid, str(watch_file), str(target_file), h, action="move")

    # Simulate crash before move (target_file does not exist, watch_file intact)
    reconciled = journal.reconcile_pending_moves()
    assert len(reconciled) == 1
    assert reconciled[0]["id"] == mid
    assert reconciled[0]["status"] == "failed"

    # Verify DB status
    history = journal.get_history()
    assert history[0]["status"] == "failed"
    assert "intact at source" in history[0]["reason"].lower()


def test_crash_recovery_file_already_at_destination(temp_env):
    """Crash simulation: move happened on disk, but process crashed before updating move status."""
    journal: Journal = temp_env["journal"]
    watch_file = temp_env["watch_dir"] / "doc.pdf"
    watch_file.write_text("PDF content 2", encoding="utf-8")
    target_file = temp_env["organized_dir"] / "Finance" / "2026-10-23_doc.pdf"
    h = compute_sha256(watch_file)

    b_id = journal.start_batch()
    fid, _ = journal.get_or_create_file(h, "doc.pdf", str(watch_file), watch_file.stat().st_size)
    mid = journal.record_move_pending(b_id, fid, str(watch_file), str(target_file), h, action="move")

    # Move happened on disk, then crash
    safe_filesystem_move(watch_file, target_file)

    reconciled = journal.reconcile_pending_moves()
    assert len(reconciled) == 1
    assert reconciled[0]["id"] == mid
    assert reconciled[0]["status"] == "done"

    history = journal.get_history()
    assert history[0]["status"] == "done"
