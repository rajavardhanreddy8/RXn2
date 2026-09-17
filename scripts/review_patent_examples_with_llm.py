#!/usr/bin/env python3
"""Run two evidence-bound LLM audits over native patent example blocks.

These agents propose review findings only. Their output is deliberately incompatible
with the human curation-decision importer and can never promote a route to gold.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.local_automation import load_env_file


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_OPENROUTER_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
DEFAULT_GROQ_MODEL = "qwen/qwen3.6-27b"
ProviderMode = Literal["groq", "openrouter"]
ReviewerRole = Literal["source_completeness", "adversarial_chemistry"]
MaterialRole = Literal["consumed", "produced", "reagent", "catalyst", "solvent", "workup"]
FactField = Literal[
    "amount", "concentration", "temperature", "time", "pressure", "atmosphere",
    "operation", "yield", "purity", "product_form", "stereochemistry",
    "analytical", "other",
]
MissingField = Literal[
    "starting_material", "product", "amount", "solvent", "temperature", "time",
    "workup", "yield", "product_form", "stereochemistry",
]


class MaterialFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    surface_text: str = Field(min_length=1, max_length=500)
    role: MaterialRole
    evidence_quote: str = Field(min_length=1, max_length=5000)
    uncertain: bool
    confidence: float = Field(ge=0, le=1)


class FactFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: FactField
    value_text: str = Field(min_length=1, max_length=1000)
    evidence_quote: str = Field(min_length=1, max_length=5000)
    uncertain: bool
    confidence: float = Field(ge=0, le=1)


class ReviewProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    procedure_type: Literal["performed", "referenced", "analytical", "purification", "ambiguous"]
    materials: list[MaterialFinding] = Field(max_length=100)
    facts: list[FactFinding] = Field(max_length=100)
    missing_fields: list[MissingField] = Field(max_length=20)
    issues: list[str] = Field(max_length=30)
    recommendation: Literal[
        "ready_for_human_review", "incomplete", "ambiguous", "reject_nonperformed"
    ]
    rationale: str = Field(min_length=1, max_length=4000)


BASE_PROMPT = """You audit one public patent example for RXN2 process-data extraction.
Return one JSON object matching the supplied contract and nothing else.
Use only explicit statements in the source. Every surface_text, value_text, and
evidence_quote must be an exact verbatim substring of the source. Never invent a
chemical identity, structure, role, quantity, condition, yield, product, or missing
starting material. Classify an executed procedure as performed; do not treat a generic,
prophetic, analytical-only, purification-only, or referenced procedure as performed.
List a required field in missing_fields only when it is not explicitly reported in this
example block. Uncertainty must remain uncertainty. Your output is a proposal for a
human chemist and never an approval."""

OUTPUT_RULES = """Every top-level key is required, even when its value is an empty list.
Keep rationale under 300 characters. Use the shortest exact quote that supports each
item. Do not repeat the same fact. Return at most 30 materials and 30 facts."""

ROLE_PROMPTS = {
    "source_completeness": """Act as the source-completeness auditor. Capture all explicit
materials, operations, quantities, conditions, outcomes, and product-form language.
Pay special attention to fields present in the source that an extractor could miss.
Atmosphere, temperature, time, and pressure are facts, never materials.""",
    "adversarial_chemistry": """Act as the adversarial chemistry auditor. Independently
