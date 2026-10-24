"""Tests for file stabilizer."""

import threading
import time
from pathlib import Path

from butler.stabilizer import wait_until_stable


def test_stabilizer_waits_for_growing_file(tmp_path: Path):
    test_file = tmp_path / "downloading.pdf"
    test_file.write_bytes(b"initial")

    # Background thread writes more chunks over 1.2 seconds
    def append_chunks():
        for i in range(3):
            time.sleep(0.3)
            with open(test_file, "ab") as f:
                f.write(b" additional_bytes_" + str(i).encode())

    writer = threading.Thread(target=append_chunks)
    writer.start()

    start_wait = time.time()
    # Expect to stabilize with stability_seconds = 0.8s
    stable = wait_until_stable(
        test_file,
        stability_seconds=0.8,
        check_interval=0.1,
        max_wait_seconds=5.0,
    )
    duration = time.time() - start_wait
    writer.join()

    assert stable is True
    # Must have waited at least ~1.5s total (during writing + stability window)
    assert duration >= 1.2
