"""PDF extractor using PyMuPDF with OCR fallback."""

from __future__ import annotations

import io
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image

from butler.config import ButlerConfig
from butler.extractors import ExtractionResult


def extract_pdf(path: Path, config: ButlerConfig) -> ExtractionResult:
    """Extract text from PDF with automatic OCR fallback for scanned pages."""
    try:
        doc = fitz.open(str(path))
        num_pages = len(doc)
        total_text: list[str] = []
        scanned_pages: list[int] = []

        # Extract text page by page
        for page_idx in range(min(num_pages, 20)):  # Check up to 20 pages
            page = doc[page_idx]
            page_text = page.get_text()
            if len(page_text.strip()) < 200 and page_idx < 3:
                scanned_pages.append(page_idx)
            total_text.append(page_text)

        full_extracted = "\n\n".join(t for t in total_text if t.strip())

        # If early pages had fewer than 200 chars, attempt OCR on up to 3 pages
        ocr_text: list[str] = []
        ocr_warning: str | None = None

        if scanned_pages:
            try:
                import pytesseract
                for p_idx in scanned_pages[:3]:
                    page = doc[p_idx]
                    pix = page.get_pixmap(dpi=150)
                    img = Image.open(io.BytesIO(pix.tobytes("png")))
                    lang = config.extract.ocr_languages or "eng"
                    t = pytesseract.image_to_string(img, lang=lang)
                    if t.strip():
                        ocr_text.append(t.strip())
            except Exception as ocr_err:
                ocr_warning = f"OCR fallback skipped: {ocr_err}"

        # Combine text: prefer full_extracted if rich, or append OCR
        combined_parts = []
        if full_extracted:
            combined_parts.append(full_extracted)
        if ocr_text:
            combined_parts.append("\n--- OCR Text ---\n" + "\n\n".join(ocr_text))

        final_text = "\n\n".join(combined_parts).strip()
        if len(final_text) > config.extract.max_chars:
            final_text = final_text[:config.extract.max_chars]

        doc.close()

        return ExtractionResult(
            text=final_text,
            metadata={
                "filename": path.name,
                "extension": ".pdf",
                "page_count": num_pages,
                "scanned_pages_detected": len(scanned_pages),
            },
            warning=ocr_warning,
        )

    except Exception as e:
        return ExtractionResult(
            text="",
            metadata={"filename": path.name, "extension": ".pdf"},
            warning=f"PDF extraction error: {e}",
        )
