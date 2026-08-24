#!/usr/bin/env python3
"""Pure helpers shared by the RXN2 Colab relation runner and local tests."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

SCHEMA_VERSION = "rxn2-relation-v1"
MODEL_NAME = "Qwen/Qwen3-4B-Instruct-2507"
PROCEDURE_TYPES = ("performed", "referenced", "analytical", "purification", "ambiguous")
ROLES = ("consumed", "produced", "reagent", "catalyst", "solvent", "workup")
FACT_TYPES = ("condition", "quantity", "outcome")

RELATION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["procedure_type", "materials", "facts", "conflicts"],
    "properties": {
        "procedure_type": {"enum": list(PROCEDURE_TYPES)},
        "materials": {"type": "array", "maxItems": 60, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["surface_text", "role", "evidence_quote", "explicit", "uncertain", "confidence"],
            "properties": {
                "surface_text": {"type": "string", "minLength": 1, "maxLength": 500},
                "role": {"enum": list(ROLES)},
                "evidence_quote": {"type": "string", "minLength": 1, "maxLength": 5000},
                "explicit": {"type": "boolean"}, "uncertain": {"type": "boolean"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            }}},
        "facts": {"type": "array", "maxItems": 60, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["fact_type", "value_text", "evidence_quote", "explicit", "uncertain", "confidence"],
            "properties": {
                "fact_type": {"enum": list(FACT_TYPES)},
                "value_text": {"type": "string", "minLength": 1, "maxLength": 1000},
                "evidence_quote": {"type": "string", "minLength": 1, "maxLength": 5000},
                "explicit": {"type": "boolean"}, "uncertain": {"type": "boolean"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            }}},
        "conflicts": {"type": "array", "maxItems": 20, "items": {"type": "string"}},
    },
}

SYSTEM_PROMPT = """Extract only explicit facts from one public patent procedure.
Return one JSON object and nothing else matching the supplied schema.
Classify procedure_type as performed only when the text describes an executed procedure.
Material roles are only consumed, produced, reagent, catalyst, solvent, or workup.
Fact types are only condition, quantity, or outcome.
surface_text, value_text, and evidence_quote must be exact verbatim substrings.
Use the shortest supporting evidence_quote that occurs exactly once in the procedure.
Do not infer identities, structures, SMILES, InChI, reaction SMILES, missing quantities,
missing products, reaction edges, or accepted chemistry. Record uncertainty and conflicts."""


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def job_hash(job: dict) -> str:
    return sha256_text(job["evidence_span_id"] + "\n" + job["evidence_text"])


def _unique_offset(source: str, value: str) -> int:
    first = source.find(value)
    if first < 0:
        raise ValueError("verbatim_value_missing")
    if source.find(value, first + 1) >= 0:
        raise ValueError("verbatim_value_not_unique")
    return first


def validate_candidate(candidate: dict, source_text: str) -> dict:
    if set(candidate) != {"procedure_type", "materials", "facts", "conflicts"}:
        raise ValueError("candidate_keys_invalid")
    if candidate["procedure_type"] not in PROCEDURE_TYPES:
        raise ValueError("procedure_type_invalid")
    if not isinstance(candidate["materials"], list) or len(candidate["materials"]) > 60:
        raise ValueError("materials_invalid")
    if not isinstance(candidate["facts"], list) or len(candidate["facts"]) > 60:
        raise ValueError("facts_invalid")
    if not isinstance(candidate["conflicts"], list) or len(candidate["conflicts"]) > 20:
        raise ValueError("conflicts_invalid")
    for item in candidate["materials"]:
        required = {"surface_text", "role", "evidence_quote", "explicit", "uncertain", "confidence"}
        if set(item) != required or item["role"] not in ROLES:
            raise ValueError("material_schema_invalid")
        if not isinstance(item["explicit"], bool) or not isinstance(item["uncertain"], bool):
            raise ValueError("material_flags_invalid")
        if not isinstance(item["confidence"], (int, float)) or not 0 <= item["confidence"] <= 1:
            raise ValueError("material_confidence_invalid")
        _unique_offset(source_text, item["evidence_quote"])
        _unique_offset(item["evidence_quote"], item["surface_text"])
    for item in candidate["facts"]:
        required = {"fact_type", "value_text", "evidence_quote", "explicit", "uncertain", "confidence"}
        if set(item) != required or item["fact_type"] not in FACT_TYPES:
            raise ValueError("fact_schema_invalid")
        if not isinstance(item["explicit"], bool) or not isinstance(item["uncertain"], bool):
            raise ValueError("fact_flags_invalid")
        if not isinstance(item["confidence"], (int, float)) or not 0 <= item["confidence"] <= 1:
            raise ValueError("fact_confidence_invalid")
        _unique_offset(source_text, item["evidence_quote"])
        _unique_offset(item["evidence_quote"], item["value_text"])
    if not all(isinstance(value, str) for value in candidate["conflicts"]):
        raise ValueError("conflicts_invalid")
    return candidate


def pack_jobs(jobs: Iterable[dict], max_items: int = 4, max_chars: int = 16_000) -> list[list[dict]]:
    ordered = sorted(jobs, key=lambda job: (len(job["evidence_text"]), job["evidence_span_id"]))
    batches: list[list[dict]] = []
    current: list[dict] = []
    characters = 0
    for job in ordered:
        size = len(job["evidence_text"])
        if current and (len(current) >= max_items or characters + size > max_chars):
            batches.append(current)
            current, characters = [], 0
        current.append(job)
        characters += size
    if current:
        batches.append(current)
    return batches


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


PROMPT_SHA256 = sha256_text(SYSTEM_PROMPT + "\n" + SCHEMA_VERSION + "\n" + canonical_json(RELATION_SCHEMA))
