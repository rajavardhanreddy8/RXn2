from __future__ import annotations

import json
import sqlite3
from collections import Counter, deque
from datetime import UTC, datetime
from xml.sax.saxutils import escape

from .db import connect, transaction
from .chemistry import screen_atom_conservation


def _node_id(kind: str, record_id: object) -> str:
    return f"{kind}:{record_id}"


def _insert_nodes(db: sqlite3.Connection, sql: str) -> None:
    db.execute(sql)


def rebuild_graph_projection() -> dict:
    """Atomically rebuild the query projection from authoritative curated tables."""
    with transaction() as db:
        db.execute("DELETE FROM graph_edge")
        db.execute("DELETE FROM graph_node")

        node_sql = (
            ("drug", "drug_entity", "drug_id", "preferred_name", "review_status"),
            ("moiety", "active_moiety", "active_moiety_id", "preferred_name", "review_status"),
            ("compound", "compound", "compound_id", "coalesce(preferred_name, inchi_key, compound_id)", "review_status"),
            ("product", "regulatory_product", "regulatory_product_id", "coalesce(trade_name, application_number, regulatory_product_id)", "'identified'"),
            ("patent_family", "patent_family", "family_id", "family_id", "'identified'"),
            ("patent", "patent_document", "publication_number", "coalesce(title, publication_number)", "'identified'"),
            ("evidence", "evidence_span", "evidence_span_id", "coalesce(section_type, paragraph_id, evidence_span_id)", "review_status"),
            ("element", "element", "element_id", "symbol || ' · ' || name", "'identified'"),
            ("functional_group", "functional_group", "functional_group_id", "preferred_name", "'identified'"),
            ("reaction", "reaction_instance", "reaction_id", "coalesce(reaction_name, transformation_key, reaction_id)", "review_status"),
            ("route", "process_route", "route_id", "route_id", "review_status"),
        )
        for kind, table, key, label, review in node_sql:
            _insert_nodes(db, f"""INSERT INTO graph_node
                (node_id,node_type,record_id,label,source_table,review_status,properties_json)
                SELECT '{kind}:' || {key},'{kind}',cast({key} as text),{label},'{table}',
                       coalesce({review},'needs_review'),'{{}}' FROM {table}""")

        db.execute("""INSERT OR IGNORE INTO graph_node
            SELECT 'procedure:'||evidence_span_id,'procedure',evidence_span_id,
                   coalesce(section_type,paragraph_id,evidence_span_id),'evidence_span',
                   coalesce(review_status,'needs_review'),json_object('publication_number',publication_number)
            FROM evidence_span""")

        # Material/fact mentions are real evidence-offset records even when unresolved.
        for side in ("subject", "object"):
            db.execute(f"""INSERT OR IGNORE INTO graph_node
                SELECT 'mention:'||relation_candidate_id||':{side}',
                       {side}_type,relation_candidate_id||':{side}',{side}_text,
                       'relation_candidate',review_status,
                       json_object('validation_status',validation_status,'evidence_span_id',evidence_span_id)
                FROM relation_candidate
                WHERE NOT ({side}_type='compound' AND {side}_compound_id IS NOT NULL)
                  AND {side}_type NOT IN ('patent','procedure')""")

        edges = (
            """INSERT INTO graph_edge SELECT 'drug-moiety:'||drug_id,
               'drug:'||drug_id,'moiety:'||active_moiety_id,'has_active_moiety',
               'drug_entity',drug_id,'validated',CASE WHEN review_status='accepted' THEN 'identified' ELSE review_status END,NULL,NULL,'{}'
               FROM drug_entity WHERE active_moiety_id IS NOT NULL""",
            """INSERT INTO graph_edge SELECT 'drug-compound:'||drug_id||':'||compound_id||':'||relationship_type,
               'drug:'||drug_id,'compound:'||compound_id,relationship_type,
               'drug_compound',drug_id||':'||compound_id,'validated',CASE WHEN review_status='accepted' THEN 'identified' ELSE review_status END,NULL,NULL,'{}'
               FROM drug_compound""",
            """INSERT INTO graph_edge SELECT 'moiety-compound:'||active_moiety_id||':'||compound_id,
               'moiety:'||active_moiety_id,'compound:'||compound_id,'represented_by',
               'compound',compound_id,'validated',CASE WHEN review_status='accepted' THEN 'identified' ELSE review_status END,NULL,NULL,'{}'
               FROM compound WHERE active_moiety_id IS NOT NULL""",
            """INSERT INTO graph_edge SELECT 'product-drug:'||regulatory_product_id||':'||drug_id||':'||relationship_type,
               'product:'||regulatory_product_id,'drug:'||drug_id,relationship_type,
               'regulatory_product_drug',regulatory_product_id||':'||drug_id,'validated','identified',NULL,NULL,'{}'
               FROM regulatory_product_drug""",
            """INSERT INTO graph_edge SELECT 'family-patent:'||family_id||':'||publication_number,
               'patent_family:'||family_id,'patent:'||publication_number,'has_publication',
               'patent_family_member',family_id||':'||publication_number,'validated','identified',NULL,NULL,
               json_object('relationship',relationship) FROM patent_family_member""",
            """INSERT INTO graph_edge SELECT 'candidate-drug-patent:'||candidate_id,
               'drug:'||drug_id,'patent:'||publication_number,'patent_candidate',
               'patent_candidate',candidate_id,'validated',review_status,confidence,NULL,
               json_object('match_type',match_type,'field',source_field_name) FROM patent_candidate""",
            """INSERT INTO graph_edge SELECT 'candidate-compound-patent:'||candidate_id,
               'compound:'||compound_id,'patent:'||publication_number,'mentioned_in_patent',
               'patent_candidate',candidate_id,'validated',review_status,confidence,NULL,
               json_object('match_type',match_type,'field',source_field_name)
               FROM patent_candidate WHERE compound_id IS NOT NULL""",
            """INSERT INTO graph_edge SELECT 'patent-evidence:'||evidence_span_id,
               'patent:'||publication_number,'evidence:'||evidence_span_id,'has_evidence',
               'evidence_span',evidence_span_id,'validated','identified',NULL,evidence_span_id,
               json_object('section_type',section_type,'paragraph_id',paragraph_id) FROM evidence_span""",
            """INSERT INTO graph_edge SELECT 'evidence-procedure:'||evidence_span_id,
               'evidence:'||evidence_span_id,'procedure:'||evidence_span_id,'describes_procedure',
               'evidence_span',evidence_span_id,'validated','identified',NULL,evidence_span_id,'{}'
               FROM evidence_span""",
            """INSERT INTO graph_edge SELECT 'compound-element:'||ce.compound_id||':'||ce.element_id,
               'compound:'||ce.compound_id,'element:'||ce.element_id,'contains_element',
               'compound_element',ce.compound_id||':'||ce.element_id,'validated','identified',NULL,NULL,
               json_object('atom_count',ce.atom_count) FROM compound_element ce""",
            """INSERT INTO graph_edge SELECT 'compound-fg:'||compound_id||':'||functional_group_id||':'||detector_version,
               'compound:'||compound_id,'functional_group:'||functional_group_id,'has_functional_group',
               'compound_functional_group',compound_id||':'||functional_group_id,'validated','identified',NULL,NULL,
               json_object('match_count',match_count,'detector_version',detector_version) FROM compound_functional_group""",
            """INSERT INTO graph_edge SELECT 'reaction-participant:'||rp.reaction_id||':'||rp.compound_id||':'||rp.role,
               CASE WHEN rp.role IN ('reactant','consumed','reagent','catalyst','solvent','workup') THEN 'compound:'||rp.compound_id ELSE 'reaction:'||rp.reaction_id END,
               CASE WHEN rp.role IN ('reactant','consumed','reagent','catalyst','solvent','workup') THEN 'reaction:'||rp.reaction_id ELSE 'compound:'||rp.compound_id END,
               rp.role,'reaction_participant',rp.reaction_id||':'||rp.compound_id||':'||rp.role,'validated',ri.review_status,NULL,ri.evidence_span_id,
               json_object('stoichiometry',rp.stoichiometry,'amount_value',rp.amount_value,'amount_unit',rp.amount_unit)
               FROM reaction_participant rp JOIN reaction_instance ri USING(reaction_id)""",
            """INSERT INTO graph_edge SELECT 'reaction-evidence:'||rel.reaction_id||':'||rel.evidence_span_id||':'||rel.relationship_type,
               'reaction:'||rel.reaction_id,'evidence:'||rel.evidence_span_id,rel.relationship_type,
               'reaction_evidence_link',rel.reaction_id||':'||rel.evidence_span_id,'validated',
               CASE WHEN ri.review_status='accepted' THEN 'accepted' ELSE 'needs_review' END,
               NULL,rel.evidence_span_id,'{}' FROM reaction_evidence_link rel
               JOIN reaction_instance ri USING(reaction_id)""",
            """INSERT INTO graph_edge SELECT 'route-reaction:'||ps.step_id,
               'route:'||ps.route_id,'reaction:'||ri.reaction_id,'has_step',
               'process_step',ps.step_id,'validated',ri.review_status,NULL,ps.evidence_span_id,
               json_object('step_order',ps.step_order) FROM process_step ps
               JOIN reaction_instance ri ON ri.evidence_span_id=ps.evidence_span_id""",
        )
        for statement in edges:
            db.execute(statement)

        # Project extracted relations with resolved entities when available.
        db.execute("""INSERT INTO graph_edge
            SELECT 'relation:'||relation_candidate_id,
              CASE
                WHEN subject_type='compound' AND subject_compound_id IS NOT NULL THEN 'compound:'||subject_compound_id
                WHEN subject_type='patent' THEN 'patent:'||subject_text
                WHEN subject_type='procedure' THEN 'procedure:'||evidence_span_id
                ELSE 'mention:'||relation_candidate_id||':subject' END,
              CASE
                WHEN object_type='compound' AND object_compound_id IS NOT NULL THEN 'compound:'||object_compound_id
                WHEN object_type='patent' THEN 'patent:'||object_text
                WHEN object_type='procedure' THEN 'procedure:'||evidence_span_id
                ELSE 'mention:'||relation_candidate_id||':object' END,
              predicate,'relation_candidate',relation_candidate_id,validation_status,
              review_status,model_confidence,evidence_span_id,coalesce(attributes_json,'{}')
            FROM relation_candidate""")

        counts = {
            "nodes": db.execute("SELECT count(*) FROM graph_node").fetchone()[0],
            "edges": db.execute("SELECT count(*) FROM graph_edge").fetchone()[0],
        }
    return {**counts, "built_at": datetime.now(UTC).isoformat(), "automatic_acceptance": False}


