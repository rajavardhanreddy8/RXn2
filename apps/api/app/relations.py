from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from datetime import UTC, datetime
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .chemistry import validate_provisional_reaction
from .db import connect, transaction


Role = Literal["consumed", "produced", "reagent", "catalyst", "solvent", "workup"]
FactType = Literal["condition", "quantity", "outcome"]
ProviderMode = Literal["auto", "groq", "openrouter", "huggingface"]


class MaterialRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    surface_text: str = Field(min_length=1, max_length=500)
    role: Role
    evidence_quote: str = Field(min_length=1, max_length=5000)
    explicit: bool
    uncertain: bool
    confidence: float = Field(ge=0, le=1)


class TextFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_type: FactType
    value_text: str = Field(min_length=1, max_length=1000)
    evidence_quote: str = Field(min_length=1, max_length=5000)
    explicit: bool
    uncertain: bool
    confidence: float = Field(ge=0, le=1)


class RelationExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    procedure_type: Literal[
        "performed", "referenced", "analytical", "purification", "ambiguous"
    ]
    materials: list[MaterialRelation] = Field(max_length=100)
    facts: list[TextFact] = Field(max_length=100)
    conflicts: list[str] = Field(max_length=20)


SYSTEM_PROMPT = """Extract only explicit facts from one public patent procedure.
Return the required JSON schema and nothing else.
Material surface_text, fact value_text, and evidence_quote must be verbatim substrings.
Classify the block as performed only when it describes an actually executed procedure.
Do not infer identities, structures, SMILES, InChI, reaction SMILES, missing quantities,
or missing products. Mark uncertainty and internal contradictions. A mention is not proof
of a reaction role. All results are provisional and can never approve chemistry."""
SCHEMA_VERSION = "rxn2-relation-v1"
RELATION_SCHEMA = RelationExtraction.model_json_schema()
PROVIDER_LIMIT = int(os.getenv("RELATION_PROVIDER_CONCURRENCY", "4"))
_PROVIDER_SEMAPHORES = {
    "groq": asyncio.Semaphore(PROVIDER_LIMIT),
    "openrouter": asyncio.Semaphore(PROVIDER_LIMIT),
    "huggingface": asyncio.Semaphore(PROVIDER_LIMIT),
}

PROVIDERS = {
    "groq": {
        "key": "GROQ_API_KEY",
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "model": os.getenv("RELATION_GROQ_MODEL", "openai/gpt-oss-20b"),
    },
    "openrouter": {
        "url": "https://openrouter.ai/api/v1/chat/completions",
    },
    "huggingface": {
        "key": "HF_TOKEN",
        "url": "https://router.huggingface.co/v1/chat/completions",
        "model": os.getenv("RELATION_HF_MODEL", "Qwen/Qwen3-32B:featherless-ai"),
    },
}
OPENROUTER_KEY_NAMES = ("OPENROUTER_API_KEY", "op_api_key")
DEFAULT_OPENROUTER_MODELS = (
    "z-ai/glm-5.2:free,"
    "nvidia/nemotron-3-ultra-550b-a55b:free,"
    "google/gemma-4-31b-it:free"
)
STRICT_SCHEMA_MODELS = {"z-ai/glm-5.2:free"}
FALLBACK_OUTPUT_CONTRACT = """
Return one JSON object with exactly these keys: procedure_type, materials, facts, conflicts.
procedure_type is one of performed, referenced, analytical, purification, ambiguous.
Each material is {surface_text, role, evidence_quote, explicit, uncertain, confidence};
role is consumed, produced, reagent, catalyst, solvent, or workup.
Each fact is {fact_type, value_text, evidence_quote, explicit, uncertain, confidence};
fact_type is condition, quantity, or outcome. Use [] when no explicit items exist.
"""

def openrouter_key() -> str | None:
    """Use one configured OpenRouter project key; never rotate keys for quota."""
    return next((os.getenv(name) for name in OPENROUTER_KEY_NAMES if os.getenv(name)), None)


