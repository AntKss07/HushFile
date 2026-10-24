"""File stabilization detector for in-progress downloads and writes."""

from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def wait_until_stable(
    file_path: Path | str,
    stability_seconds: float = 3.0,
    check_interval: float = 0.5,
    max_wait_seconds: float = 600.0,
) -> bool:
    """Block until a file has finished downloading or writing.

    A file is considered stable when:
    1. It exists and can be opened for reading.
    2. Its file size does not change for `stability_seconds`.

    Returns:
        True if the file stabilized within `max_wait_seconds`, False otherwise.
    """
    p = Path(file_path)
    start_time = time.time()
    last_size = -1
    last_change_time = time.time()

    while time.time() - start_time < max_wait_seconds:
        if not p.exists():
            time.sleep(check_interval)
            continue

        try:
            # Check if file can be opened for reading (not locked by browser/OS)
            with open(p, "rb") as f:
                f.read(1)

            current_size = p.stat().st_size

            if current_size != last_size:
                last_size = current_size
                last_change_time = time.time()
            else:
                # Size has been constant
                if time.time() - last_change_time >= stability_seconds:
                    return True

        except (OSError, PermissionError):
            # File is still locked by downloading process
            last_change_time = time.time()

        time.sleep(check_interval)

    logger.warning(
        f"File {p.name} failed to stabilize after {max_wait_seconds}s (last size: {last_size})"
    )
    return False
