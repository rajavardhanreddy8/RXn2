from __future__ import annotations

import re
import sqlite3


def _page(paragraph_id: str | None) -> str | None:
    match = re.search(r"(?:page|p\.?)\s*(\d+)", paragraph_id or "", re.I)
    return match.group(1) if match else None


def build_provisional_review_queue(db: sqlite3.Connection, limit: int = 50) -> list[dict]:
    """Rank evidence completeness only; this function never accepts chemistry."""
    rows = db.execute(
        """SELECT e.evidence_span_id, e.publication_number, e.section_type,
                  e.paragraph_id, e.char_start, e.char_end, e.evidence_text,
                  e.text_sha256, e.artifact_sha256, e.source_url,
                  e.extraction_method, e.evidence_status,
                  r.relation_candidate_id, r.predicate, r.subject_text,
                  r.subject_char_start, r.subject_char_end, r.subject_compound_id,
                  r.object_text, r.object_char_start, r.object_char_end,
                  r.object_compound_id, r.is_explicit, r.validation_status,
                  r.validation_reason, r.review_status, r.model_confidence
           FROM evidence_span e JOIN relation_candidate r USING (evidence_span_id)
           WHERE r.review_status = 'needs_review'
           ORDER BY e.publication_number, e.evidence_span_id, r.relation_candidate_id"""
    ).fetchall()
    grouped: dict[str, dict] = {}
    for row in rows:
        item = grouped.setdefault(row["evidence_span_id"], {
            "evidence_span_id": row["evidence_span_id"],
            "publication_number": row["publication_number"],
            "provenance": {
                "artifact_sha256": row["artifact_sha256"], "text_sha256": row["text_sha256"],
                "source_url": row["source_url"], "section_type": row["section_type"],
                "paragraph_id": row["paragraph_id"], "page": _page(row["paragraph_id"]),
                "page_status": "recorded" if _page(row["paragraph_id"]) else "not_recorded",
                "char_start": row["char_start"], "char_end": row["char_end"],
                "extraction_method": row["extraction_method"],
            }, "evidence_status": row["evidence_status"], "relations": [],
        })
        text, start, end = row["evidence_text"], row["object_char_start"], row["object_char_end"]
        item["relations"].append({
            "id": row["relation_candidate_id"], "predicate": row["predicate"],
            "subject": row["subject_text"], "object": row["object_text"],
            "quote": text[start:end] if start is not None and end is not None else None,
            "char_start": start, "char_end": end, "subject_compound_id": row["subject_compound_id"],
            "object_compound_id": row["object_compound_id"], "explicit": bool(row["is_explicit"]),
            "validation_status": row["validation_status"], "validation_reason": row["validation_reason"],
            "model_confidence": row["model_confidence"],
        })
    queue = []
    for item in grouped.values():
        relations = item["relations"]
        explicit = lambda predicate: [r for r in relations if r["predicate"] == predicate and r["explicit"]]
        product = [r for r in explicit("produced") if r["object_compound_id"] and r["validation_status"] == "validated"]
        consumed = [r for r in explicit("consumed") if r["object_compound_id"] and r["validation_status"] == "validated"]
        components = {
            "explicit_resolved_product": 50 if product else 0,
            "explicit_resolved_input": 20 if consumed else 0,
            "explicit_conditions": 8 if explicit("has_condition") else 0,
            "explicit_quantities": 10 if explicit("has_quantity") else 0,
            "explicit_outcomes": 12 if explicit("has_outcome") else 0,
            "complete_provenance": 8 if item["provenance"]["artifact_sha256"] and item["provenance"]["text_sha256"] else 0,
            "rejected_relations": -40 if any(r["validation_status"] == "rejected" for r in relations) else 0,
        }
        item["rank_score"] = sum(components.values())
        item["rank_components"] = components
        item["review_state"] = "needs_review"
        item["automatic_acceptance"] = False
        queue.append(item)
    queue.sort(key=lambda item: (-item["rank_score"], item["publication_number"], item["evidence_span_id"]))
    for index, item in enumerate(queue[:limit], 1): item["rank"] = index
    return queue[:limit]