def openrouter_models(model: str | None = None) -> list[str]:
    if model:
        return [model]
    configured = os.getenv("RELATION_OPENROUTER_MODELS")
    if configured is None:
        configured = os.getenv("RELATION_OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODELS)
    return [value.strip() for value in configured.split(",") if value.strip()]


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_id(prefix: str, *parts: object) -> str:
    digest = sha256_text("\x1f".join(str(part) for part in parts))[:24]
    return f"{prefix}:{digest}"


def request_payload(provider: str, model: str, source_text: str) -> dict:
    strict_schema = provider in {"groq", "huggingface"} or model in STRICT_SCHEMA_MODELS
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": int(os.getenv("RELATION_MAX_OUTPUT_TOKENS", "2048")),
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT if strict_schema else SYSTEM_PROMPT + FALLBACK_OUTPUT_CONTRACT},
            {"role": "user", "content": source_text},
        ],
        "response_format": (
            {
                "type": "json_schema",
                "json_schema": {
                    "name": SCHEMA_VERSION,
                    "strict": True,
                    "schema": RELATION_SCHEMA,
                },
            }
            if strict_schema
            else {"type": "json_object"}
        ),
    }
    if provider == "openrouter":
        payload["reasoning"] = {"effort": "none"}
    elif provider == "groq":
        payload["reasoning_effort"] = "low"
    if provider == "openrouter" and os.getenv("RELATION_OPENROUTER_DATA_COLLECTION", "deny") == "deny":
        payload["provider"] = {
            "require_parameters": True,
            "data_collection": "deny",
            "allow_fallbacks": True,
        }
    return payload


def provider_specs(mode: ProviderMode, model: str | None = None) -> list[tuple[str, str]]:
    names = ["openrouter", "groq"] if mode == "auto" else [mode]
    configured = []
    for name in names:
        spec = PROVIDERS[name]
        if name == "openrouter" and openrouter_key():
            configured.extend((name, selected_model) for selected_model in openrouter_models(model))
        elif name == "groq" and os.getenv(spec["key"]):
            configured.append((name, model or spec["model"]))
        elif name == "huggingface" and os.getenv(spec["key"]):
            configured.append((name, model or spec["model"]))
    if not configured:
        expected = " or ".join(
            "OPENROUTER_API_KEY (or op_api_key)" if name == "openrouter" else PROVIDERS[name]["key"]
            for name in names
        )
        raise RuntimeError(f"Relation extraction is disabled: configure {expected}")
    for name, selected_model in configured:
        if name == "openrouter" and not (
            selected_model.endswith(":free") or selected_model == "openrouter/free"
        ):
            raise RuntimeError("Paid OpenRouter models are disabled; use a :free model")
    return configured


def _cached_candidate(job_id: str) -> tuple[dict, RelationExtraction] | None:
    with connect() as db:
        row = db.execute(
            """SELECT raw_response_json FROM extraction_job
               WHERE extraction_job_id=? AND status IN ('completed', 'needs_review')""",
            (job_id,),
        ).fetchone()
    if not row or not row["raw_response_json"]:
        return None
    saved = json.loads(row["raw_response_json"])
    return saved.get("provider_response", {}), RelationExtraction.model_validate(
        saved["candidate"]
    )


