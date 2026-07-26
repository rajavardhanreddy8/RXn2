from __future__ import annotations

import hashlib
import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .chemistry import standardize_smiles
from .costing import evaluate_route, rank_evaluated
from .db import connect, initialize, transaction
from .models import (
    PriceImportRequest,
    QroqExtractionRequest,
    RouteCompareRequest,
    RouteGenerateRequest,
    TargetResolveRequest,
)
from .qroq import extract
from .routes import compound_by_id, generate_routes, resolve_compound
from .seed import seed_demo


def demo_seed_enabled() -> bool:
    return os.getenv("RXN2_SEED_DEMO", "").strip().casefold() in {
        "1", "true", "yes", "on"
    }


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize()
    if demo_seed_enabled():
        seed_demo()
    yield


app = FastAPI(
    title="ScaleUp Graph MVP",
    version="0.2.0",
    description="Evidence-bounded synthesis route and raw-material cost decision support.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

COVERAGE_STATUSES = (
    "identified", "patents_found", "examples_extracted", "routes_under_review",
    "complete_reviewed_route", "price_complete", "cost_comparison_ready",
    "public_evidence_unavailable",
)


@app.get("/api/health")
def health() -> dict:
    with connect() as db:
        counts = {
            "compounds": db.execute("SELECT count(*) FROM compound").fetchone()[0],
            "drugs": db.execute("SELECT count(*) FROM drug_entity").fetchone()[0],
            "patent_candidates": db.execute("SELECT count(*) FROM patent_candidate").fetchone()[0],
            "reactions": db.execute("SELECT count(*) FROM reaction_instance").fetchone()[0],
            "quotes": db.execute("SELECT count(*) FROM material_quote").fetchone()[0],
        }
    return {"status": "ok", "database": "sqlite", "counts": counts}


@app.get("/api/catalogue/coverage")
def catalogue_coverage(
    query: str = Query(default="", max_length=200),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=250),
    offset: int = Query(default=0, ge=0),
) -> dict:
    if status and status not in COVERAGE_STATUSES:
        raise HTTPException(status_code=422, detail=f"Unknown coverage status: {status}")
    where = []
    parameters: list[object] = []
    if status:
        where.append("cv.status = ?")
        parameters.append(status)
    if query.strip():
        value = f"%{query.strip().casefold()}%"
        where.append(
            """(lower(d.preferred_name) LIKE ? OR EXISTS (
                 SELECT 1 FROM drug_alias a
                 WHERE a.drug_id = d.drug_id AND lower(a.alias) LIKE ?))"""
        )
        parameters.extend((value, value))
    where_sql = "WHERE " + " AND ".join(where) if where else ""
    with connect() as db:
        total = db.execute(
            f"""SELECT count(*) FROM drug_entity d
                JOIN drug_coverage cv USING (drug_id) {where_sql}""",
            parameters,
        ).fetchone()[0]
        rows = db.execute(
            f"""SELECT d.drug_id, d.preferred_name, d.modality, d.review_status,
                       cv.status, cv.identified, cv.patents_found, cv.examples_extracted,
                       cv.routes_under_review, cv.complete_reviewed_route,
                       cv.price_complete, cv.cost_comparison_ready,
                       cv.public_evidence_unavailable, cv.patent_count,
                       cv.extracted_example_count, cv.reviewed_route_count,
                       cv.priced_route_count, cv.refreshed_at, cv.details_json,
                       (SELECT count(*) FROM drug_compound dc
                        WHERE dc.drug_id = d.drug_id) compound_count,
                       (SELECT count(*) FROM regulatory_product_drug rpd
                        WHERE rpd.drug_id = d.drug_id) product_count,
                       (SELECT group_concat(marketing_status) FROM (
                          SELECT DISTINCT rp.marketing_status
                          FROM regulatory_product_drug rpd
                          JOIN regulatory_product rp USING (regulatory_product_id)
                          WHERE rpd.drug_id = d.drug_id
                          ORDER BY rp.marketing_status
                       )) marketing_statuses
                FROM drug_entity d JOIN drug_coverage cv USING (drug_id)
                {where_sql}
                ORDER BY d.preferred_name, d.drug_id LIMIT ? OFFSET ?""",
            [*parameters, limit, offset],
        ).fetchall()
        summary_row = db.execute(
            """SELECT sum(identified) identified, sum(patents_found) patents_found,
                      sum(examples_extracted) examples_extracted,
                      sum(routes_under_review) routes_under_review,
                      sum(complete_reviewed_route) complete_reviewed_route,
                      sum(price_complete) price_complete,
                      sum(cost_comparison_ready) cost_comparison_ready,
                      sum(public_evidence_unavailable) public_evidence_unavailable
               FROM drug_coverage"""
        ).fetchone()
        summary = {name: int(summary_row[name] or 0) for name in COVERAGE_STATUSES}
    items = []
    for row in rows:
        item = dict(row)
        item["details"] = json.loads(item.pop("details_json"))
        item["marketing_statuses"] = sorted(
            value for value in (item["marketing_statuses"] or "").split(",") if value
        )
        for flag in COVERAGE_STATUSES:
            item[flag] = bool(item[flag])
        items.append(item)
    return {"total": total, "limit": limit, "offset": offset, "status_counts": summary, "items": items}