def graph_stats() -> dict:
    with connect() as db:
        nodes = [dict(row) for row in db.execute(
            "SELECT node_type,count(*) count FROM graph_node GROUP BY node_type ORDER BY count DESC"
        )]
        edges = [dict(row) for row in db.execute(
            """SELECT predicate,validation_status,review_status,count(*) count
               FROM graph_edge GROUP BY predicate,validation_status,review_status ORDER BY count DESC"""
        )]
    return {
        "node_count": sum(row["count"] for row in nodes),
        "edge_count": sum(row["count"] for row in edges),
        "nodes_by_type": nodes, "edges_by_type": edges,
        "automatic_acceptance": False,
    }


def graph_overview(node_type: str | None = None, statuses: set[str] | None = None,
                   direction: str = "both", depth: int = 1) -> dict:
    """Return an aggregated, filterable type-level graph.

    A global graph cannot sensibly apply a directional hop without an anchor.
    When a node type is selected, it becomes that anchor and we walk the
    type-to-type aggregate graph. This keeps the overview small while making
    every toolbar control meaningful.
    """
    statuses = statuses or {"validated", "unresolved", "rejected"}
    with connect() as db:
        all_nodes = [dict(row) for row in db.execute(
            """SELECT node_type id,replace(node_type,'_',' ') label,count(*) count
               FROM graph_node GROUP BY node_type ORDER BY count DESC"""
        )]
        marks = ",".join("?" for _ in statuses)
        all_edges = [dict(row) for row in db.execute(
            """SELECT substr(source_node_id,1,instr(source_node_id,':')-1) source,
                      substr(target_node_id,1,instr(target_node_id,':')-1) target,
                      predicate,validation_status,count(*) count
               FROM graph_edge
               WHERE validation_status IN (""" + marks + """)
               GROUP BY source,target,predicate,validation_status
               ORDER BY count DESC"""
        , sorted(statuses))]

    if node_type:
        known = {row["id"] for row in all_nodes}
        if node_type not in known:
            return {"nodes": [], "edges": [], "automatic_acceptance": False}
        included, frontier = {node_type}, {node_type}
        for _ in range(depth):
            next_frontier = set()
            for edge in all_edges:
                if direction in {"both", "outgoing"} and edge["source"] in frontier:
                    next_frontier.add(edge["target"])
                if direction in {"both", "incoming"} and edge["target"] in frontier:
                    next_frontier.add(edge["source"])
            next_frontier -= included
            included.update(next_frontier)
            frontier = next_frontier
            if not frontier:
                break
        nodes = [row for row in all_nodes if row["id"] in included]
        edges = [row for row in all_edges if row["source"] in included and row["target"] in included]
    else:
        nodes, edges = all_nodes, all_edges
    return {"nodes": nodes, "edges": edges, "automatic_acceptance": False}