async def call_provider(
    provider: str,
    model: str,
    source_text: str,
    source_url: str | None,
) -> tuple[str, dict, RelationExtraction]:
    spec = PROVIDERS[provider]
    key = openrouter_key() if provider == "openrouter" else os.getenv(spec["key"])
    if not key:
        expected = "OPENROUTER_API_KEY (or op_api_key)" if provider == "openrouter" else spec["key"]
        raise RuntimeError(f"{expected} is not configured")
    prompt_sha = sha256_text(SYSTEM_PROMPT + "\n" + SCHEMA_VERSION)
    input_sha = sha256_text(source_text)
    job_id = stable_id("extraction-job", provider, model, prompt_sha, input_sha)
    cached = _cached_candidate(job_id)
    if cached:
        raw, candidate = cached
        return job_id, raw, candidate

    now = datetime.now(UTC).isoformat()
    with transaction() as db:
        db.execute(
            """INSERT INTO extraction_job
               (extraction_job_id, provider, model, prompt_sha256, input_sha256,
                source_url, status, review_status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'queued', 'unreviewed', ?)
               ON CONFLICT(extraction_job_id) DO UPDATE SET
                 status='queued', raw_response_json=NULL, completed_at=NULL""",
            (job_id, provider, model, prompt_sha, input_sha, source_url, now),
        )

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if provider == "openrouter":
        headers.update({"HTTP-Referer": "https://github.com/rajavardhanreddy8/RXn2", "X-Title": "RXN2"})
    last_error: Exception | None = None
    try:
        async with _PROVIDER_SEMAPHORES[provider]:
            async with httpx.AsyncClient(timeout=float(os.getenv("RELATION_TIMEOUT_SECONDS", "60"))) as client:
                for attempt in range(4):
                    try:
                        response = await client.post(
                            spec["url"],
                            headers=headers,
                            json=request_payload(provider, model, source_text),
                        )
                        if response.status_code == 429 or response.status_code >= 500:
                            retry_after = response.headers.get("Retry-After")
                            delay = min(float(retry_after), 30) if retry_after else 2**attempt
                            if attempt < 3:
                                await asyncio.sleep(delay)
                                continue
                        response.raise_for_status()
                        raw = response.json()
                        content = raw["choices"][0]["message"]["content"]
                        candidate = RelationExtraction.model_validate_json(content)
                        saved = {
                            "provider_response": raw,
                            "candidate": candidate.model_dump(),
                        }
                        with transaction() as db:
                            db.execute(
                                """UPDATE extraction_job SET response_sha256=?,
                                   raw_response_json=?, token_cost_json=?,
                                   status='needs_review', review_status='needs_review',
                                   completed_at=? WHERE extraction_job_id=?""",
                                (
                                    sha256_text(content),
                                    json.dumps(saved, ensure_ascii=False),
                                    json.dumps(raw.get("usage", {})),
                                    datetime.now(UTC).isoformat(),
                                    job_id,
                                ),
                            )
                        return job_id, raw, candidate
                    except (httpx.HTTPError, KeyError, ValueError) as error:
                        last_error = error
                        if attempt < 3:
                            await asyncio.sleep(2**attempt)
                            continue
                        raise
    except Exception as error:
        with transaction() as db:
            db.execute(
                """UPDATE extraction_job SET status='failed', raw_response_json=?,
                   completed_at=? WHERE extraction_job_id=?""",
                (
                    json.dumps({"error": str(error)}),
                    datetime.now(UTC).isoformat(),
                    job_id,
                ),
            )
        raise last_error or error


async def extract_text(
    source_text: str,
    source_url: str | None = None,
    provider: ProviderMode = "auto",
    model: str | None = None,
) -> dict:
    errors = []
    for name, selected_model in provider_specs(provider, model):
        try:
            job_id, _, candidate = await call_provider(
                name, selected_model, source_text, source_url
            )
            return {
                "extraction_job_id": job_id,
                "provider": name,
                "model": selected_model,
                "review_status": "needs_review",
                "candidate": candidate.model_dump(),
            }
        except Exception as error:
            errors.append(f"{name}: {error}")
    raise RuntimeError("All configured relation providers failed: " + "; ".join(errors))


def _positions(text: str, value: str) -> list[int]:
    return [match.start() for match in re.finditer(re.escape(value), text)]


def locate_verbatim(source_text: str, value: str, quote: str) -> tuple[int, int]:
    quote_positions = _positions(source_text, quote)
    if len(quote_positions) != 1:
        raise ValueError("evidence_quote_not_unique_in_source")
    value_positions = _positions(quote, value)
    if len(value_positions) != 1:
        raise ValueError("value_not_unique_in_evidence_quote")
    start = quote_positions[0] + value_positions[0]
    return start, start + len(value)