@app.get("/api/catalogue/drugs/{drug_id}")
def catalogue_drug(drug_id: str) -> dict:
    with connect() as db:
        drug = db.execute(
            """SELECT d.*, cv.status coverage_status, cv.details_json coverage_details,
                      cv.patent_count, cv.extracted_example_count,
                      cv.reviewed_route_count, cv.priced_route_count, cv.refreshed_at
               FROM drug_entity d LEFT JOIN drug_coverage cv USING (drug_id)
               WHERE d.drug_id = ?""",
            (drug_id,),
        ).fetchone()
        if not drug:
            raise HTTPException(status_code=404, detail="Drug not found")
        aliases = [dict(row) for row in db.execute(
            "SELECT alias, alias_type, source_id FROM drug_alias WHERE drug_id = ? ORDER BY alias_type, alias",
            (drug_id,),
        )]
        identifiers = [dict(row) for row in db.execute(
            "SELECT namespace, identifier_value, source_id FROM drug_identifier WHERE drug_id = ? ORDER BY namespace, identifier_value",
            (drug_id,),
        )]
        compounds = [dict(row) for row in db.execute(
            """SELECT c.compound_id, c.preferred_name, c.smiles, c.inchi_key,
                      c.material_form, dc.relationship_type, dc.review_status
               FROM drug_compound dc JOIN compound c USING (compound_id)
               WHERE dc.drug_id = ? ORDER BY dc.relationship_type, c.compound_id""",
            (drug_id,),
        )]
        patents = [dict(row) for row in db.execute(
            """SELECT pc.publication_number, pc.match_type, pc.confidence,
                      pc.source_field_name, pc.review_status, p.title, p.publication_date
               FROM patent_candidate pc JOIN patent_document p USING (publication_number)
               WHERE pc.drug_id = ? ORDER BY p.publication_date DESC, pc.publication_number LIMIT 100""",
            (drug_id,),
        )]
        products = [dict(row) for row in db.execute(
            """SELECT rp.regulatory_product_id, rp.jurisdiction,
                      rp.application_number, rp.product_number, rp.trade_name,
                      rp.dosage_form, rp.route, rp.strength, rp.approval_date,
                      rp.marketing_status, rp.applicant, rp.source_id
               FROM regulatory_product_drug rpd
               JOIN regulatory_product rp USING (regulatory_product_id)
               WHERE rpd.drug_id = ?
               ORDER BY rp.marketing_status, rp.trade_name,
                        rp.application_number, rp.product_number
               LIMIT 500""",
            (drug_id,),
        )]
    result = dict(drug)
    result["coverage_details"] = json.loads(result["coverage_details"] or "{}")
    result.update({
        "aliases": aliases,
        "identifiers": identifiers,
        "compounds": compounds,
        "regulatory_products": products,
        "patent_candidates": patents,
    })
    return result


@app.get("/api/catalogue/releases")
def catalogue_releases() -> dict:
    with connect() as db:
        rows = [dict(row) for row in db.execute(
            """SELECT ir.ingestion_run_id, ir.release_id, ir.source_id,
                      ir.parser_version, ir.started_at, ir.completed_at, ir.status,
                      ir.input_rows, ir.accepted_rows, ir.excluded_rows,
                      ir.rejected_rows, ir.reason_counts_json,
                      sr.released_on,
                      (SELECT count(*) FROM artifact a
                       WHERE a.release_id = ir.release_id) artifact_count,
                      (SELECT COALESCE(sum(a.size_bytes), 0) FROM artifact a
                       WHERE a.release_id = ir.release_id) artifact_bytes
               FROM ingestion_run ir
               JOIN source_release sr USING (release_id)
               ORDER BY ir.completed_at DESC"""
        )]
    for row in rows:
        row["reason_counts"] = json.loads(row.pop("reason_counts_json"))
    return {"total": len(rows), "items": rows}


