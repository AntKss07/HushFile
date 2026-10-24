"""Benchmark runner comparing local models on the labeled test dataset."""

from __future__ import annotations

import csv
import subprocess
import time
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from butler.classifier import classify_file
from butler.config import ButlerConfig
from butler.extractors import extract_content

console = Console()


def get_current_vram_mb() -> float:
    """Read GPU memory usage via nvidia-smi."""
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            check=True,
        )
        val = proc.stdout.strip().split("\n")[0]
        return float(val)
    except Exception:
        return 0.0


def run_benchmark(
    dataset_dir: str | Path | None = None,
    labels_file: str | Path | None = None,
    models: list[str] | None = None,
    cpu_only: bool = False,
    results_path: str | Path | None = None,
) -> Path:
    """Run benchmark evaluating models on accuracy, latency, review share, and VRAM."""
    base_bench = Path(__file__).parent
    ds_dir = Path(dataset_dir) if dataset_dir else base_bench / "dataset"
    lbl_file = Path(labels_file) if labels_file else base_bench / "labels.csv"
    res_file = Path(results_path) if results_path else base_bench / "results.md"

    # Auto-generate synthetic dataset if missing
    if not ds_dir.exists() or not lbl_file.exists():
        from bench.generate_synthetic import generate_synthetic_dataset
        generate_synthetic_dataset(ds_dir)

    # Read ground truth labels
    labeled_samples: list[dict[str, str]] = []
    with open(lbl_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        labeled_samples = list(reader)

    if not labeled_samples:
        console.print("[red]No labeled samples found in labels.csv[/red]")
        return res_file

    # Models to test: default to installed / configured or comparison models
    target_models = models or ["qwen2.5:7b-instruct", "llama3.2:3b", "rule-fallback"]
    if cpu_only:
        console.print("[bold yellow]Running benchmark in CPU-only mode[/bold yellow]")

    bench_results: list[dict[str, Any]] = []

    for model_name in target_models:
        console.print(f"\n[bold cyan]Evaluating Model:[/bold cyan] {model_name} ({'CPU' if cpu_only else 'CUDA/Auto'})")

        cfg = ButlerConfig()
        cfg.models.text = model_name
        if cpu_only:
            cfg.models.device = "cpu"

        correct_count = 0
        review_count = 0
        total_time = 0.0
        peak_vram = 0.0

        for sample in labeled_samples:
            fname = sample["filename"]
            expected = sample["expected_category"]
            fpath = ds_dir / fname

            if not fpath.exists():
                continue

            vram_start = get_current_vram_mb()
            t0 = time.time()

            # Extraction + Classification (No Move)
            ext = extract_content(fpath, cfg)
            clf, _ = classify_file(fpath, ext, cfg)

            elapsed = time.time() - t0
            vram_end = get_current_vram_mb()

            total_time += elapsed
            peak_vram = max(peak_vram, vram_end, vram_start)

            is_correct = clf.category.lower() == expected.lower()
            if is_correct:
                correct_count += 1
            if clf.confidence < cfg.confidence_threshold:
                review_count += 1

        total_samples = len(labeled_samples)
        accuracy = (correct_count / total_samples) * 100 if total_samples else 0.0
        review_share = (review_count / total_samples) * 100 if total_samples else 0.0
        avg_sec = total_time / total_samples if total_samples else 0.0

        bench_results.append(
            {
                "model": model_name,
                "device": "CPU" if cpu_only else "CUDA",
                "accuracy": f"{accuracy:.1f}%",
                "avg_sec": f"{avg_sec:.2f}s",
                "review_share": f"{review_share:.1f}%",
                "peak_vram": f"{peak_vram:.0f} MB" if peak_vram > 0 else "N/A",
            }
        )

    # Print summary table in terminal
    table = Table(title="HushFile Model Benchmark Results", show_lines=True)
    table.add_column("Model", style="bold cyan")
    table.add_column("Device", style="yellow")
    table.add_column("Category Accuracy", style="bold green")
    table.add_column("Avg Latency / File", style="white")
    table.add_column("Routed to _Review", style="magenta")
    table.add_column("Peak VRAM", style="blue")

    for r in bench_results:
        table.add_row(
            r["model"],
            r["device"],
            r["accuracy"],
            r["avg_sec"],
            r["review_share"],
            r["peak_vram"],
        )
    console.print(table)

    # Write bench/results.md
    md_lines = [
        "# HushFile — Model Benchmark Results",
        "",
        "Published benchmark comparing local open-weight models on a labeled test set across categories (Finance, Work, Education, Health, Travel, Receipts, Code, Screenshots, Archives).",
        "",
        "| Model | Device | Category Accuracy | Mean Time / File | Share to _Review | Peak VRAM |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in bench_results:
        md_lines.append(
            f"| `{r['model']}` | {r['device']} | **{r['accuracy']}** | {r['avg_sec']} | {r['review_share']} | {r['peak_vram']} |"
        )
    md_lines.append("")
    md_lines.append("### Key Takeaways:")
    md_lines.append("1. **Privacy & Offline:** Runs 100% locally with zero cloud API dependencies.")
    md_lines.append("2. **Graceful Safety:** Low-confidence documents route securely to `_Review/` rather than misfiling.")
    md_lines.append("3. **CPU Fallback:** CPU-only mode provides dependable organization without requiring a dedicated GPU.")

    res_file.write_text("\n".join(md_lines), encoding="utf-8")
    console.print(f"[green]Saved benchmark report to {res_file}[/green]")
    return res_file


if __name__ == "__main__":
    run_benchmark()
