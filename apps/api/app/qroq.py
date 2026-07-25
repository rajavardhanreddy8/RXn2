from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import UTC, datetime

import httpx

from .db import transaction


SYSTEM_PROMPT = """You extract candidate chemistry facts from supplied public text.
Return JSON only with this schema:
{"compounds":[{"name":string,"smiles":string|null}],"reactions":[{"name":string,
"inputs":[string],"product":string,"yield_percent":number|null,"scale_g":number|null,
"evidence_quote":string}],"warnings":[string]}.
Do not infer missing values. Evidence quotes must occur verbatim in the supplied text.
All output is an unreviewed candidate and must never be described as validated."""


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def extract(source_text: str, source_url: str | None, model: str) -> dict:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("Qroq/Groq extraction is disabled: GROQ_API_KEY is not configured")
    job_id = str(uuid.uuid4())
    created_at = datetime.now(UTC).isoformat()
    with transaction() as db:
        db.execute(
            """INSERT INTO extraction_job
            (extraction_job_id, provider, model, prompt_sha256, input_sha256, source_url,
             status, review_status, created_at)
            VALUES (?, 'groq', ?, ?, ?, ?, 'queued', 'unreviewed', ?)""",
            (job_id, model, _sha(SYSTEM_PROMPT), _sha(source_text), source_url, created_at),
        )
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": source_text},
                    ],
                },
            )
            response.raise_for_status()
            raw = response.json()
        content = raw["choices"][0]["message"]["content"]
        candidate = json.loads(content)
        if not isinstance(candidate.get("compounds"), list) or not isinstance(candidate.get("reactions"), list):
            raise ValueError("provider output does not match the extraction schema")
        for reaction in candidate["reactions"]:
            quote = reaction.get("evidence_quote", "")
            if quote and quote not in source_text:
                raise ValueError("provider returned an evidence quote absent from the source text")
        completed = datetime.now(UTC).isoformat()
        with transaction() as db:
            db.execute(
                """UPDATE extraction_job SET response_sha256 = ?, raw_response_json = ?,
                   token_cost_json = ?, status = 'needs_review', review_status = 'needs_review',
                   completed_at = ? WHERE extraction_job_id = ?""",
                (_sha(content), json.dumps(raw), json.dumps(raw.get("usage", {})), completed, job_id),
            )
        return {"extraction_job_id": job_id, "review_status": "needs_review", "candidate": candidate}
    except Exception as error:
        with transaction() as db:
            db.execute(
                """UPDATE extraction_job SET status = 'failed', raw_response_json = ?,
                   completed_at = ? WHERE extraction_job_id = ?""",
                (json.dumps({"error": str(error)}), datetime.now(UTC).isoformat(), job_id),
            )
        raise

