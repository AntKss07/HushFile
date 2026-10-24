"""File processing pipeline: fingerprinting, extraction, classification, and safe move execution."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from butler.classifier import classify_file
from butler.config import ButlerConfig
from butler.decision import decide_destination
from butler.extractors import extract_content
from butler.journal import Journal, compute_sha256, safe_filesystem_move

logger = logging.getLogger(__name__)


def process_file(
    file_path: Path | str,
    config: ButlerConfig,
    journal: Journal,
    batch_id: int,
    dry_run: bool = False,
    indexer: Any = None,
) -> dict[str, Any]:
    """Execute end-to-end processing pipeline on a single file."""
    p = Path(file_path).resolve()
    if not p.exists() or not p.is_file():
        return {
            "src_path": str(p),
            "status": "failed",
            "reason": f"File does not exist: {p}",
        }

    try:
        # 1. Fingerprint
        file_sha = compute_sha256(p)
        file_size = p.stat().st_size
        mime = f"application/{p.suffix.lstrip('.')}" if p.suffix else "application/octet-stream"

        file_id, is_duplicate = journal.get_or_create_file(
            sha256=file_sha,
            original_name=p.name,
            current_path=str(p),
            size_bytes=file_size,
            mime=mime,
        )

        # Handle Duplicate
        if is_duplicate:
            from butler.schemas import ClassificationResult

            dummy_clf = ClassificationResult(
                category="Duplicates",
                suggested_name=p.stem,
                document_type="duplicate",
                summary="Duplicate file content detected by SHA256",
                confidence=1.0,
            )
            target_path, action, reason = decide_destination(p, dummy_clf, config, is_duplicate=True)

            if dry_run:
                return {
                    "file_id": file_id,
                    "src_path": str(p),
                    "dst_path": str(target_path),
                    "action": action,
                    "status": "dry_run",
                    "reason": reason,
                    "confidence": 1.0,
                    "model": "fingerprint:sha256",
                }

            move_id = journal.record_move_pending(
                batch_id=batch_id,
                file_id=file_id,
                src_path=str(p),
                dst_path=str(target_path),
                sha256=file_sha,
                action=action,
                reason=reason,
                confidence=1.0,
                model="fingerprint:sha256",
            )
            safe_filesystem_move(p, target_path)
            journal.mark_move_done(move_id, str(target_path))
            return {
                "file_id": file_id,
                "move_id": move_id,
                "src_path": str(p),
                "dst_path": str(target_path),
                "action": action,
                "status": "done",
                "reason": reason,
                "confidence": 1.0,
            }

        # 2. Extract content
        extraction = extract_content(p, config)

        # 3. Classify
        classification, model_used = classify_file(p, extraction, config)

        # 4. Decide destination
        target_path, action, reason = decide_destination(p, classification, config, is_duplicate=False)

        if dry_run:
            return {
                "file_id": file_id,
                "src_path": str(p),
                "dst_path": str(target_path),
                "action": action,
                "status": "dry_run",
                "reason": reason,
                "category": classification.category,
                "suggested_name": classification.suggested_name,
                "confidence": classification.confidence,
                "model": model_used,
            }

        # 5. Write-ahead move pending
        move_id = journal.record_move_pending(
            batch_id=batch_id,
            file_id=file_id,
            src_path=str(p),
            dst_path=str(target_path),
            sha256=file_sha,
            action=action,
            reason=reason,
            confidence=classification.confidence,
            model=model_used,
        )

        try:
            # 6. Execute safe filesystem move
            safe_filesystem_move(p, target_path)
            journal.mark_move_done(move_id, str(target_path))

            # 7. Upsert quick-info card
            journal.save_card(
                file_id=file_id,
                category=classification.category,
                document_type=classification.document_type,
                suggested_name=classification.suggested_name,
                summary=classification.summary,
                key_facts=classification.key_facts,
                tags=classification.tags,
                document_date=classification.document_date,
                confidence=classification.confidence,
                extracted_excerpt=(extraction.text or "")[:1000],
            )

            # 8. Index into vector database if indexer provided
            if indexer is not None:
                try:
                    indexer.index_document(
                        file_id=file_id,
                        summary=classification.summary,
                        text_excerpt=(extraction.text or "")[:1500],
                        category=classification.category,
                        tags=classification.tags,
                        current_path=str(target_path),
                    )
                except Exception as idx_err:
                    logger.warning(f"Failed to index file {file_id}: {idx_err}")

            return {
                "file_id": file_id,
                "move_id": move_id,
                "src_path": str(p),
                "dst_path": str(target_path),
                "action": action,
                "status": "done",
                "category": classification.category,
                "suggested_name": classification.suggested_name,
                "reason": reason,
                "confidence": classification.confidence,
                "model": model_used,
            }

        except Exception as move_err:
            journal.mark_move_failed(move_id, f"Filesystem move error: {move_err}")
            raise

    except Exception as e:
        logger.error(f"Error processing {p.name}: {e}", exc_info=True)
        return {
            "src_path": str(p),
            "status": "failed",
            "reason": str(e),
        }
