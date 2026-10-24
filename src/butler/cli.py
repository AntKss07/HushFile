"""Command Line Interface for HushFile (Typer + Rich)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from butler.config import ButlerConfig, load_config
from butler.digest import get_recent_digest, render_info_card
from butler.indexer import DocumentIndexer
from butler.journal import Journal
from butler.mover import process_file
from butler.search import search_documents
from butler.watcher import FolderWatcher

app = typer.Typer(
    name="hushfile",
    help="HushFile - Drop it in Downloads, forget it, find it later.",
    add_completion=False,
)
console = Console()


def get_services(config_path: str | None = None) -> tuple[ButlerConfig, Journal, DocumentIndexer]:
    cfg = load_config(config_path)
    journal = Journal(cfg.db_path)
    indexer = DocumentIndexer(cfg)
    return cfg, journal, indexer


@app.command()
def start(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to butler.yaml"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Log planned actions without moving files"),
) -> None:
    """Run the folder watcher in the foreground."""
    cfg, journal, indexer = get_services(config)
    console.print(
        Panel(
            f"[bold green]Starting HushFile[/bold green]\n"
            f"[cyan]Watching:[/cyan] {cfg.watch_dir}\n"
            f"[cyan]Output:[/cyan] {cfg.output_dir}\n"
            f"[cyan]Mode:[/cyan] {'DRY RUN' if dry_run else cfg.mode}\n"
            f"[dim]Press Ctrl+C to stop.[/dim]",
            border_style="green",
        )
    )

    watcher = FolderWatcher(cfg, journal, indexer=indexer, dry_run=dry_run)
    try:
        watcher.start()
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopping Butler...[/yellow]")
        watcher.stop()
        console.print("[green]Butler stopped safely.[/green]")


@app.command()
def scan(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to butler.yaml"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show planned moves without changing anything"),
) -> None:
    """Scan and organize everything currently in the watched folder once."""
    cfg, journal, indexer = get_services(config)
    watch_path = cfg.watch_dir

    if not watch_path.exists():
        console.print(f"[red]Watch directory does not exist:[/red] {watch_path}")
        raise typer.Exit(1)

    # Reconcile pending moves from previous crashes
    reconciled = journal.reconcile_pending_moves()
    if reconciled:
        console.print(f"[yellow]Reconciled {len(reconciled)} pending moves from earlier crashes.[/yellow]")

    files_to_process = [
        f for f in watch_path.iterdir()
        if f.is_file() and not f.name.startswith(".") and f.suffix.lower() not in cfg.ignore_extensions
    ]

    if not files_to_process:
        console.print(f"[green]No new files to process in {watch_path}[/green]")
        return

    console.print(
        f"[bold]Found {len(files_to_process)} files to process {'(DRY RUN)' if dry_run else ''}...[/bold]"
    )

    batch_id = journal.start_batch(trigger="scan", dry_run=dry_run)
    results = []

    for f in files_to_process:
        console.print(f"  [dim]Processing {f.name}...[/dim]")
        res = process_file(
            file_path=f,
            config=cfg,
            journal=journal,
            batch_id=batch_id,
            dry_run=dry_run,
            indexer=indexer if not dry_run else None,
        )
        results.append(res)

    journal.finish_batch(batch_id)

    # Render results table
    table = Table(title="Butler Scan Results", show_lines=True)
    table.add_column("Original File", style="cyan")
    table.add_column("Action", style="yellow")
    table.add_column("Category", style="magenta")
    table.add_column("Destination", style="green")
    table.add_column("Status", style="bold")

    for r in results:
        orig = Path(r.get("src_path", "")).name
        dest = Path(r.get("dst_path", "")).name if r.get("dst_path") else "-"
        status_style = "green" if r.get("status") in ["done", "dry_run"] else "red"
        table.add_row(
            orig,
            r.get("action", "-"),
            r.get("category", "-"),
            dest,
            f"[{status_style}]{r.get('status')}[/{status_style}]",
        )

    console.print(table)


@app.command()
def undo(
    batch: int | None = typer.Option(None, "--batch", "-b", help="Undo a specific batch ID"),
    move_id: int | None = typer.Option(None, "--id", "-i", help="Undo a specific move ID"),
    force: bool = typer.Option(False, "--force", "-f", help="Force undo even if file hash changed"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to butler.yaml"),
) -> None:
    """Reverse the most recent batch or a specific move."""
    cfg, journal, indexer = get_services(config)

    try:
        results = journal.undo(batch_id=batch, move_id=move_id, force=force)
    except ValueError as e:
        console.print(f"[red]Undo refused:[/red] {e}")
        raise typer.Exit(1)

    if not results:
        console.print("[yellow]No moves were undone.[/yellow]")
        return

    table = Table(title="Undo Execution Log")
    table.add_column("Move ID", style="cyan")
    table.add_column("Status", style="bold")
    table.add_column("Restored Location / Detail", style="green")

    for r in results:
        status_col = (
            "[green]undone[/green]"
            if r.get("status") == "undone"
            else f"[yellow]{r.get('status')}[/yellow]"
        )
        detail = r.get("restored_to") or r.get("reason", "")
        table.add_row(str(r.get("move_id")), status_col, detail)

        # Remove from vector index if undone
        if r.get("status") == "undone" and r.get("file_id"):
            indexer.remove_document(r["file_id"])

    console.print(table)


@app.command()
def history(
    limit: int = typer.Option(20, "--limit", "-n", help="Number of records to show"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to butler.yaml"),
) -> None:
    """Display history of file moves and journal actions."""
    cfg, journal, _ = get_services(config)
    moves = journal.get_history(limit=limit)

    if not moves:
        console.print("[dim]No move history recorded yet.[/dim]")
        return

    table = Table(title=f"Recent File Operations (Last {limit})", show_lines=True)
    table.add_column("ID", style="dim", width=4)
    table.add_column("Batch", width=6)
    table.add_column("Original Name", style="cyan")
    table.add_column("Action", style="yellow")
    table.add_column("Status", style="bold")
    table.add_column("Destination", style="green")
    table.add_column("Timestamp", style="dim")

    for m in moves:
        status_color = "green" if m["status"] == "done" else "red" if m["status"] == "failed" else "yellow"
        dst_name = Path(m["dst_path"]).name if m["dst_path"] else "-"
        dry_flag = " (dry)" if m.get("dry_run") else ""
        table.add_row(
            str(m["id"]),
            f"{m['batch_id']}{dry_flag}",
            m["original_name"],
            m["action"],
            f"[{status_color}]{m['status']}[/{status_color}]",
            dst_name,
            m["timestamp"][:19].replace("T", " "),
        )

    console.print(table)


@app.command()
def info(
    file_identifier: str = typer.Argument(..., help="File ID, original filename, or current path"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to butler.yaml"),
) -> None:
    """Show Quick-Info card for a file."""
    cfg, journal, _ = get_services(config)
    card = journal.get_card(file_identifier)
    if not card:
        console.print(f"[red]File record not found for:[/red] {file_identifier}")
        raise typer.Exit(1)

    render_info_card(card, console)


@app.command()
def find(
    query: str = typer.Argument(..., help="Natural language search query"),
    doc_type: str | None = typer.Option(None, "--type", "-t", help="Filter by category or type"),
    since: str | None = typer.Option(None, "--since", "-s", help="Filter files since date (YYYY-MM-DD)"),
    limit: int = typer.Option(5, "--limit", "-n", help="Max results to return"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to butler.yaml"),
) -> None:
    """Search organized files using semantic meaning."""
    cfg, _, indexer = get_services(config)
    hits = search_documents(
        query=query,
        config=cfg,
        limit=limit,
        doc_type=doc_type,
        since=since,
        indexer=indexer,
    )

    if not hits:
        console.print(f"[yellow]No matches found for:[/yellow] '{query}'")
        return

    table = Table(title=f"Semantic Search Results: '{query}'", show_lines=True)
    table.add_column("Similarity", style="bold green", width=10)
    table.add_column("Category", style="cyan", width=12)
    table.add_column("Filename / Path", style="bright_white")
    table.add_column("Summary", style="white")

    for h in hits:
        sim_pct = f"{h['similarity'] * 100:.1f}%"
        filename = Path(h["current_path"]).name
        table.add_row(sim_pct, h["category"], filename, h["summary"])

    console.print(table)


@app.command()
def recent(
    since: str | None = typer.Option(None, "--since", "-s", help="Start date (YYYY-MM-DD)"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to butler.yaml"),
) -> None:
    """Display activity digest of organized files."""
    cfg, journal, _ = get_services(config)
    digest = get_recent_digest(journal, since_date=since)

    console.print(
        Panel(
            f"[bold cyan]HushFile - Activity Digest ({digest['since']})[/bold cyan]\n"
            f"[yellow]Duplicates Filtered:[/yellow] {digest['duplicate_count']}\n"
            f"[magenta]Sent to _Review:[/magenta] {digest['review_count']}\n"
            f"[red]Errors / Failures:[/red] {digest['failure_count']}",
            border_style="cyan",
        )
    )

    if digest["category_counts"]:
        cat_table = Table(title="Files by Category")
        cat_table.add_column("Category", style="cyan")
        cat_table.add_column("Count", style="bold")
        for cat, cnt in digest["category_counts"].items():
            cat_table.add_row(cat, str(cnt))
        console.print(cat_table)

    if digest["recent_files"]:
        rec_table = Table(title="Latest Handled Files")
        rec_table.add_column("File", style="bright_white")
        rec_table.add_column("Category", style="magenta")
        rec_table.add_column("Destination", style="green")
        rec_table.add_column("Status", style="bold")
        for r in digest["recent_files"]:
            dst_name = Path(r.get("dst_path", "")).name if r.get("dst_path") else "-"
            rec_table.add_row(r["original_name"], r.get("category", "-"), dst_name, r["status"])
        console.print(rec_table)


@app.command()
def review(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to butler.yaml"),
) -> None:
    """List files currently waiting in _Review folder."""
    cfg, journal, _ = get_services(config)
    rev_files = journal.get_review_files()

    if not rev_files:
        console.print("[green]The _Review queue is empty! All files filed with high confidence.[/green]")
        return

    table = Table(title=f"Files Waiting in _Review ({len(rev_files)} total)", show_lines=True)
    table.add_column("File ID", width=6)
    table.add_column("Filename", style="cyan")
    table.add_column("Confidence", style="yellow")
    table.add_column("Reason", style="white")

    for f in rev_files:
        table.add_row(
            str(f["id"]),
            f["original_name"],
            f"{f.get('confidence', 0.0):.2f}",
            f.get("reason", "-"),
        )
    console.print(table)


@app.command()
def doctor(
    config: str | None = typer.Option(None, "--config", "-c", help="Path to butler.yaml"),
) -> None:
    """Check Ollama, configured models, Tesseract, GPU, and directory permissions."""
    cfg = load_config(config)
    console.print("[bold]Running HushFile Doctor System Diagnostics...[/bold]\n")

    all_passed = True

    # 1. Ollama Connectivity
    ollama_ok = False
    installed_models: list[str] = []
    try:
        import httpx
        resp = httpx.get("http://localhost:11434/api/tags", timeout=5.0)
        if resp.status_code == 200:
            ollama_ok = True
            installed_models = [m.get("name", "") for m in resp.json().get("models", [])]
            console.print("  [green][OK][/green] Ollama service reachable at http://localhost:11434")
        else:
            console.print(f"  [red][FAIL][/red] Ollama responded with HTTP {resp.status_code}")
    except Exception as e:
        console.print(f"  [red][FAIL][/red] Ollama is not running: {e}")
        console.print("    [yellow]Fix:[/yellow] Start Ollama desktop or run 'ollama serve' in terminal.")
        all_passed = False

    # 2. Configured Models Pulled
    if ollama_ok:
        for model_role, model_name in [
            ("Text", cfg.models.text),
            ("Embeddings", cfg.models.embed),
            ("Vision", cfg.models.vision),
        ]:
            # Exact or tag match
            match = any(m == model_name or m.startswith(f"{model_name}:") or model_name.startswith(f"{m}:") for m in installed_models)
            if match:
                console.print(f"  [green][OK][/green] {model_role} model pulled: {model_name}")
            else:
                console.print(f"  [yellow][!][/yellow] {model_role} model not found: '{model_name}'")
                console.print(f"    [yellow]Fix:[/yellow] Run: ollama pull {model_name}")

    # 3. Tesseract OCR
    tess_path = shutil.which("tesseract")
    if not tess_path and os.name == "nt":
        # Check standard Windows installation directory
        default_win_tess = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
        if default_win_tess.exists():
            tess_path = str(default_win_tess)

    if tess_path:
        console.print(f"  [green][OK][/green] Tesseract OCR found at: {tess_path}")
    else:
        console.print("  [yellow][!][/yellow] Tesseract OCR not found in PATH")
        console.print("    [yellow]Fix on Windows:[/yellow] Download installer from https://github.com/UB-Mannheim/tesseract/wiki and add to PATH.")

    # 4. GPU Detection
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            check=True,
        )
        gpu_info = proc.stdout.strip()
        console.print(f"  [green][OK][/green] Dedicated GPU detected: {gpu_info}")
    except Exception:
        console.print("  [dim][i][/dim] NVIDIA GPU not detected via nvidia-smi (will use CPU mode)")

    # 5. Paths & Write Permissions
    for label, path in [
        ("Watch Directory", cfg.watch_dir),
        ("Output Directory", cfg.output_dir),
        ("Database File", cfg.db_path),
    ]:
        if path:
            p = Path(path)
            try:
                if p.is_file() or p.suffix:
                    p.parent.mkdir(parents=True, exist_ok=True)
                else:
                    p.mkdir(parents=True, exist_ok=True)
                test_file = (p if p.is_dir() else p.parent) / ".butler_write_test"
                test_file.touch()
                test_file.unlink()
                console.print(f"  [green][OK][/green] {label} writable: {p}")
            except Exception as e:
                console.print(f"  [red][FAIL][/red] {label} cannot be written: {e}")
                all_passed = False

    if all_passed:
        console.print("\n[bold green]Diagnostics complete: Butler environment is healthy![/bold green]")
    else:
        console.print("\n[bold yellow]Diagnostics complete: Please resolve warnings above.[/bold yellow]")


@app.command()
def ui(
    port: int = typer.Option(8501, "--port", "-p", help="Port to run Streamlit on"),
) -> None:
    """Launch the Streamlit web dashboard."""
    app_script = Path(__file__).parent / "ui" / "app.py"
    console.print(f"[bold cyan]Launching HushFile Web Dashboard on port {port}...[/bold cyan]")
    cmd = [sys.executable, "-m", "streamlit", "run", str(app_script), "--server.port", str(port)]
    subprocess.run(cmd)


@app.command()
def bench(
    dataset_dir: str | None = typer.Option(None, "--dataset", "-d", help="Path to benchmark dataset"),
    labels_file: str | None = typer.Option(None, "--labels", "-l", help="Path to labels.csv"),
    models: str | None = typer.Option(None, "--models", "-m", help="Comma-separated models to evaluate"),
    cpu: bool = typer.Option(False, "--cpu", help="Run benchmark in CPU-only mode"),
) -> None:
    """Run model benchmark on labeled test dataset."""
    # Ensure workspace root is in sys.path
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from bench.run_bench import run_benchmark
    models_list = [m.strip() for m in models.split(",")] if models else None
    run_benchmark(dataset_dir=dataset_dir, labels_file=labels_file, models=models_list, cpu_only=cpu)


if __name__ == "__main__":
    app()
