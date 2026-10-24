"""Quick-info card formatter and daily activity digest generator."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from butler.db import get_db
from butler.journal import Journal


def get_recent_digest(journal: Journal, since_date: str | None = None) -> dict[str, Any]:
    """Compile activity statistics and recent file list."""
    since = since_date or datetime.now(UTC).strftime("%Y-%m-%d")

    with get_db(journal.db_path) as conn:
        # Category counts
        cat_rows = conn.execute(
            """
            SELECT c.category, COUNT(*) as cnt
            FROM moves m
            JOIN cards c ON m.file_id = c.file_id
            WHERE m.status = 'done' AND m.timestamp >= ?
            GROUP BY c.category
            ORDER BY cnt DESC
            """,
            (since,),
        ).fetchall()
        category_counts = {r["category"]: r["cnt"] for r in cat_rows if r["category"]}

        # Review count
        rev_row = conn.execute(
            """
            SELECT COUNT(*) as cnt FROM moves
            WHERE action = 'review' AND status = 'done' AND timestamp >= ?
            """,
            (since,),
        ).fetchone()
        review_count = rev_row["cnt"] if rev_row else 0

        # Duplicate count
        dup_row = conn.execute(
            """
            SELECT COUNT(*) as cnt FROM moves
            WHERE action = 'duplicate' AND status = 'done' AND timestamp >= ?
            """,
            (since,),
        ).fetchone()
        duplicate_count = dup_row["cnt"] if dup_row else 0

        # Failure count
        fail_row = conn.execute(
            """
            SELECT COUNT(*) as cnt FROM moves
            WHERE status = 'failed' AND timestamp >= ?
            """,
            (since,),
        ).fetchone()
        failure_count = fail_row["cnt"] if fail_row else 0

        # 5 most recent moves
        recent_moves = conn.execute(
            """
            SELECT m.*, f.original_name, f.size_bytes, c.category, c.summary
            FROM moves m
            JOIN files f ON m.file_id = f.id
            LEFT JOIN cards c ON f.id = c.file_id
            WHERE m.timestamp >= ?
            ORDER BY m.id DESC LIMIT 5
            """,
            (since,),
        ).fetchall()

        return {
            "since": since,
            "category_counts": category_counts,
            "review_count": review_count,
            "duplicate_count": duplicate_count,
            "failure_count": failure_count,
            "recent_files": [dict(r) for r in recent_moves],
        }


def render_info_card(card: dict[str, Any], console: Console | None = None) -> None:
    """Render a Quick-info card using Rich Panel and Tables."""
    c = console or Console()

    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("Field", style="bold cyan", width=18)
    table.add_column("Value", style="white")

    table.add_row("File ID", str(card.get("file_id", "N/A")))
    table.add_row("Original Name", str(card.get("original_name", "N/A")))
    table.add_row("Current Path", str(card.get("current_path", "N/A")))
    size_kb = (card.get("size_bytes") or 0) / 1024
    table.add_row("Size", f"{size_kb:.1f} KB")
    table.add_row("Document Type", str(card.get("document_type", "Unknown")))
    table.add_row("Summary", str(card.get("summary", "No summary available")))

    facts = card.get("key_facts") or []
    if isinstance(facts, list) and facts:
        table.add_row("Key Facts", ", ".join(facts))

    tags = card.get("tags") or []
    if isinstance(tags, list) and tags:
        table.add_row("Tags", ", ".join(f"#{t}" for t in tags))

    conf = card.get("confidence")
    conf_str = f"{conf:.2f}" if conf is not None else "N/A"
    table.add_row("Category", f"{card.get('category', 'Misc')} (confidence: {conf_str})")
    table.add_row("Model / Engine", str(card.get("model", "N/A")))
    table.add_row("Filing Reason", str(card.get("reason", "N/A")))

    actions_text = "[bold green]Actions:[/bold green] butler undo --id " + str(card.get("file_id")) + " | open in folder"
    panel = Panel(
        table,
        title="[bold blue]HushFile - Quick-Info Card[/bold blue]",
        subtitle=actions_text,
        border_style="bright_blue",
        padding=(1, 2),
    )
    c.print(panel)