@app.post("/api/targets/resolve")
def resolve_target(request: TargetResolveRequest) -> dict:
    query_type = request.query_type
    if query_type == "auto":
        query_type = "smiles" if any(token in request.query for token in ("=", "(", ")", "[", "]", "#")) else "name"
    target = None
    standardized = None
    if query_type == "smiles":
        try:
            standardized = standardize_smiles(request.query).as_dict()
        except (ValueError, RuntimeError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        with connect() as db:
            row = db.execute(
                """SELECT c.compound_id, c.preferred_name, c.smiles, c.inchi_key,
                          p.standardized_smiles, p.molecular_formula, p.molecular_weight
                   FROM compound c JOIN compound_property p USING (compound_id)
                   WHERE p.structure_hash = ? OR p.standardized_smiles = ? LIMIT 1""",
                (standardized["structure_hash"], standardized["standardized_smiles"]),
            ).fetchone()
            target = dict(row) if row else None
    else:
        target = resolve_compound(request.query)
    if not target:
        return {
            "resolved": False,
            "query": request.query,
            "standardized": standardized,
            "coverage": "not_in_local_graph",
            "message": "The input is valid but has no reviewed local graph record. Import and review evidence before route generation.",
        }
    with connect() as db:
        reaction_count = db.execute(
            """SELECT count(*) FROM reaction_participant p JOIN reaction_instance r USING (reaction_id)
               WHERE p.compound_id = ? AND p.role = 'produced' AND r.review_status = 'accepted'""",
            (target["compound_id"],),
        ).fetchone()[0]
    return {"resolved": True, "target": target, "reviewed_producing_reactions": reaction_count}


@app.post("/api/routes/generate")
def routes_generate(request: RouteGenerateRequest) -> dict:
    target = compound_by_id(request.compound_id) if request.compound_id else resolve_compound(request.query or "")
    if not target:
        raise HTTPException(status_code=404, detail="Target is not present in the reviewed local graph")
    routes = generate_routes(
        target["compound_id"], request.constraints.max_steps, request.constraints.max_routes,
        request.constraints.excluded_compound_ids, request.constraints.excluded_hazard_codes,
    )
    if not routes:
        return {
            "target": target,
            "routes": [],
            "coverage_gap": True,
            "message": "No complete evidence-bounded route connects this target to reviewed starting materials under the selected constraints.",
        }
    for route in routes:
        request_basis = f"{route['route_id']}|{request.target_mass_g}|{request.base_currency}|{request.fx_date or 'latest'}"
        route["route_id"] = "route-" + hashlib.sha256(request_basis.encode()).hexdigest()[:16]
        route["evaluation"] = evaluate_route(route, request.target_mass_g, request.base_currency, request.fx_date)
    rank_evaluated(routes)
    now = datetime.now(UTC).isoformat()
    with transaction() as db:
        for route in routes:
            evaluation = route["evaluation"]
            db.execute(
                """INSERT OR REPLACE INTO route_candidate
                (route_candidate_id, target_compound_id, target_mass_g, base_currency,
                 generated_at, algorithm_version, request_json, route_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (route["route_id"], target["compound_id"], request.target_mass_g,
                 request.base_currency, now, route["algorithm_version"], request.model_dump_json(),
                 json.dumps(route)),
            )
            db.execute(
                """INSERT OR REPLACE INTO route_evaluation
                (route_candidate_id, actual_material_cost, actual_cost_coverage,
                 relative_cost_index, feasibility_score, rank_score, rank_tier,
                 cost_basis_json, evaluated_at, scorer_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (route["route_id"], evaluation["actual_material_cost"],
                 evaluation["actual_cost_coverage"], evaluation["relative_cost_index"],
                 evaluation["feasibility_score"], evaluation["rank_score"],
                 evaluation["rank_tier"], json.dumps(evaluation), now, evaluation["scorer_version"]),
            )
    return {
        "target": target,
        "target_mass_g": request.target_mass_g,
        "base_currency": request.base_currency,
        "routes": routes,
        "coverage_gap": False,
        "disclaimer": "Decision support only. Synthetic fixtures are not scientific evidence or manufacturing instructions.",
    }


@app.post("/api/routes/compare")
def routes_compare(request: RouteCompareRequest) -> dict:
    with connect() as db:
        records = []
        for route_id in request.route_ids:
            row = db.execute(
                """SELECT c.route_candidate_id, c.target_compound_id, c.target_mass_g,
                          c.base_currency, c.route_json, e.cost_basis_json
                   FROM route_candidate c JOIN route_evaluation e USING (route_candidate_id)
                   WHERE c.route_candidate_id = ?""",
                (route_id,),
            ).fetchone()
            if row:
                route = json.loads(row["route_json"])
                route["evaluation"] = json.loads(row["cost_basis_json"])
                records.append(route)
    if len(records) != len(request.route_ids):
        raise HTTPException(status_code=404, detail="One or more route IDs were not found")
    return {"routes": rank_evaluated(records), "comparable": True}


@app.get("/api/routes/{route_id}")
def route_get(route_id: str) -> dict:
    with connect() as db:
        row = db.execute(
            """SELECT c.*, e.cost_basis_json FROM route_candidate c
               JOIN route_evaluation e USING (route_candidate_id)
               WHERE route_candidate_id = ?""",
            (route_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Route not found")
    route = json.loads(row["route_json"])
    route["evaluation"] = json.loads(row["cost_basis_json"])
    return route


@app.get("/api/graph/subgraph")
def graph_subgraph(compound_id: str = Query(...), depth: int = Query(default=2, ge=1, le=4)) -> dict:
    del depth  # MVP returns the directly supported neighborhood; depth is reserved for expansion.
    with connect() as db:
        reaction_rows = db.execute(
            """SELECT DISTINCT r.reaction_id FROM reaction_instance r
               JOIN reaction_participant p USING (reaction_id)
               WHERE p.compound_id = ? AND r.review_status = 'accepted'""",
            (compound_id,),
        ).fetchall()
        reaction_ids = [row[0] for row in reaction_rows]
        if not reaction_ids:
            compound = compound_by_id(compound_id)
            return {"nodes": [{"id": f"compound:{compound_id}", "type": "compound", "label": compound["preferred_name"] if compound else compound_id}], "edges": []}
        placeholders = ",".join("?" for _ in reaction_ids)
        participants = db.execute(
            f"""SELECT p.*, c.preferred_name, r.reaction_name FROM reaction_participant p
                JOIN compound c USING (compound_id) JOIN reaction_instance r USING (reaction_id)
                WHERE p.reaction_id IN ({placeholders})""",
            reaction_ids,
        ).fetchall()
    nodes = {}
    edges = []
    for row in participants:
        item = dict(row)
        nodes[f"compound:{item['compound_id']}"] = {"id": f"compound:{item['compound_id']}", "type": "compound", "label": item["preferred_name"]}
        nodes[f"reaction:{item['reaction_id']}"] = {"id": f"reaction:{item['reaction_id']}", "type": "reaction", "label": item["reaction_name"]}
        source, target = ((f"reaction:{item['reaction_id']}", f"compound:{item['compound_id']}") if item["role"] == "produced" else (f"compound:{item['compound_id']}", f"reaction:{item['reaction_id']}"))
        edges.append({"source": source, "target": target, "type": item["role"]})
    return {"nodes": list(nodes.values()), "edges": edges}


@app.post("/api/prices/import")
def price_import(request: PriceImportRequest) -> dict:
    now = datetime.now(UTC).isoformat()
    imported = 0
    with transaction() as db:
        for quote in request.quotes:
            if not db.execute("SELECT 1 FROM compound WHERE compound_id = ?", (quote.compound_id,)).fetchone():
                raise HTTPException(status_code=422, detail=f"Unknown compound_id: {quote.compound_id}")
            db.execute(
                """INSERT OR IGNORE INTO supplier
                (supplier_id, supplier_name, review_status) VALUES (?, ?, 'needs_review')""",
                (quote.supplier_id, quote.supplier_name),
            )
            payload = quote.model_dump()
            db.execute(
                """INSERT OR REPLACE INTO material_quote
                (quote_id, compound_id, supplier_id, source_url, observed_at, currency,
                 geography, purity_percent, pack_size_value, pack_size_unit,
                 available_quantity_value, available_quantity_unit, price, imported_at,
                 raw_record_json, review_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (quote.quote_id, quote.compound_id, quote.supplier_id, quote.source_url,
                 quote.observed_at, quote.currency.upper(), quote.geography, quote.purity_percent,
                 quote.pack_size_value, quote.pack_size_unit, quote.available_quantity_value,
                 quote.available_quantity_unit, quote.price, now, json.dumps(payload),
                 quote.review_status),
            )
            imported += 1
    return {"imported": imported, "message": "Quotes remain review-gated until explicitly accepted."}


@app.post("/api/extraction/qroq")
async def qroq_extraction(request: QroqExtractionRequest) -> dict:
    try:
        return await extract(request.source_text, request.source_url, request.model)
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Extraction failed: {error}") from error
