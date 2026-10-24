"""Audio extractor using faster-whisper."""

from __future__ import annotations

import logging
from pathlib import Path

from butler.config import ButlerConfig
from butler.extractors import ExtractionResult

logger = logging.getLogger(__name__)


def extract_audio(path: Path, config: ButlerConfig) -> ExtractionResult:
    """Extract speech transcription from audio file using faster-whisper."""
    try:
        from faster_whisper import WhisperModel

        device = config.models.device.lower()
        compute_type = "float16" if device == "cuda" else "int8"

        try:
            model = WhisperModel(config.models.whisper, device=device, compute_type=compute_type)
        except Exception:
            # Fall back to CPU int8 if CUDA fails
            model = WhisperModel(config.models.whisper, device="cpu", compute_type="int8")

        segments, info = model.transcribe(
            str(path),
            beam_size=1,
            language=None,  # auto-detect
            vad_filter=True,
        )

        texts = []
        max_duration = config.extract.audio_seconds or 300
        for segment in segments:
            texts.append(segment.text.strip())
            if segment.end >= max_duration:
                break

        full_transcript = " ".join(texts).strip()

        return ExtractionResult(
            text=full_transcript,
            metadata={
                "filename": path.name,
                "extension": path.suffix.lower(),
                "duration": getattr(info, "duration", 0),
                "language": getattr(info, "language", "unknown"),
            },
        )

    except Exception as e:
        logger.warning(f"Audio transcription skipped for {path.name}: {e}")
        return ExtractionResult(
            text="",
            metadata={"filename": path.name, "extension": path.suffix.lower()},
            warning=f"Audio transcription error: {e}",
        )
