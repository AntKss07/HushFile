"""Pydantic schemas for classification and structured LLM output."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ClassificationResult(BaseModel):
    """Structured output schema for file categorization and metadata."""
    category: str = Field(
        ...,
        description="The category chosen from the allowed categories list."
    )
    suggested_name: str = Field(
        ...,
        description="A short descriptive title (3 to 8 words), in lowercase or plain text, without file extension or date."
    )
    document_type: str = Field(
        ...,
        description="Type of document, e.g. invoice, receipt, marksheet, lecture notes, contract, screenshot, audio recording."
    )
    summary: str = Field(
        ...,
        description="One or two sentence factual summary of the file content."
    )
    key_facts: list[str] = Field(
        default_factory=list,
        description="List of key dates, amounts, person or organization names found."
    )
    tags: list[str] = Field(
        default_factory=list,
        description="3 to 6 descriptive topical keywords."
    )
    document_date: str | None = Field(
        default="",
        description="ISO date (YYYY-MM-DD) if explicitly stated in the document, else empty string."
    )
    confidence: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="Confidence score between 0.0 and 1.0."
    )

    @field_validator("confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, v: Any) -> float:
        try:
            val = float(v)
            if val > 1.0:
                val = val / 100.0
            return max(0.0, min(1.0, val))
        except (ValueError, TypeError):
            return 0.5

    @field_validator("suggested_name")
    @classmethod
    def clean_suggested_name(cls, v: str) -> str:
        # Strip extension if accidentally included
        name = re.sub(r"\.[a-zA-Z0-9]{1,6}$", "", v.strip())
        # Strip leading dates like 2026-10-23
        name = re.sub(r"^\d{4}[-_]\d{2}[-_]\d{2}[-_]?", "", name).strip()
        return name or "unnamed-document"

    @field_validator("document_date")
    @classmethod
    def validate_date(cls, v: str | None) -> str:
        if not v:
            return ""
        # Match YYYY-MM-DD
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", v)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        return ""