def _normalized_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def resolve_compound(
    db,
    evidence_span_id: str,
    surface_text: str,
    start: int | None,
    end: int | None,
) -> tuple[str | None, list[str]]:
    if start is not None and end is not None:
        rows = db.execute(
            """SELECT DISTINCT object_compound_id FROM relation_candidate
               WHERE evidence_span_id=? AND predicate='mentions'
                 AND object_char_start=? AND object_char_end=?
                 AND object_compound_id IS NOT NULL""",
            (evidence_span_id, start, end),
        ).fetchall()
        ids = sorted({row[0] for row in rows})
        if len(ids) == 1:
            return ids[0], ids

    rows = db.execute(
        """SELECT compound_id FROM compound
           WHERE preferred_name IS NOT NULL AND lower(trim(preferred_name))=lower(trim(?))
             AND review_status <> 'rejected'
           UNION
           SELECT dc.compound_id FROM drug_alias a
           JOIN drug_compound dc USING (drug_id)
           JOIN compound c ON c.compound_id=dc.compound_id
           WHERE lower(trim(a.alias))=lower(trim(?))
             AND dc.review_status <> 'rejected' AND c.review_status <> 'rejected'""",
        (surface_text, surface_text),
    ).fetchall()
    ids = sorted({row[0] for row in rows})
    if len(ids) == 1:
        return ids[0], ids
    normalized = _normalized_name(surface_text)
    if not ids and normalized:
        rows = db.execute(
            """SELECT DISTINCT dc.compound_id FROM drug_alias a
               JOIN drug_compound dc USING (drug_id)
               JOIN compound c ON c.compound_id=dc.compound_id
               WHERE a.normalized_alias=? AND dc.review_status <> 'rejected'
                 AND c.review_status <> 'rejected'""",
            (normalized,),
        ).fetchall()
        ids = sorted({row[0] for row in rows})
    return (ids[0] if len(ids) == 1 else None), ids


def insert_relation(db, record: dict) -> None:
    columns = (
        "relation_candidate_id", "extraction_job_id", "evidence_span_id",
        "subject_type", "subject_text", "subject_char_start", "subject_char_end",
        "subject_compound_id", "predicate", "object_type", "object_text",
        "object_char_start", "object_char_end", "object_compound_id",
        "attributes_json", "model_confidence", "is_explicit",
        "validation_status", "validation_reason", "review_status", "created_at",
    )
    db.execute(
        f"INSERT OR REPLACE INTO relation_candidate ({','.join(columns)}) "
        f"VALUES ({','.join('?' for _ in columns)})",
        tuple(record.get(column) for column in columns),
    )


def _relation_record(
    job_id: str,
    evidence_span_id: str,
    index: str,
    **values,
) -> dict:
    return {
        "relation_candidate_id": stable_id(
            "relation-candidate", job_id, evidence_span_id, index
        ),
        "extraction_job_id": job_id,
        "evidence_span_id": evidence_span_id,
        "subject_char_start": None,
        "subject_char_end": None,
        "subject_compound_id": None,
        "object_char_start": None,
        "object_char_end": None,
        "object_compound_id": None,
        "attributes_json": "{}",
        "model_confidence": 1.0,
        "is_explicit": 1,
        "validation_status": "unresolved",
        "validation_reason": "requires_review",
        "review_status": "needs_review",
        "created_at": datetime.now(UTC).isoformat(),
        **values,
    }