def graph_search(query: str, node_type: str | None, limit: int) -> list[dict]:
    where, values = ["label LIKE ? COLLATE NOCASE"], [f"%{query.strip()}%"]
    if node_type:
        where.append("node_type=?")
        values.append(node_type)
    values.append(limit)
    with connect() as db:
        return [dict(row) for row in db.execute(
            f"""SELECT node_id,node_type,label,review_status,properties_json
                 FROM graph_node WHERE {' AND '.join(where)}
                 ORDER BY CASE WHEN label LIKE ? COLLATE NOCASE THEN 0 ELSE 1 END,label LIMIT ?""",
            [*values[:-1], f"{query.strip()}%", values[-1]],
        )]


def graph_projection_page(kind: str, offset: int, limit: int,
                          statuses: set[str] | None = None) -> dict:
    """Return one deterministic page of the complete derived projection.

    This is deliberately a page API rather than a second visual-only graph.
    A client that explicitly asks for the full graph can assemble every stored
    node and edge without changing the curated database or fabricating links.
    """
    if kind not in {"nodes", "edges"}:
        raise ValueError("kind must be nodes or edges")
    table = "graph_node" if kind == "nodes" else "graph_edge"
    order = "node_id" if kind == "nodes" else "edge_id"
    with connect() as db:
        if kind == "edges":
            included = statuses or {"validated", "unresolved", "rejected"}
            marks = ",".join("?" for _ in included)
            total = db.execute(
                f"SELECT count(*) FROM {table} WHERE validation_status IN ({marks})", sorted(included)
            ).fetchone()[0]
            rows = db.execute(
                f"SELECT * FROM {table} WHERE validation_status IN ({marks}) ORDER BY {order} LIMIT ? OFFSET ?",
                [*sorted(included), limit, offset],
            ).fetchall()
        else:
            total = db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            rows = db.execute(
                f"SELECT * FROM {table} ORDER BY {order} LIMIT ? OFFSET ?", (limit, offset)
            ).fetchall()
    return {"kind": kind, "offset": offset, "limit": limit, "total": total,
            "items": [dict(row) for row in rows], "automatic_acceptance": False}


