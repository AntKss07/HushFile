"""Crash-safe SQLite undo journal for Downloads Butler."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from butler.db import get_db, init_db


def compute_sha256(path: Path | str, chunk_size: int = 65536) -> str:
    """Compute sha256 hash of a file in streaming chunks."""
    p = Path(path)
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def safe_filesystem_move(src: Path | str, dst: Path | str) -> Path:
    """Safely move a file.

    Uses atomic os.replace when on the same filesystem/drive.
    Falls back to copy + sha256 verification + remove for cross-filesystem moves.
    """
    src_p = Path(src).resolve()
    dst_p = Path(dst).resolve()
    dst_p.parent.mkdir(parents=True, exist_ok=True)

    # Check same filesystem / mount / drive
    src_drive = os.path.splitdrive(str(src_p))[0].lower()
    dst_drive = os.path.splitdrive(str(dst_p))[0].lower()

    if src_drive == dst_drive:
        try:
            os.replace(src_p, dst_p)
            return dst_p
        except OSError:
            pass  # Fall back to copy + verify + unlink

    # Cross-device move
    src_hash = compute_sha256(src_p)
    shutil.copy2(src_p, dst_p)
    dst_hash = compute_sha256(dst_p)
    if src_hash != dst_hash:
        if dst_p.exists():
            dst_p.unlink(missing_ok=True)
        raise OSError(f"Cross-device copy failed hash verification for {src_p} -> {dst_p}")
    src_p.unlink()
    return dst_p


class Journal:
    """Manages batches, write-ahead move logging, crash reconciliation, and undo operations."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        init_db(self.db_path)

    def start_batch(self, trigger: str = "watcher", dry_run: bool = False) -> int:
        """Start a new batch of operations."""
        now = datetime.now(UTC).isoformat()
        with get_db(self.db_path) as conn:
            cur = conn.execute(
                "INSERT INTO batches (started_at, trigger, dry_run) VALUES (?, ?, ?)",
                (now, trigger, 1 if dry_run else 0),
            )
            return cur.lastrowid  # type: ignore

    def finish_batch(self, batch_id: int) -> None:
        """Mark a batch as completed."""
        now = datetime.now(UTC).isoformat()
        with get_db(self.db_path) as conn:
            conn.execute(
                "UPDATE batches SET finished_at = ? WHERE id = ?",
                (now, batch_id),
            )

    def get_batch(self, batch_id: int) -> dict[str, Any] | None:
        """Retrieve batch metadata."""
        with get_db(self.db_path) as conn:
            row = conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
            return dict(row) if row else None

    def get_or_create_file(
        self,
        sha256: str,
        original_name: str,
        current_path: str,
        size_bytes: int,
        mime: str | None = None,
    ) -> tuple[int, bool]:
        """Find existing file by sha256 or insert new.

        Returns:
            (file_id, is_duplicate)
        """
        now = datetime.now(UTC).isoformat()
        with get_db(self.db_path) as conn:
            row = conn.execute(
                "SELECT id, current_path FROM files WHERE sha256 = ?", (sha256,)
            ).fetchone()
            if row:
                return int(row["id"]), True

            cur = conn.execute(
                """
                INSERT INTO files (sha256, original_name, current_path, size_bytes, mime, first_seen)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (sha256, original_name, str(current_path), size_bytes, mime or "", now),
            )
            return int(cur.lastrowid), False

    def find_file_by_sha256(self, sha256: str) -> dict[str, Any] | None:
        with get_db(self.db_path) as conn:
            row = conn.execute("SELECT * FROM files WHERE sha256 = ?", (sha256,)).fetchone()
            return dict(row) if row else None

    def record_move_pending(
        self,
        batch_id: int,
        file_id: int,
        src_path: str,
        dst_path: str,
        sha256: str,
        action: str = "move",
        reason: str | None = None,
        confidence: float | None = None,
        model: str | None = None,
    ) -> int:
        """Write-ahead: log move as pending before touching filesystem."""
        now = datetime.now(UTC).isoformat()
        with get_db(self.db_path) as conn:
            cur = conn.execute(
                """
                INSERT INTO moves (
                    batch_id, file_id, timestamp, src_path, dst_path,
                    sha256, action, status, reason, confidence, model
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    batch_id,
                    file_id,
                    now,
                    str(src_path),
                    str(dst_path),
                    sha256,
                    action,
                    reason,
                    confidence,
                    model,
                ),
            )
            return int(cur.lastrowid)

    def mark_move_done(self, move_id: int, final_dst_path: str | None = None) -> None:
        """Mark move status as done and update current_path in files."""
        with get_db(self.db_path) as conn:
            move = conn.execute("SELECT * FROM moves WHERE id = ?", (move_id,)).fetchone()
            if not move:
                return
            dst = final_dst_path or move["dst_path"]
            conn.execute(
                "UPDATE moves SET status = 'done', dst_path = ? WHERE id = ?",
                (str(dst), move_id),
            )
            conn.execute(
                "UPDATE files SET current_path = ? WHERE id = ?",
                (str(dst), move["file_id"]),
            )

    def mark_move_failed(self, move_id: int, reason: str) -> None:
        """Mark move status as failed with explanatory reason."""
        with get_db(self.db_path) as conn:
            conn.execute(
                "UPDATE moves SET status = 'failed', reason = ? WHERE id = ?",
                (reason, move_id),
            )

    def reconcile_pending_moves(self) -> list[dict[str, Any]]:
        """Reconcile unfinished moves after crash on startup."""
        reconciled = []
        with get_db(self.db_path) as conn:
            moves = conn.execute("SELECT * FROM moves WHERE status = 'pending'").fetchall()
            for m in moves:
                move_id = m["id"]
                src = Path(m["src_path"])
                dst = Path(m["dst_path"])
                expected_hash = m["sha256"]

                if dst.exists():
                    try:
                        actual_hash = compute_sha256(dst)
                        if actual_hash == expected_hash:
                            conn.execute(
                                "UPDATE moves SET status = 'done' WHERE id = ?", (move_id,)
                            )
                            conn.execute(
                                "UPDATE files SET current_path = ? WHERE id = ?",
                                (str(dst), m["file_id"]),
                            )
                            reconciled.append(
                                {"id": move_id, "status": "done", "reason": "File found at destination"}
                            )
                            continue
                    except Exception:
                        pass

                if src.exists():
                    conn.execute(
                        "UPDATE moves SET status = 'failed', reason = ? WHERE id = ?",
                        ("Crashed before move completed; file intact at source", move_id),
                    )
                    reconciled.append(
                        {"id": move_id, "status": "failed", "reason": "Intact at source"}
                    )
                else:
                    conn.execute(
                        "UPDATE moves SET status = 'failed', reason = ? WHERE id = ?",
                        ("File not found at source or destination after crash", move_id),
                    )
                    reconciled.append(
                        {"id": move_id, "status": "failed", "reason": "File missing"}
                    )
        return reconciled

    def undo(
        self,
        batch_id: int | None = None,
        move_id: int | None = None,
        force: bool = False,
    ) -> list[dict[str, Any]]:
        """Reverse moves.

        Args:
            batch_id: specific batch to reverse
            move_id: specific move to reverse
            force: if True, undo even if destination file hash changed

        Returns:
            list of result dicts for each move processed
        """
        results: list[dict[str, Any]] = []

        with get_db(self.db_path) as conn:
            if move_id is not None:
                moves = conn.execute(
                    "SELECT m.*, b.dry_run FROM moves m JOIN batches b ON m.batch_id = b.id WHERE m.id = ?",
                    (move_id,),
                ).fetchall()
                if not moves:
                    raise ValueError(f"Move ID {move_id} not found")
                if moves[0]["dry_run"]:
                    raise ValueError("Cannot undo a dry-run move")
            elif batch_id is not None:
                batch = conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
                if not batch:
                    raise ValueError(f"Batch ID {batch_id} not found")
                if batch["dry_run"]:
                    raise ValueError("Cannot undo a dry-run batch")
                moves = conn.execute(
                    "SELECT m.*, b.dry_run FROM moves m JOIN batches b ON m.batch_id = b.id WHERE m.batch_id = ? ORDER BY m.id DESC",
                    (batch_id,),
                ).fetchall()
            else:
                # Last non-dry batch with done moves
                row = conn.execute(
                    """
                    SELECT b.id FROM batches b
                    JOIN moves m ON m.batch_id = b.id
                    WHERE b.dry_run = 0 AND m.status = 'done'
                    ORDER BY b.id DESC LIMIT 1
                    """
                ).fetchone()
                if not row:
                    raise ValueError("No reversible non-dry batches found")
                target_batch_id = row["id"]
                moves = conn.execute(
                    "SELECT m.*, b.dry_run FROM moves m JOIN batches b ON m.batch_id = b.id WHERE m.batch_id = ? ORDER BY m.id DESC",
                    (target_batch_id,),
                ).fetchall()

            for m in moves:
                m_id = m["id"]
                if m["status"] != "done":
                    results.append(
                        {
                            "move_id": m_id,
                            "status": "skipped",
                            "reason": f"Move status is {m['status']}, not done",
                        }
                    )
                    continue

                dst_path = Path(m["dst_path"])
                src_path = Path(m["src_path"])
                expected_sha = m["sha256"]

                if not dst_path.exists():
                    results.append(
                        {
                            "move_id": m_id,
                            "status": "skipped",
                            "reason": f"Destination file not found: {dst_path}",
                        }
                    )
                    continue

                current_sha = compute_sha256(dst_path)
                if current_sha != expected_sha and not force:
                    results.append(
                        {
                            "move_id": m_id,
                            "status": "skipped",
                            "reason": f"File at {dst_path} was edited or modified (sha256 mismatch)",
                        }
                    )
                    continue

                # Determine restoration path if original src_path is occupied
                target_restore = src_path
                if target_restore.exists():
                    counter = 1
                    stem = src_path.stem
                    suffix = src_path.suffix
                    parent = src_path.parent
                    while target_restore.exists():
                        extra = " (restored)" if counter == 1 else f" (restored {counter})"
                        target_restore = parent / f"{stem}{extra}{suffix}"
                        counter += 1

                # Safely move back
                safe_filesystem_move(dst_path, target_restore)

                # Update database
                conn.execute(
                    "UPDATE moves SET status = 'undone' WHERE id = ?",
                    (m_id,),
                )
                conn.execute(
                    "UPDATE files SET current_path = ? WHERE id = ?",
                    (str(target_restore), m["file_id"]),
                )
                results.append(
                    {
                        "move_id": m_id,
                        "file_id": m["file_id"],
                        "status": "undone",
                        "restored_to": str(target_restore),
                    }
                )

        return results

    def save_card(
        self,
        file_id: int,
        category: str | None = None,
        document_type: str | None = None,
        suggested_name: str | None = None,
        summary: str | None = None,
        key_facts: list[str] | str | None = None,
        tags: list[str] | str | None = None,
        document_date: str | None = None,
        confidence: float | None = None,
        extracted_excerpt: str | None = None,
    ) -> None:
        """Upsert quick-info card for a file."""
        facts_str = json.dumps(key_facts) if isinstance(key_facts, list) else (key_facts or "[]")
        tags_str = json.dumps(tags) if isinstance(tags, list) else (tags or "[]")

        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO cards (
                    file_id, category, document_type, suggested_name,
                    summary, key_facts, tags, document_date, confidence, extracted_excerpt
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_id) DO UPDATE SET
                    category = excluded.category,
                    document_type = excluded.document_type,
                    suggested_name = excluded.suggested_name,
                    summary = excluded.summary,
                    key_facts = excluded.key_facts,
                    tags = excluded.tags,
                    document_date = excluded.document_date,
                    confidence = excluded.confidence,
                    extracted_excerpt = excluded.extracted_excerpt
                """,
                (
                    file_id,
                    category,
                    document_type,
                    suggested_name,
                    summary,
                    facts_str,
                    tags_str,
                    document_date,
                    confidence,
                    extracted_excerpt,
                ),
            )

    def get_card(self, identifier: str | int) -> dict[str, Any] | None:
        """Retrieve complete quick-info card by file id, current path, or original name."""
        with get_db(self.db_path) as conn:
            # Try file id
            file_row = None
            if isinstance(identifier, int) or (isinstance(identifier, str) and identifier.isdigit()):
                file_row = conn.execute(
                    "SELECT * FROM files WHERE id = ?", (int(identifier),)
                ).fetchone()

            if not file_row and isinstance(identifier, str):
                file_row = conn.execute(
                    "SELECT * FROM files WHERE current_path = ? OR original_name = ? ORDER BY id DESC LIMIT 1",
                    (str(identifier), Path(identifier).name),
                ).fetchone()

            if not file_row:
                return None

            file_id = file_row["id"]
            card_row = conn.execute("SELECT * FROM cards WHERE file_id = ?", (file_id,)).fetchone()
            latest_move = conn.execute(
                "SELECT * FROM moves WHERE file_id = ? ORDER BY id DESC LIMIT 1", (file_id,)
            ).fetchone()

            card_dict = dict(card_row) if card_row else {}
            move_dict = dict(latest_move) if latest_move else {}

            # Parse JSON lists safely
            for field in ["key_facts", "tags"]:
                raw = card_dict.get(field)
                if isinstance(raw, str):
                    try:
                        card_dict[field] = json.loads(raw)
                    except Exception:
                        card_dict[field] = [raw] if raw else []

            return {
                "file_id": file_id,
                "original_name": file_row["original_name"],
                "current_path": file_row["current_path"],
                "size_bytes": file_row["size_bytes"],
                "sha256": file_row["sha256"],
                "first_seen": file_row["first_seen"],
                "category": card_dict.get("category"),
                "document_type": card_dict.get("document_type"),
                "suggested_name": card_dict.get("suggested_name"),
                "summary": card_dict.get("summary"),
                "key_facts": card_dict.get("key_facts", []),
                "tags": card_dict.get("tags", []),
                "document_date": card_dict.get("document_date"),
                "confidence": card_dict.get("confidence") or move_dict.get("confidence"),
                "extracted_excerpt": card_dict.get("extracted_excerpt"),
                "latest_action": move_dict.get("action"),
                "latest_status": move_dict.get("status"),
                "reason": move_dict.get("reason"),
                "model": move_dict.get("model"),
                "src_path": move_dict.get("src_path"),
                "dst_path": move_dict.get("dst_path"),
            }

    def get_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """Get list of recent moves with file and batch details."""
        with get_db(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT m.*, f.original_name, b.dry_run, b.trigger
                FROM moves m
                JOIN files f ON m.file_id = f.id
                JOIN batches b ON m.batch_id = b.id
                ORDER BY m.id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_review_files(self) -> list[dict[str, Any]]:
        """Get files that are waiting in _Review."""
        with get_db(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT f.*, m.id as move_id, m.reason, m.confidence, m.model
                FROM files f
                JOIN moves m ON m.file_id = f.id
                WHERE m.action = 'review' AND m.status = 'done'
                ORDER BY m.id DESC
                """
            ).fetchall()
            return [dict(r) for r in rows]