def persist_candidate(
    evidence_span_id: str, job_id: str, candidate: RelationExtraction
) -> dict:
    with transaction() as db:
        evidence = db.execute(
            """SELECT evidence_span_id, publication_number, evidence_text
               FROM evidence_span WHERE evidence_span_id=?""",
            (evidence_span_id,),
        ).fetchone()
        if not evidence:
            raise ValueError(f"Unknown evidence_span_id: {evidence_span_id}")
        db.execute(
            "DELETE FROM relation_candidate WHERE extraction_job_id=? AND evidence_span_id=?",
            (job_id, evidence_span_id),
        )
        procedure_text = f"procedure:{evidence_span_id}"
        disallowed = candidate.procedure_type in {
            "referenced", "analytical", "purification"
        }
        describes_status = "rejected" if disallowed else (
            "validated" if candidate.procedure_type == "performed" and not candidate.conflicts
            else "unresolved"
        )
        insert_relation(
            db,
            _relation_record(
                job_id,
                evidence_span_id,
                "describes",
                subject_type="patent",
                subject_text=evidence["publication_number"],
                predicate="describes",
                object_type="procedure",
                object_text=procedure_text,
                attributes_json=json.dumps({
                    "procedure_type": candidate.procedure_type,
                    "conflicts": candidate.conflicts,
                }),
                validation_status=describes_status,
                validation_reason=(
                    "non_performed_procedure" if disallowed else
                    "explicit_performed_procedure" if describes_status == "validated" else
                    "ambiguous_or_conflicting_procedure"
                ),
                review_status="rejected" if disallowed else "needs_review",
            ),
        )

        for index, material in enumerate(candidate.materials):
            try:
                start, end = locate_verbatim(
                    evidence["evidence_text"], material.surface_text, material.evidence_quote
                )
                offset_error = None
            except ValueError as error:
                start = end = None
                offset_error = str(error)
            compound_id, candidate_ids = resolve_compound(
                db, evidence_span_id, material.surface_text, start, end
            )
            if offset_error or disallowed:
                status, reason = "rejected", offset_error or "non_performed_procedure"
            elif not material.explicit or material.uncertain or candidate.conflicts:
                status, reason = "unresolved", "uncertain_or_conflicting_relation"
            elif not compound_id:
                status, reason = "unresolved", "compound_identity_unresolved"
            else:
                status, reason = "validated", "exact_evidence_and_unique_compound"
            common = {
                "predicate": material.role,
                "attributes_json": json.dumps({
                    "evidence_quote": material.evidence_quote,
                    "uncertain": material.uncertain,
                    "candidate_compound_ids": candidate_ids,
                    "conflicts": candidate.conflicts,
                }),
                "model_confidence": material.confidence,
                "is_explicit": int(material.explicit),
                "validation_status": status,
                "validation_reason": reason,
                "review_status": "rejected" if status == "rejected" else "needs_review",
            }
            if material.role == "produced":
                common.update({
                    "subject_type": "procedure", "subject_text": procedure_text,
                    "object_type": "compound", "object_text": material.surface_text,
                    "object_char_start": start, "object_char_end": end,
                    "object_compound_id": compound_id,
                })
            else:
                common.update({
                    "subject_type": "compound", "subject_text": material.surface_text,
                    "subject_char_start": start, "subject_char_end": end,
                    "subject_compound_id": compound_id,
                    "object_type": "procedure", "object_text": procedure_text,
                })
            insert_relation(
                db,
                _relation_record(job_id, evidence_span_id, f"material:{index}", **common),
            )

        for index, fact in enumerate(candidate.facts):
            try:
                start, end = locate_verbatim(
                    evidence["evidence_text"], fact.value_text, fact.evidence_quote
                )
                offset_error = None
            except ValueError as error:
                start = end = None
                offset_error = str(error)
            if offset_error or disallowed:
                status, reason = "rejected", offset_error or "non_performed_procedure"
            elif not fact.explicit or fact.uncertain or candidate.conflicts:
                status, reason = "unresolved", "uncertain_or_conflicting_fact"
            else:
                status, reason = "validated", "exact_explicit_fact"
            insert_relation(
                db,
                _relation_record(
                    job_id,
                    evidence_span_id,
                    f"fact:{index}",
                    subject_type="procedure",
                    subject_text=procedure_text,
                    predicate=f"has_{fact.fact_type}",
                    object_type=fact.fact_type,
                    object_text=fact.value_text,
                    object_char_start=start,
                    object_char_end=end,
                    attributes_json=json.dumps({
                        "evidence_quote": fact.evidence_quote,
                        "uncertain": fact.uncertain,
                        "conflicts": candidate.conflicts,
                    }),
                    model_confidence=fact.confidence,
                    is_explicit=int(fact.explicit),
                    validation_status=status,
                    validation_reason=reason,
                    review_status="rejected" if status == "rejected" else "needs_review",
                ),
            )
        counts = {
            row["validation_status"]: row["count"]
            for row in db.execute(
                """SELECT validation_status, count(*) count FROM relation_candidate
                   WHERE extraction_job_id=? AND evidence_span_id=?
                   GROUP BY validation_status""",
                (job_id, evidence_span_id),
            )
        }
    return counts


