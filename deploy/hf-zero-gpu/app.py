from __future__ import annotations

import json
import os
import re

import gradio as gr
import spaces
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from lmformatenforcer import JsonSchemaParser
from lmformatenforcer.integrations.transformers import build_transformers_prefix_allowed_tokens_fn

MODEL_ID = os.getenv("MODEL_ID", "Qwen/Qwen3-4B-Instruct-2507")
MAX_ITEMS = 8
MAX_ITEM_CHARS = 15_000
MAX_BATCH_CHARS = 70_000
RELATION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["procedure_type", "materials", "facts", "conflicts"],
    "properties": {
        "procedure_type": {"enum": ["performed", "referenced", "analytical", "purification", "ambiguous"]},
        "materials": {"type": "array", "maxItems": 60, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["surface_text", "role", "evidence_quote", "explicit", "uncertain", "confidence"],
            "properties": {
                "surface_text": {"type": "string", "minLength": 1, "maxLength": 500},
                "role": {"enum": ["consumed", "produced", "reagent", "catalyst", "solvent", "workup"]},
                "evidence_quote": {"type": "string", "minLength": 1, "maxLength": 5000},
                "explicit": {"type": "boolean"}, "uncertain": {"type": "boolean"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1}
            }}},
        "facts": {"type": "array", "maxItems": 60, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["fact_type", "value_text", "evidence_quote", "explicit", "uncertain", "confidence"],
            "properties": {
                "fact_type": {"enum": ["condition", "quantity", "outcome"]},
                "value_text": {"type": "string", "minLength": 1, "maxLength": 1000},
                "evidence_quote": {"type": "string", "minLength": 1, "maxLength": 5000},
                "explicit": {"type": "boolean"}, "uncertain": {"type": "boolean"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1}
            }}},
        "conflicts": {"type": "array", "maxItems": 20, "items": {"type": "string"}}
    }
}
SYSTEM_PROMPT = """Extract only explicit facts from one public patent procedure.
Return one JSON object and nothing else with exactly these keys:
procedure_type, materials, facts, conflicts.
procedure_type is performed, referenced, analytical, purification, or ambiguous.
materials is a list of objects with surface_text, role, evidence_quote, explicit,
uncertain, confidence. role is consumed, produced, reagent, catalyst, solvent, or workup.
facts is a list of objects with fact_type, value_text, evidence_quote, explicit,
uncertain, confidence. fact_type is condition, quantity, or outcome.
Use the shortest exact supporting clause that occurs exactly once in the supplied procedure for evidence_quote.
Material surface_text, fact value_text, and every evidence_quote must be exact verbatim
substrings of the supplied procedure. Do not infer structures, identities, SMILES,
quantities, products, or reaction edges. Mark uncertainty and contradictions."""

tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
model = AutoModelForCausalLM.from_pretrained(
    MODEL_ID, torch_dtype=torch.bfloat16, low_cpu_mem_usage=True,
).eval().to("cuda")
json_parser = JsonSchemaParser(RELATION_SCHEMA)
prefix_allowed_tokens_fn = build_transformers_prefix_allowed_tokens_fn(tokenizer, json_parser)


def _json_object(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("model_output_is_not_an_object")
    return value


def _duration(payload: str) -> int:
    try:
        count = len(json.loads(payload))
    except Exception:
        count = 1
    return min(600, max(180, count * 90))


@spaces.GPU(duration=_duration)
def extract_batch(payload: str) -> str:
    items = json.loads(payload)
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS:
        raise gr.Error(f"Submit between 1 and {MAX_ITEMS} procedures")
    total = 0
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("text"), str):
            raise gr.Error("Each item requires string id and text fields")
        if len(item["text"]) > MAX_ITEM_CHARS:
            raise gr.Error(f"Procedure {item['id']} exceeds {MAX_ITEM_CHARS} characters")
        total += len(item["text"])
    if total > MAX_BATCH_CHARS:
        raise gr.Error(f"Batch exceeds {MAX_BATCH_CHARS} characters")

    conversations = [
        [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": item["text"]}]
        for item in items
    ]
    prompts = [
        tokenizer.apply_chat_template(messages, add_generation_prompt=True, tokenize=False)
        for messages in conversations
    ]
    tokenizer.padding_side = "left"
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token_id = tokenizer.eos_token_id
    inputs = tokenizer(prompts, padding=True, return_tensors="pt").to(model.device)
    try:
        with torch.inference_mode():
            generated = model.generate(
                **inputs, max_new_tokens=1600, do_sample=False,
                prefix_allowed_tokens_fn=prefix_allowed_tokens_fn,
                pad_token_id=tokenizer.pad_token_id,
            )
        prompt_width = inputs["input_ids"].shape[-1]
        raw_outputs = tokenizer.batch_decode(generated[:, prompt_width:], skip_special_tokens=True)
        results = [
            {"id": item["id"], "candidate": _json_object(raw), "error": None}
            for item, raw in zip(items, raw_outputs, strict=True)
        ]
    except Exception as error:
        results = [
            {"id": item["id"], "candidate": None, "error": f"{type(error).__name__}: {error}"}
            for item in items
        ]
    return json.dumps({"model": MODEL_ID, "results": results}, ensure_ascii=False)


demo = gr.Interface(
    fn=extract_batch,
    inputs=gr.Textbox(lines=16, label="JSON array of public patent procedures"),
    outputs=gr.Code(language="json", label="Provisional relation candidates"),
    title="RXN2 private provisional relation extractor",
    description="LLM output is unreviewed. RXN2 validates exact quotations, offsets, structures, and chemistry locally.",
    api_name="extract_batch",
)

if __name__ == "__main__":
    demo.queue(default_concurrency_limit=1).launch()