_PROCESS_TERMS = {
    "formulation_or_dosage": (
        "tablet", "capsule", "granule", "syrup", "cream", "ointment",
        "suspension", "dosage form", "formulation", "pharmaceutical composition",
        "excipient", "coating composition",
    ),
    "analytical": (
        "hplc", "chromatogram", "spectroscopy", "assay method",
        "analytical method", "dissolution test", "particle size analysis",
    ),
    "solid_form_or_purification": (
        "crystalline form", "crystal form", "polymorph", "recrystall",
        "seeded with", "purified by", "column chromatography",
        "preparative chromatography",
    ),
}


def _text_process_flags(evidence_text: str) -> list[str]:
    text = (evidence_text or "").casefold()
    return sorted(
        process_class for process_class, terms in _PROCESS_TERMS.items()
        if any(term in text for term in terms)
    )


def _process_class(consumed_key: str, produced_key: str) -> str:
    if consumed_key == produced_key:
        return "isolation_or_workup"
    # The first InChIKey block represents molecular connectivity. A changed
    # stereochemical/protonation layer is a distinct manufacturing operation,
    # but it is not presented as a new covalent synthesis edge.
    if "-" in consumed_key and "-" in produced_key:
        if consumed_key.split("-", 1)[0] == produced_key.split("-", 1)[0]:
            return "salt_stereoisomer_or_solid_form"
    return "synthetic_transformation_candidate"


def _structure_key(row: sqlite3.Row, prefix: str) -> str | None:
    return row[f"{prefix}_inchi_key"] or row[f"{prefix}_smiles"]