def _needs_adjudication(candidate: RelationExtraction) -> bool:
    return (
        candidate.procedure_type == "ambiguous"
        or bool(candidate.conflicts)
        or any(item.uncertain or item.confidence < 0.7 for item in candidate.materials)
        or any(item.uncertain or item.confidence < 0.7 for item in candidate.facts)
    )


async def process_evidence_span(
    evidence_span_id: str, provider: ProviderMode = "auto"
) -> dict:
    with connect() as db:
        evidence = db.execute(
            """SELECT evidence_span_id, evidence_text, source_url
               FROM evidence_span WHERE evidence_span_id=?""",
            (evidence_span_id,),
        ).fetchone()
    if not evidence:
        raise ValueError(f"Unknown evidence_span_id: {evidence_span_id}")

    result = await extract_text(
        evidence["evidence_text"], evidence["source_url"], provider=provider
    )
    candidate = RelationExtraction.model_validate(result["candidate"])
    adjudication_enabled = os.getenv(
        "RELATION_ENABLE_GROQ_ADJUDICATOR", "false"
    ).strip().casefold() in {"1", "true", "yes", "on"}
    if _needs_adjudication(candidate) and os.getenv("GROQ_API_KEY") and adjudication_enabled:
        try:
            job_id, _, stronger = await call_provider(
                "groq", "openai/gpt-oss-120b", evidence["evidence_text"], evidence["source_url"]
            )
            result = {
                "extraction_job_id": job_id,
                "provider": "groq",
                "model": "openai/gpt-oss-120b",
                "review_status": "needs_review",
                "candidate": stronger.model_dump(),
                "adjudicated": True,
            }
            candidate = stronger
        except Exception:
            result["adjudicated"] = False
    counts = persist_candidate(evidence_span_id, result["extraction_job_id"], candidate)
    result["validation_counts"] = counts
    return result


def enqueue_relations(evidence_span_ids: list[str], provider: ProviderMode) -> dict:
    now = datetime.now(UTC).isoformat()
    jobs = []
    with transaction() as db:
        known = {
            row[0] for row in db.execute(
                f"SELECT evidence_span_id FROM evidence_span WHERE evidence_span_id IN ({','.join('?' for _ in evidence_span_ids)})",
                evidence_span_ids,
            )
        }
        missing = sorted(set(evidence_span_ids) - known)
        if missing:
            raise ValueError("Unknown evidence spans: " + ", ".join(missing[:10]))
        for evidence_span_id in evidence_span_ids:
            input_identity = f"{evidence_span_id}:{provider}"
            job_id = stable_id("pipeline-job", "relation_extraction", input_identity)
            db.execute(
                """INSERT OR IGNORE INTO pipeline_job
                   (pipeline_job_id, job_type, input_identity, status, queued_at, result_json)
                   VALUES (?, 'relation_extraction', ?, 'queued', ?, ?)""",
                (job_id, input_identity, now, json.dumps({
                    "evidence_span_id": evidence_span_id, "provider_mode": provider
                })),
            )
            jobs.append(job_id)
    return {"queued": len(jobs), "jobs": jobs, "automatic_acceptance": False}


