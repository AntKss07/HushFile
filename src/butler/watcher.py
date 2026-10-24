"""Directory watcher and sequential processing worker."""

from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from butler.config import ButlerConfig
from butler.journal import Journal
from butler.mover import process_file
from butler.stabilizer import wait_until_stable

logger = logging.getLogger(__name__)


class WatcherEventHandler(FileSystemEventHandler):
    """Filter incoming file events and enqueue eligible files."""

    def __init__(self, watcher: FolderWatcher) -> None:
        self.watcher = watcher

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self.watcher.enqueue(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self.watcher.enqueue(event.dest_path)


class FolderWatcher:
    """Manages directory observation and a single-threaded sequential worker queue."""

    def __init__(
        self,
        config: ButlerConfig,
        journal: Journal,
        indexer: Any = None,
        dry_run: bool = False,
    ) -> None:
        self.config = config
        self.journal = journal
        self.indexer = indexer
        self.dry_run = dry_run

        self.queue: queue.Queue[Path] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.observer = Observer()

    def is_eligible(self, path: Path) -> bool:
        """Check if file should be processed according to rules and ignore lists."""
        try:
            # Check hidden
            if path.name.startswith("."):
                return False

            # Check ignore extensions
            suffix = path.suffix.lower()
            if suffix in self.config.ignore_extensions:
                return False

            # Check if file is inside output_dir to prevent infinite move loops
            resolved_p = path.resolve()
            resolved_out = self.config.output_dir.resolve()
            try:
                resolved_p.relative_to(resolved_out)
                return False  # It is inside output_dir
            except ValueError:
                pass  # Not inside output_dir

            return True
        except Exception:
            return False

    def enqueue(self, file_path: str | Path) -> None:
        p = Path(file_path).resolve()
        if self.is_eligible(p):
            logger.info(f"Queued file: {p.name}")
            self.queue.put(p)

    def _worker_loop(self) -> None:
        """Sequential single worker: processes files one by one to protect VRAM."""
        current_batch_id: int | None = None

        while not self.stop_event.is_set():
            try:
                file_path = self.queue.get(timeout=1.0)
            except queue.Empty:
                if current_batch_id is not None:
                    self.journal.finish_batch(current_batch_id)
                    current_batch_id = None
                continue

            try:
                if current_batch_id is None:
                    current_batch_id = self.journal.start_batch(
                        trigger="watcher", dry_run=self.dry_run
                    )

                logger.info(f"Stabilizing: {file_path.name}")
                stable = wait_until_stable(
                    file_path,
                    stability_seconds=self.config.stability_seconds,
                    max_wait_seconds=600.0,
                )

                if not stable:
                    logger.warning(f"File did not stabilize, skipping: {file_path.name}")
                    continue

                logger.info(f"Processing: {file_path.name}")
                res = process_file(
                    file_path=file_path,
                    config=self.config,
                    journal=self.journal,
                    batch_id=current_batch_id,
                    dry_run=self.dry_run,
                    indexer=self.indexer,
                )
                logger.info(f"Result for {file_path.name}: {res.get('status')} -> {res.get('dst_path')}")

            except Exception as e:
                logger.error(f"Worker failed on {file_path.name}: {e}", exc_info=True)
            finally:
                self.queue.task_done()

        if current_batch_id is not None:
            self.journal.finish_batch(current_batch_id)

    def start(self) -> None:
        """Start watcher observer and processing worker."""
        # 1. Startup crash reconciliation
        reconciled = self.journal.reconcile_pending_moves()
        if reconciled:
            logger.info(f"Reconciled {len(reconciled)} pending moves from previous session")

        # 2. Start worker thread
        self.worker_thread.start()

        # 3. Schedule observer
        self.config.watch_dir.mkdir(parents=True, exist_ok=True)
        handler = WatcherEventHandler(self)
        self.observer.schedule(handler, str(self.config.watch_dir), recursive=False)
        self.observer.start()
        logger.info(f"Watching directory: {self.config.watch_dir}")

    def stop(self) -> None:
        """Stop observer and queue worker cleanly."""
        self.stop_event.set()
        self.observer.stop()
        self.observer.join(timeout=3.0)
        self.worker_thread.join(timeout=3.0)
        logger.info("Watcher stopped.")