def graph_route_map(
    statuses: set[str] | None = None,
    collapsed: bool = True,
    process_layer: str = "core",
) -> dict:
    """Return a structure-gated route graph separated by manufacturing class.

    ``core`` contains molecule-to-molecule synthesis. ``support`` contains
    solid-form, purification, formulation, dosage and isolation processes.
    All relations remain evidence-linked and review-gated.
    """
    if process_layer not in {"core", "candidates", "support", "all"}:
        raise ValueError("process_layer must be core, candidates, support or all")
    visible_statuses = statuses or {"validated", "unresolved"}

    with connect() as db:
        rows = db.execute(
            """SELECT c.relation_candidate_id consumed_relation_id,
                      p.relation_candidate_id produced_relation_id,
                      c.evidence_span_id,e.evidence_text,
                      c.subject_compound_id consumed_compound_id,
                      p.object_compound_id produced_compound_id,
                      ci.inchi_key consumed_inchi_key,co.inchi_key produced_inchi_key,
                      coalesce(cip.standardized_smiles,ci.smiles) consumed_smiles,
                      coalesce(cop.standardized_smiles,co.smiles) produced_smiles,
                      min(c.model_confidence,p.model_confidence) confidence
                 FROM relation_candidate c
                 JOIN relation_candidate p
                   ON p.evidence_span_id=c.evidence_span_id AND p.predicate='produced'
                 JOIN evidence_span e ON e.evidence_span_id=c.evidence_span_id
                 JOIN compound ci ON ci.compound_id=c.subject_compound_id
                 JOIN compound co ON co.compound_id=p.object_compound_id
                 LEFT JOIN compound_property cip ON cip.compound_id=ci.compound_id
                 LEFT JOIN compound_property cop ON cop.compound_id=co.compound_id
                 WHERE c.predicate='consumed'
                   AND c.validation_status='validated'
                   AND p.validation_status='validated'
                   AND EXISTS (
                     SELECT 1 FROM relation_candidate d
                     WHERE d.evidence_span_id=c.evidence_span_id
                       AND d.predicate='describes'
                       AND d.validation_status='validated'
                       AND json_extract(d.attributes_json,'$.procedure_type')='performed'
                       AND coalesce(json_array_length(json_extract(d.attributes_json,'$.conflicts')),0)=0
                   )
                   AND (SELECT count(DISTINCT px.object_compound_id)
                        FROM relation_candidate px
                        WHERE px.evidence_span_id=c.evidence_span_id
                          AND px.predicate='produced'
                          AND px.validation_status='validated'
                          AND px.object_compound_id IS NOT NULL)=1
                 ORDER BY c.evidence_span_id,c.relation_candidate_id"""
        ).fetchall()

        raw_edges: list[dict] = []
        node_ids: set[str] = set()
        accounting = Counter()
        promoted_accounting = Counter()
        promoted_validation = Counter()
        for row in rows:
            consumed_key = _structure_key(row, "consumed")
            produced_key = _structure_key(row, "produced")
            if not consumed_key or not produced_key:
                accounting["structure_missing"] += 1
                continue
            process_class = _process_class(consumed_key, produced_key)
            accounting[process_class] += 1
            is_candidate = process_class == "synthetic_transformation_candidate"
            if process_layer == "core":
                continue
            if process_layer == "candidates" and not is_candidate:
                continue
            if process_layer == "support" and is_candidate:
                continue
            consumed_id = f"compound:{row['consumed_compound_id']}"
            produced_id = f"compound:{row['produced_compound_id']}"
            procedure_id = f"procedure:{row['evidence_span_id']}"
            node_ids.update((consumed_id, produced_id, procedure_id))
            properties = json.dumps({
                "process_class": process_class,
                "text_process_flags": _text_process_flags(row["evidence_text"]),
                "chemistry_validation": "pending_atom_mapping",
            }, sort_keys=True)
            raw_edges.extend((
                {
                    "edge_id": f"strict-consumed:{row['consumed_relation_id']}",
                    "source_node_id": consumed_id, "target_node_id": procedure_id,
                    "predicate": "consumed", "source_table": "relation_candidate",
                    "source_record_id": row["consumed_relation_id"],
                    "validation_status": "unresolved", "review_status": "needs_review",
                    "confidence": row["confidence"], "evidence_span_id": row["evidence_span_id"],
                    "properties_json": properties,
                },
                {
                    "edge_id": f"strict-produced:{row['produced_relation_id']}",
                    "source_node_id": procedure_id, "target_node_id": produced_id,
                    "predicate": "produced", "source_table": "relation_candidate",
                    "source_record_id": row["produced_relation_id"],
                    "validation_status": "unresolved", "review_status": "needs_review",
                    "confidence": row["confidence"], "evidence_span_id": row["evidence_span_id"],
                    "properties_json": properties,
                },
            ))

        # Promoted reaction instances are classified independently from the
        # loose relation candidates. They remain provisional until chemistry
        # review, but their route/display class is deterministic.
        curated_records: dict[str, dict] = {}
        for row in db.execute(
            """SELECT ri.reaction_id,
                      ci.inchi_key consumed_inchi_key,co.inchi_key produced_inchi_key,
                      coalesce(cip.standardized_smiles,ci.smiles) consumed_smiles,
                      coalesce(cop.standardized_smiles,co.smiles) produced_smiles
                 FROM reaction_instance ri
                 JOIN reaction_participant c
                   ON c.reaction_id=ri.reaction_id AND c.role IN ('reactant','consumed')
                 JOIN reaction_participant p
                   ON p.reaction_id=ri.reaction_id AND p.role IN ('product','produced')
                 JOIN compound ci ON ci.compound_id=c.compound_id
                 JOIN compound co ON co.compound_id=p.compound_id
                 LEFT JOIN compound_property cip ON cip.compound_id=ci.compound_id
                 LEFT JOIN compound_property cop ON cop.compound_id=co.compound_id
                 WHERE ri.review_status<>'rejected'
                   AND (SELECT count(DISTINCT px.compound_id)
                        FROM reaction_participant px
                        WHERE px.reaction_id=ri.reaction_id
                          AND px.role IN ('product','produced'))=1"""
        ):
            record = curated_records.setdefault(row["reaction_id"], {
                "consumed_keys": [], "consumed_smiles": [],
                "produced_key": _structure_key(row, "produced"),
                "produced_smiles": row["produced_smiles"],
            })
            consumed_key = _structure_key(row, "consumed")
            if consumed_key and consumed_key not in record["consumed_keys"]:
                record["consumed_keys"].append(consumed_key)
            if row["consumed_smiles"] and row["consumed_smiles"] not in record["consumed_smiles"]:
                record["consumed_smiles"].append(row["consumed_smiles"])

        curated_metadata: dict[str, dict] = {}
        for reaction_id, record in curated_records.items():
            if not record["consumed_keys"] or not record["produced_key"]:
                promoted_validation["missing_resolved_structure"] += 1
                continue
            process_class = "synthetic_transformation_candidate"
            product_parts = set((record["produced_smiles"] or "").split("."))
            if any(smiles in product_parts for smiles in record["consumed_smiles"]):
                process_class = "salt_stereoisomer_or_solid_form"
            for consumed_key in record["consumed_keys"]:
                candidate_class = _process_class(consumed_key, record["produced_key"])
                if candidate_class != "synthetic_transformation_candidate":
                    process_class = candidate_class
                    break
            if process_class == "synthetic_transformation_candidate":
                process_class = "synthetic_transformation"
            screen = screen_atom_conservation(
                record["consumed_smiles"], record["produced_smiles"]
            )
            curated_metadata[reaction_id] = {
                "process_class": process_class,
                "chemistry_validation": screen.status,
                "chemistry_validation_reason": screen.reason,
                "missing_product_atoms": screen.missing_product_atoms,
                "atom_mapping_status": screen.atom_mapping_status,
            }
            promoted_accounting[process_class] += 1
            promoted_validation[screen.status] += 1

        selected_reactions = {
            reaction_id: metadata
            for reaction_id, metadata in curated_metadata.items()
            if process_layer == "all"
            or (
                process_layer == "core"
                and metadata["process_class"] == "synthetic_transformation"
                and metadata["chemistry_validation"] == "validated"
            )
            or (
                process_layer == "candidates"
                and metadata["process_class"] == "synthetic_transformation"
                and metadata["chemistry_validation"] != "validated"
            )
            or (
                process_layer == "support"
                and metadata["process_class"] != "synthetic_transformation"
            )
        }
        if selected_reactions:
            reaction_marks = ",".join("?" for _ in selected_reactions)
            curated = [dict(row) for row in db.execute(
                f"""SELECT ge.* FROM graph_edge ge
                     JOIN reaction_instance ri
                       ON ge.source_node_id='reaction:'||ri.reaction_id
                       OR ge.target_node_id='reaction:'||ri.reaction_id
                     WHERE ge.source_table='reaction_participant'
                       AND ge.validation_status='validated'
                       AND ri.reaction_id IN ({reaction_marks})
                       AND ge.predicate IN ('reactant','consumed','product','produced')
                     ORDER BY ge.source_node_id,ge.target_node_id,ge.predicate""",
                sorted(selected_reactions),
            )]
            for edge in curated:
                reaction_id = (
                    edge["source_node_id"] if edge["source_node_id"].startswith("reaction:")
                    else edge["target_node_id"]
                ).removeprefix("reaction:")
                properties = json.loads(edge["properties_json"] or "{}")
                properties.update(selected_reactions[reaction_id])
                edge["properties_json"] = json.dumps(properties, sort_keys=True)
                screen_status = selected_reactions[reaction_id]["chemistry_validation"]
                edge["validation_status"] = (
                    screen_status if screen_status in {"validated", "unresolved", "rejected"}
                    else "unresolved"
                )
            raw_edges.extend(curated)
            node_ids.update(edge[key] for edge in curated for key in ("source_node_id", "target_node_id"))

        # A multi-input procedure joins each input to the same product row.
        # Deduplicate the shared product edge before contracting the junction,
        # otherwise the visual route graph multiplies identical transitions.
        raw_edges = [
            edge for edge in {edge["edge_id"]: edge for edge in raw_edges}.values()
            if edge["validation_status"] in visible_statuses
        ]

        if node_ids:
            marks = ",".join("?" for _ in node_ids)
            nodes = [dict(row) for row in db.execute(
                f"SELECT * FROM graph_node WHERE node_id IN ({marks})", sorted(node_ids),
            )]
        else:
            nodes = []

    metadata = {
        "process_layer": process_layer,
        "candidate_pair_class_counts": dict(sorted(accounting.items())),
        "promoted_reaction_class_counts": dict(sorted(promoted_accounting.items())),
        "promoted_validation_counts": dict(sorted(promoted_validation.items())),
        "automatic_acceptance": False,
        "note": (
            "Core synthesis contains promoted synthetic reaction records. Unpromoted "
            "structure-resolved extractions and supporting manufacturing are separate."
        ),
    }
    if not collapsed:
        return {"nodes": nodes, "edges": raw_edges, "truncated": False, **metadata}

    incoming: dict[str, list[dict]] = {}
    outgoing: dict[str, list[dict]] = {}
    for edge in raw_edges:
        if edge["predicate"] in {"reactant", "consumed"}:
            incoming.setdefault(edge["target_node_id"], []).append(edge)
        elif edge["predicate"] in {"product", "produced"}:
            outgoing.setdefault(edge["source_node_id"], []).append(edge)
    material_ids: set[str] = set()
    transitions: list[dict] = []
    for junction_id in sorted(incoming.keys() & outgoing.keys()):
        for consumed in incoming[junction_id]:
            for produced in outgoing[junction_id]:
                if consumed["source_node_id"] == produced["target_node_id"] and process_layer in {"core", "candidates"}:
                    continue
                material_ids.update((consumed["source_node_id"], produced["target_node_id"]))
                properties = json.loads(produced["properties_json"] or "{}")
                properties.setdefault("process_class", "synthetic_transformation")
                properties.update({
                    "via_node_id": junction_id,
                    "consumed_edge_id": consumed["edge_id"],
                    "produced_edge_id": produced["edge_id"],
                })
                values = [value for value in (consumed["confidence"], produced["confidence"]) if value is not None]
                transitions.append({
                    "edge_id": f"route-transition:{junction_id}:{consumed['edge_id']}:{produced['edge_id']}",
                    "source_node_id": consumed["source_node_id"],
                    "target_node_id": produced["target_node_id"],
                    "predicate": "transforms_to" if properties["process_class"].startswith("synthetic_transformation") else "manufacturing_process",
                    "source_table": "route_projection", "source_record_id": junction_id,
                    "validation_status": (
                        properties.get("chemistry_validation")
                        if properties.get("chemistry_validation") in {"validated", "unresolved", "rejected"}
                        else "unresolved"
                    ),
                    "review_status": (
                        "accepted" if consumed["review_status"] == produced["review_status"] == "accepted"
                        else "needs_review"
                    ),
                    "confidence": min(values) if values else None,
                    "evidence_span_id": produced["evidence_span_id"] or consumed["evidence_span_id"],
                    "properties_json": json.dumps(properties, sort_keys=True),
                })
    material_nodes = [node for node in nodes if node["node_id"] in material_ids]
    return {"nodes": material_nodes, "edges": transitions, "truncated": False, **metadata}