def provisional_graph(
    publication_number: str | None = None,
    validation_status: str | None = None,
    limit: int = 5000,
) -> dict:
    where, values = ["1=1"], []
    if publication_number:
        where.append("e.publication_number=?")
        values.append(publication_number)
    if validation_status:
        where.append("r.validation_status=?")
        values.append(validation_status)
    values.append(limit)
    with connect() as db:
        rows = db.execute(
            f"""SELECT r.*, e.publication_number FROM relation_candidate r
                 JOIN evidence_span e USING (evidence_span_id)
                 WHERE {' AND '.join(where)}
                 ORDER BY e.publication_number, r.evidence_span_id, r.relation_candidate_id
                 LIMIT ?""",
            values,
        ).fetchall()
        candidate_spans = {
            row[0] for row in db.execute(
                """SELECT r.evidence_span_id FROM relation_candidate r
                   WHERE r.validation_status='validated'
                     AND r.predicate IN ('consumed','produced')
                   GROUP BY r.evidence_span_id
                   HAVING sum(r.predicate='consumed') >= 1
                      AND count(DISTINCT CASE WHEN r.predicate='produced'
                                              THEN r.object_compound_id END)=1
                      AND EXISTS (
                        SELECT 1 FROM relation_candidate d
                        WHERE d.evidence_span_id=r.evidence_span_id
                          AND d.predicate='describes'
                          AND d.validation_status='validated'
                      )
                      AND NOT EXISTS (
                        SELECT 1 FROM relation_candidate c
                        JOIN relation_candidate p
                          ON p.evidence_span_id=c.evidence_span_id
                        WHERE c.evidence_span_id=r.evidence_span_id
                          AND c.validation_status='validated'
                          AND p.validation_status='validated'
                          AND c.predicate='consumed' AND p.predicate='produced'
                          AND c.subject_compound_id=p.object_compound_id
                      )"""
            )
        }
        structures = {}
        if candidate_spans:
            placeholders = ",".join("?" for _ in candidate_spans)
            for row in db.execute(
                f"""SELECT r.evidence_span_id, r.predicate,
                           COALESCE(cp.standardized_smiles, c.smiles) AS smiles
                    FROM relation_candidate r
                    LEFT JOIN compound c
                      ON c.compound_id=CASE WHEN r.predicate='produced'
                                            THEN r.object_compound_id ELSE r.subject_compound_id END
                    LEFT JOIN compound_property cp USING (compound_id)
                    WHERE r.evidence_span_id IN ({placeholders})
                      AND r.validation_status='validated'
                      AND r.predicate IN ('consumed','produced')""",
                sorted(candidate_spans),
            ):
                structures.setdefault(row["evidence_span_id"], {}).setdefault(row["predicate"], []).append(row["smiles"])
        validation = {
            span_id: validate_provisional_reaction(values.get("consumed", []),
                                                    (values.get("produced") or [None])[0])
            for span_id, values in structures.items()
        }
        gated = {span_id for span_id, result in validation.items() if result.status == "validated"}
        summary = [dict(row) for row in db.execute(
            """SELECT validation_status, predicate, count(*) count
               FROM relation_candidate GROUP BY validation_status, predicate
               ORDER BY validation_status, predicate"""
        )]
        accepted = db.execute(
            "SELECT count(*) FROM reaction_instance WHERE review_status='accepted'"
        ).fetchone()[0]

    nodes: dict[str, dict] = {}
    edges = []

    def node_id(kind: str, text: str, compound_id: str | None, span_id: str, start):
        if kind == "compound" and compound_id:
            return f"compound:{compound_id}"
        if kind == "patent":
            return f"patent:{text}"
        if kind == "procedure":
            return f"procedure:{span_id}"
        return f"candidate:{kind}:{span_id}:{start if start is not None else sha256_text(text)[:12]}"

    for row in rows:
        item = dict(row)
        source = node_id(
            item["subject_type"], item["subject_text"], item["subject_compound_id"],
            item["evidence_span_id"], item["subject_char_start"],
        )
        target = node_id(
            item["object_type"], item["object_text"], item["object_compound_id"],
            item["evidence_span_id"], item["object_char_start"],
        )
        for node, kind, label, compound_id in (
            (source, item["subject_type"], item["subject_text"], item["subject_compound_id"]),
            (target, item["object_type"], item["object_text"], item["object_compound_id"]),
        ):
            node_type = (
                "provisional_reaction"
                if kind == "procedure" and item["evidence_span_id"] in gated
                else kind
            )
            nodes[node] = {
                "id": node, "type": node_type, "label": label,
                "compound_id": compound_id, "review_status": "needs_review",
            }
        edges.append({
            "id": item["relation_candidate_id"],
            "source": source,
            "target": target,
            "type": item["predicate"],
            "validation_status": item["validation_status"],
            "validation_reason": item["validation_reason"],
            "evidence_span_id": item["evidence_span_id"],
            "publication_number": item["publication_number"],
            "review_status": item["review_status"],
        })
    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "summary": summary,
        "provisional_reaction_count": len(gated),
        "provisional_reaction_validation": {
            key: value.as_dict() for key, value in validation.items()
        },
        "accepted_chemistry_count": accepted,
        "automatic_acceptance": False,
        "truncated": len(rows) == limit,
    }