reconstruct the explicit participants and facts. Look especially for a second consumed
starting material hidden among items that could be mislabelled as reagents, unexplained
product fragments, salt/stereochemical ambiguity, reference-only steps, and route gaps.
Do not infer an unreported material merely because chemistry appears incomplete.""",
}


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise ValueError(f"Malformed JSONL at {path}:{line_number}") from error
    return rows


def normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def validate_quotes(source: str, proposal: ReviewProposal) -> list[str]:
    failures = []
    for index, material in enumerate(proposal.materials):
        if material.evidence_quote not in source:
            failures.append(f"material[{index}].evidence_quote_not_verbatim")
        elif material.surface_text not in material.evidence_quote:
            failures.append(f"material[{index}].surface_text_not_in_quote")
    for index, fact in enumerate(proposal.facts):
        if fact.evidence_quote not in source:
            failures.append(f"fact[{index}].evidence_quote_not_verbatim")
        elif fact.value_text not in fact.evidence_quote:
            failures.append(f"fact[{index}].value_text_not_in_quote")
    return failures


def exact_line_quote(source: str, needle: str) -> str | None:
    if not needle or source.count(needle) != 1:
        return None
    position = source.index(needle)
    start = source.rfind("\n", 0, position) + 1
    end = source.find("\n", position + len(needle))
    if end < 0:
        end = len(source)
    quote = source[start:end]
    if len(quote) <= 5000:
        return quote
    start = max(start, position - 2000)
    end = min(end, position + len(needle) + 2000)
    return source[start:end]


def repair_quotes(source: str, proposal: ReviewProposal) -> tuple[ReviewProposal, list[str]]:
    payload = proposal.model_dump()
    repairs = []
    for collection, needle_field in (("materials", "surface_text"), ("facts", "value_text")):
        for index, item in enumerate(payload[collection]):
            quote = item["evidence_quote"]
            needle = item[needle_field]
            if quote in source and needle in quote:
                continue
            replacement = exact_line_quote(source, needle)
            if replacement is not None:
                item["evidence_quote"] = replacement
                repairs.append(f"{collection}[{index}].evidence_quote_reanchored")
    return ReviewProposal.model_validate(payload), repairs


def comparison(left: ReviewProposal, right: ReviewProposal) -> dict:
    left_materials = {(normalized(item.surface_text), item.role) for item in left.materials}
    right_materials = {(normalized(item.surface_text), item.role) for item in right.materials}
    left_facts = {(item.field, normalized(item.value_text)) for item in left.facts}
    right_facts = {(item.field, normalized(item.value_text)) for item in right.facts}
    disagreements = []
    if left.procedure_type != right.procedure_type:
        disagreements.append("procedure_type")
    if left_materials != right_materials:
        disagreements.append("materials_or_roles")
    if left_facts != right_facts:
        disagreements.append("facts")
    if set(left.missing_fields) != set(right.missing_fields):
        disagreements.append("missing_fields")
    if left.recommendation != right.recommendation:
        disagreements.append("recommendation")
    return {
        "status": "agent_agreement_candidate" if not disagreements else "agent_disagreement",
        "disagreements": disagreements,
        "human_review_required": True,
        "counts_as_human_curation": False,
    }


def response_contract() -> str:
    return json.dumps(
        {
            "procedure_type": "performed|referenced|analytical|purification|ambiguous",
            "missing_fields": [],
            "issues": [],
            "recommendation": "ready_for_human_review|incomplete|ambiguous|reject_nonperformed",
            "rationale": "short rationale",
            "materials": [{
                "surface_text": "verbatim", "role": "consumed|produced|reagent|catalyst|solvent|workup",
                "evidence_quote": "verbatim", "uncertain": False, "confidence": 0.0,
            }],
            "facts": [{
                "field": "amount|concentration|temperature|time|pressure|atmosphere|operation|yield|purity|product_form|stereochemistry|analytical|other",
                "value_text": "verbatim", "evidence_quote": "verbatim",
                "uncertain": False, "confidence": 0.0,
            }],
        }, separators=(",", ":")
    )


def parse_content(content: str) -> ReviewProposal:
    value = content.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        value = "\n".join(lines[1:-1])
    return ReviewProposal.model_validate_json(value)


def request_payload(provider: ProviderMode, model: str, role: ReviewerRole, text: str) -> dict:
    prompt = (
        BASE_PROMPT + "\n\n" + ROLE_PROMPTS[role] + "\n\n" + OUTPUT_RULES
        + "\n\nJSON contract:\n" + response_contract()
    )
    payload = {
        "model": model,
        "max_tokens": int(os.getenv("PATENT_REVIEW_MAX_OUTPUT_TOKENS", "4096")),
        "response_format": {"type": "json_object"},
    }
    if provider == "groq":
        payload.update({
            "messages": [{"role": "user", "content": prompt + "\n\nSOURCE:\n" + text}],
            "temperature": 0.7,
            "top_p": 0.8,
            "reasoning_effort": "none",
        })
    else:
        payload.update({
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": text},
            ],
            "temperature": 0,
            "reasoning": {"effort": "none"},
            "provider": {
                "require_parameters": True,
                "data_collection": "deny",
                "allow_fallbacks": True,
            },
        })
    return payload


def call_reviewer(
    client: httpx.Client,
    key: str,
    provider: ProviderMode,
    model: str,
    role: ReviewerRole,
    text: str,
) -> ReviewProposal:
    payload = request_payload(provider, model, role, text)
    url = GROQ_URL if provider == "groq" else OPENROUTER_URL
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if provider == "openrouter":
        headers.update({
            "HTTP-Referer": "https://github.com/rajavardhanreddy8/RXn2",
            "X-Title": "RXN2 patent review agents",
        })
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            response = client.post(url, headers=headers, json=payload)
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < 3:
                    retry_after = response.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after else 20.0
                    time.sleep(min(delay, 45))
                    continue
            if response.status_code >= 400:
                raise RuntimeError(
                    f"{provider}_http_{response.status_code}: {response.text[:1000]}"
                )
            response.raise_for_status()
            return parse_content(response.json()["choices"][0]["message"]["content"])
        except RuntimeError:
            raise
        except (httpx.HTTPError, KeyError, ValueError) as error:
            last_error = error
            if attempt < 3:
                time.sleep(min(2**attempt, 15))
                continue
            raise
    raise last_error or RuntimeError("review provider failed")


def write_checkpoint(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    with partial.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    partial.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--example-blocks", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--provider", choices=("groq", "openrouter"), default="groq")
    parser.add_argument("--model")
    parser.add_argument("--limit", type=int, default=0, help="0 reviews every block")
    parser.add_argument("--max-input-chars", type=int, default=30000)
    args = parser.parse_args()
    load_env_file(args.env_file)
    if args.provider == "groq":
        model = args.model or os.getenv("PATENT_REVIEW_GROQ_MODEL", DEFAULT_GROQ_MODEL)
        key = os.getenv("GROQ_API_KEY")
    else:
        model = args.model or os.getenv(
            "PATENT_REVIEW_OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL
        )
        if not model.endswith(":free") and model != "openrouter/free":
            raise SystemExit("Paid OpenRouter models are disabled; use a :free model")
        key = os.getenv("OPENROUTER_API_KEY") or os.getenv("op_api_key")
    if not key:
        raise SystemExit(f"{'GROQ_API_KEY' if args.provider == 'groq' else 'OPENROUTER_API_KEY'} is not configured")

    inputs = read_jsonl(args.example_blocks)
    output_file = args.output / "agent_review_proposals.jsonl"
    completed = read_jsonl(output_file) if output_file.is_file() else []
    completed_ids = {row["example_id"] for row in completed}
    pending = [row for row in inputs if row["example_id"] not in completed_ids]
    if args.limit:
        pending = pending[: args.limit]

    with httpx.Client(timeout=float(os.getenv("PATENT_REVIEW_TIMEOUT_SECONDS", "120"))) as client:
        for block in pending:
            text = block["text"][: args.max_input_chars]
            proposals: dict[str, ReviewProposal] = {}
            failures: dict[str, list[str]] = {}
            repairs: dict[str, list[str]] = {}
            for role in ("source_completeness", "adversarial_chemistry"):
                try:
                    proposal = call_reviewer(client, key, args.provider, model, role, text)
                    proposal, repairs[role] = repair_quotes(text, proposal)
                    proposals[role] = proposal
                    failures[role] = validate_quotes(text, proposal)
                except Exception as error:
                    repairs[role] = []
                    failures[role] = [f"provider_or_schema_error:{str(error)[:500]}"]
            if len(proposals) == 2 and not any(failures.values()):
                consensus = comparison(proposals["source_completeness"], proposals["adversarial_chemistry"])
            else:
                consensus = {
                    "status": "invalid_or_incomplete_agent_output",
                    "disagreements": ["local_validation_or_provider_failure"],
                    "human_review_required": True, "counts_as_human_curation": False,
                }
            completed.append(
                {
                    "schema_version": "rxn2-agent-review-proposal-v1",
                    "example_id": block["example_id"],
                    "publication_number": block["publication_number"],
                    "heading": block["heading"],
                    "text_sha256": block["text_sha256"],
                    "source_artifact_sha256": block["source_artifact_sha256"],
                    "provider": args.provider, "model": model,
                    "reviewed_at": datetime.now(UTC).isoformat(),
                    "reviews": {name: value.model_dump() for name, value in proposals.items()},
                    "local_quote_repairs": repairs,
                    "local_validation_failures": failures, "consensus": consensus,
                    "review_status": "needs_human_review", "creates_curation_decision": False,
                }
            )
            write_checkpoint(output_file, completed)
            print(json.dumps({"example_id": block["example_id"], "status": consensus["status"]}))

    manifest = {
        "schema_version": "rxn2-agent-review-manifest-v1",
        "created_at": datetime.now(UTC).isoformat(), "provider": args.provider,
        "model": model,
        "records": len(completed), "input": str(args.example_blocks),
        "input_sha256": hashlib.sha256(args.example_blocks.read_bytes()).hexdigest(),
        "output_sha256": hashlib.sha256(output_file.read_bytes()).hexdigest() if output_file.is_file() else None,
        "safety": {
            "counts_as_human_curation": False, "creates_curation_decisions": False,
            "accepts_gold_chemistry": False, "human_review_required": True,
        },
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
