"""End-to-end integration test demonstrating the Definition of Done."""

from pathlib import Path
from unittest.mock import patch

import fitz
import pytest

from butler.config import ButlerConfig
from butler.digest import get_recent_digest
from butler.indexer import DocumentIndexer
from butler.journal import Journal
from butler.mover import process_file
from butler.search import search_documents


@pytest.fixture
def e2e_env(tmp_path: Path):
    watch_dir = tmp_path / "Downloads"
    output_dir = tmp_path / "Organized"
    db_file = tmp_path / "test_butler.db"
    watch_dir.mkdir()
    output_dir.mkdir()

    cfg = ButlerConfig(
        watch_dir=watch_dir,
        output_dir=output_dir,
        db_path=db_file,
    )
    journal = Journal(db_file)
    indexer = DocumentIndexer(cfg, persist_dir=tmp_path / "chroma_e2e")
    return {
        "cfg": cfg,
        "journal": journal,
        "indexer": indexer,
        "watch_dir": watch_dir,
        "output_dir": output_dir,
    }


def test_end_to_end_lifecycle(e2e_env):
    cfg: ButlerConfig = e2e_env["cfg"]
    journal: Journal = e2e_env["journal"]
    indexer: DocumentIndexer = e2e_env["indexer"]
    watch_dir: Path = e2e_env["watch_dir"]
    e2e_env["output_dir"]

    # 1. Create a PDF invoice in watch folder
    pdf_file = watch_dir / "acme_cloud_invoice.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "ACME Cloud Services Invoice #9910\nAmount: $120.00\nDate: 2026-10-23")
    doc.save(str(pdf_file))
    doc.close()

    # 2. Create a screenshot note in watch folder
    img_file = watch_dir / "screenshot_server_error.txt"
    img_file.write_text("Screenshot capture: 502 Bad Gateway server configuration error", encoding="utf-8")

    batch_id = journal.start_batch(trigger="test_scan")

    # Mock Ollama embedding so Chroma search works offline in tests
    mock_vec = [0.1] * 768

    with patch("butler.indexer.get_embedding", return_value=mock_vec), \
         patch("butler.search.get_embedding", return_value=mock_vec):

        # Process file 1 (PDF Invoice)
        res1 = process_file(
            file_path=pdf_file,
            config=cfg,
            journal=journal,
            batch_id=batch_id,
            dry_run=False,
            indexer=indexer,
        )
        assert res1["status"] == "done"
        assert res1["category"] == "Finance"
        assert Path(res1["dst_path"]).exists()
        assert not pdf_file.exists()

        # Process file 2 (Screenshot / Error log)
        res2 = process_file(
            file_path=img_file,
            config=cfg,
            journal=journal,
            batch_id=batch_id,
            dry_run=False,
            indexer=indexer,
        )
        assert res2["status"] == "done"
        assert Path(res2["dst_path"]).exists()
        assert not img_file.exists()

        journal.finish_batch(batch_id)

        # 3. Check History
        history = journal.get_history(limit=10)
        assert len(history) == 2
        assert all(h["status"] == "done" for h in history)

        # 4. Check Info Card
        card = journal.get_card(res1["file_id"])
        assert card is not None
        assert card["category"] == "Finance"
        assert card["original_name"] == "acme_cloud_invoice.pdf"

        # 5. Check Semantic Search
        hits = search_documents("cloud server invoice", cfg, limit=5, indexer=indexer)
        assert len(hits) > 0

        # 6. Check Digest
        digest = get_recent_digest(journal)
        assert digest["category_counts"]["Finance"] >= 1

        # 7. Reverse batch (Undo)
        undo_results = journal.undo(batch_id=batch_id)
        assert len(undo_results) == 2
        assert all(u["status"] == "undone" for u in undo_results)

        # Verify files are restored back to original watch_dir
        assert pdf_file.exists()
        assert img_file.exists()
        assert not Path(res1["dst_path"]).exists()
        assert not Path(res2["dst_path"]).exists()
