"""Image extractor using Tesseract OCR with VLM caption fallback."""

from __future__ import annotations

import base64
import logging
from pathlib import Path

import httpx
from PIL import Image

from butler.config import ButlerConfig
from butler.extractors import ExtractionResult

logger = logging.getLogger(__name__)


def ask_vision_model(image_path: Path, model: str) -> str:
    """Query local Ollama vision model for a concise caption."""
    try:
        with open(image_path, "rb") as f:
            b64_img = base64.b64encode(f.read()).decode("utf-8")

        payload = {
            "model": model,
            "prompt": "Describe this image in one concise factual sentence for file categorization.",
            "images": [b64_img],
            "stream": False,
        }

        # Bounded timeout for local VLM
        with httpx.Client(timeout=15.0) as client:
            resp = client.post("http://localhost:11434/api/generate", json=payload)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("response", "").strip()
    except Exception as e:
        logger.debug(f"Vision model caption call failed: {e}")
    return ""


def extract_image(path: Path, config: ButlerConfig) -> ExtractionResult:
    """Extract text from image using Tesseract OCR, falling back to VLM caption if minimal text."""
    ocr_text = ""
    warning = None

    # Step 1: Tesseract OCR
    try:
        import pytesseract
        with Image.open(path) as img:
            lang = config.extract.ocr_languages or "eng"
            ocr_text = pytesseract.image_to_string(img, lang=lang).strip()
    except Exception as e:
        warning = f"Tesseract OCR unavailable or failed: {e}"

    # Step 2: If fewer than 20 characters, ask vision model for caption
    caption = ""
    if len(ocr_text) < 20:
        caption = ask_vision_model(path, config.models.vision)

    combined_text = []
    if ocr_text:
        combined_text.append(ocr_text)
    if caption:
        combined_text.append(f"Visual Caption: {caption}")

    final_text = "\n\n".join(combined_text).strip()

    return ExtractionResult(
        text=final_text,
        metadata={
            "filename": path.name,
            "extension": path.suffix.lower(),
            "has_caption": bool(caption),
            "ocr_char_count": len(ocr_text),
        },
        warning=warning,
    )