def graph_neighborhood(node_id: str, depth: int, node_limit: int, edge_limit: int,
                       statuses: set[str], direction: str = "both") -> dict:
    seen, frontier, edges = {node_id}, {node_id}, []
    with connect() as db:
        for _ in range(depth):
            if not frontier or len(seen) >= node_limit or len(edges) >= edge_limit:
                break
            marks = ",".join("?" for _ in frontier)
            status_marks = ",".join("?" for _ in statuses)
            if direction == "outgoing":
                clause, parameters = f"source_node_id IN ({marks})", [*sorted(frontier)]
            elif direction == "incoming":
                clause, parameters = f"target_node_id IN ({marks})", [*sorted(frontier)]
            else:
                clause, parameters = (
                    f"(source_node_id IN ({marks}) OR target_node_id IN ({marks}))",
                    [*sorted(frontier), *sorted(frontier)],
                )
            rows = db.execute(
                f"""SELECT * FROM graph_edge WHERE validation_status IN ({status_marks})
                    AND {clause} LIMIT ?""",
                [*sorted(statuses), *parameters, edge_limit-len(edges)],
            ).fetchall()
            nxt = set()
            for row in rows:
                item = dict(row); edges.append(item)
                for key in ("source_node_id", "target_node_id"):
                    if item[key] not in seen and len(seen) < node_limit:
                        seen.add(item[key]); nxt.add(item[key])
            frontier = nxt
        marks = ",".join("?" for _ in seen)
        nodes = [dict(row) for row in db.execute(
            f"SELECT * FROM graph_node WHERE node_id IN ({marks})", sorted(seen)
        )] if seen else []
    return {"selected_node": node_id, "nodes": nodes, "edges": edges,
            "truncated": len(nodes) >= node_limit or len(edges) >= edge_limit,
            "automatic_acceptance": False}


