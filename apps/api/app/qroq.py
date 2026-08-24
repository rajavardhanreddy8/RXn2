from __future__ import annotations

from .relations import extract_text


async def extract(source_text: str, source_url: str | None, model: str) -> dict:
    return await extract_text(
        source_text, source_url, provider="groq", model=model
    )