def graph_path(source: str, target: str, max_depth: int, statuses: set[str]) -> dict:
    if source == target:
        return {"found": True, "nodes": [source], "edges": []}
    parents: dict[str, tuple[str, dict]] = {}
    queue = deque([(source, 0)]); visited = {source}
    with connect() as db:
        while queue:
            current, depth = queue.popleft()
            if depth >= max_depth:
                continue
            marks = ",".join("?" for _ in statuses)
            rows = db.execute(
                f"""SELECT * FROM graph_edge WHERE validation_status IN ({marks})
                    AND (source_node_id=? OR target_node_id=?) LIMIT 5000""",
                [*sorted(statuses), current, current],
            ).fetchall()
            for row in rows:
                edge = dict(row)
                nxt = edge["target_node_id"] if edge["source_node_id"] == current else edge["source_node_id"]
                if nxt in visited:
                    continue
                visited.add(nxt); parents[nxt] = (current, edge)
                if nxt == target:
                    nodes, path_edges, cursor = [target], [], target
                    while cursor != source:
                        previous, path_edge = parents[cursor]
                        path_edges.append(path_edge); nodes.append(previous); cursor = previous
                    return {"found": True, "nodes": list(reversed(nodes)),
                            "edges": list(reversed(path_edges)), "automatic_acceptance": False}
                queue.append((nxt, depth + 1))
                if len(visited) > 50_000:
                    return {"found": False, "reason": "search_limit_reached", "nodes": [], "edges": []}
    return {"found": False, "reason": "no_path_within_depth", "nodes": [], "edges": []}


def export_graph(node_id: str, depth: int, format_name: str) -> str:
    graph = graph_neighborhood(node_id, depth, 2000, 5000, {"validated", "unresolved", "rejected"}, "both")
    if format_name == "jsonl":
        lines = [json.dumps({"record_type": "node", **node}, ensure_ascii=False) for node in graph["nodes"]]
        lines.extend(json.dumps({"record_type": "edge", **edge}, ensure_ascii=False) for edge in graph["edges"])
        return "\n".join(lines) + "\n"
    node_xml = "".join(
        f'<node id="{escape(node["node_id"])}"><data key="type">{escape(node["node_type"])}</data><data key="label">{escape(node["label"])}</data></node>'
        for node in graph["nodes"]
    )
    edge_xml = "".join(
        f'<edge id="{escape(edge["edge_id"])}" source="{escape(edge["source_node_id"])}" target="{escape(edge["target_node_id"])}"><data key="predicate">{escape(edge["predicate"])}</data></edge>'
        for edge in graph["edges"]
    )
    return '<?xml version="1.0" encoding="UTF-8"?><graphml xmlns="http://graphml.graphdrawing.org/xmlns"><graph edgedefault="directed">' + node_xml + edge_xml + '</graph></graphml>'
