import { readFileSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { deriveScale, normalizePatentNumber } from './core.js';

export function openDatabase(dbPath) {
  const absolute = resolve(dbPath);
  mkdirSync(dirname(absolute), { recursive: true });
  const db = new DatabaseSync(absolute);
  db.exec('PRAGMA foreign_keys = ON;');
  return db;
}

export function initializeDatabase(db, schemaPath = 'sql/schema.sql') {
  db.exec(readFileSync(resolve(schemaPath), 'utf8'));
}

export function registerSources(db, registry) {
  const insert = db.prepare(`
    INSERT INTO source (
      source_id, name, authority, role, collection_mode, runtime_dependency,
      automated_acquisition_allowed, redistribution, license_code, homepage, registry_json
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(source_id) DO UPDATE SET
      name=excluded.name, authority=excluded.authority, role=excluded.role,
      collection_mode=excluded.collection_mode,
      runtime_dependency=excluded.runtime_dependency,
      automated_acquisition_allowed=excluded.automated_acquisition_allowed,
      redistribution=excluded.redistribution, license_code=excluded.license_code,
      homepage=excluded.homepage, registry_json=excluded.registry_json
  `);
  for (const source of registry.sources) {
    insert.run(
      source.id, source.name, source.authority, source.role, source.collection_mode,
      source.runtime_dependency ? 1 : 0, source.automated_acquisition_allowed ? 1 : 0,
      source.redistribution, source.license, source.homepage ?? null, JSON.stringify(source)
    );
  }
}

export function ingestExamples(db, examples, policy, registry, datasetVersion = 'working-fixtures-v1') {
  registerSources(db, registry);
  const now = new Date().toISOString();
  db.exec('BEGIN IMMEDIATE;');
  try {
    db.prepare(`INSERT INTO dataset_version
      (dataset_version_id, created_at, status, policy_version, notes)
      VALUES (?, ?, 'working', ?, ?)
      ON CONFLICT(dataset_version_id) DO NOTHING`)
      .run(datasetVersion, now, policy.policy_version, 'Bootstrap working dataset; may contain synthetic fixtures.');

    for (const example of examples) {
      const sourceId = example.evidence.source_id;
      const releaseId = `${sourceId}:fixture-release`;
      const artifactId = `${releaseId}:${example.evidence.source_artifact_sha256.slice(0, 16)}`;
      const publicationNumber = normalizePatentNumber(example.patent.publication_number);
      const country = publicationNumber.split('-')[0];
      const kind = publicationNumber.split('-').at(-1);
      const evidenceId = `${example.example_id}:evidence`;
      const quantityPrefix = `${example.example_id}:quantity`;
      const scale = deriveScale(example.quantities, policy);

      db.prepare(`INSERT INTO source_release
        (release_id, source_id, released_on, acquired_at, parser_version, schema_version, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(release_id) DO NOTHING`)
        .run(releaseId, sourceId, example.patent.publication_date ?? null, now, 'fixture-v1', example.schema_version, 'Synthetic fixture release');
      db.prepare(`INSERT INTO artifact
        (artifact_id, release_id, relative_path, sha256, size_bytes, media_type)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(artifact_id) DO NOTHING`)
        .run(artifactId, releaseId, 'examples/curated_examples.jsonl', example.evidence.source_artifact_sha256, Buffer.byteLength(example.evidence.text), 'application/x-ndjson');
      db.prepare(`INSERT INTO patent_family (family_id, family_type, source_id, confidence)
        VALUES (?, 'source_reported', ?, ?)
        ON CONFLICT(family_id) DO NOTHING`)
        .run(example.patent.family_id, sourceId, example.linkage.confidence);
      db.prepare(`INSERT INTO patent_document
        (publication_number, country_code, kind_code, publication_date, title, artifact_id, source_id, source_document_id, parser_version, raw_record_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(publication_number) DO UPDATE SET artifact_id=excluded.artifact_id`)
        .run(publicationNumber, country, kind, example.patent.publication_date ?? null,
          example.patent.title ?? null, artifactId, sourceId, publicationNumber, 'fixture-v1', JSON.stringify(example.patent));
      db.prepare(`INSERT INTO patent_family_member (family_id, publication_number, relationship)
        VALUES (?, ?, 'member') ON CONFLICT(family_id, publication_number) DO NOTHING`)
        .run(example.patent.family_id, publicationNumber);
      db.prepare(`INSERT INTO active_moiety
        (active_moiety_id, preferred_name, structure_key, structure_source, review_status)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(active_moiety_id) DO NOTHING`)
        .run(example.compound.active_moiety_id, example.compound.active_moiety_name ?? null,
          example.compound.connectivity_key ?? null, sourceId, example.review.status);
      db.prepare(`INSERT INTO compound
        (compound_id, preferred_name, smiles, inchi, inchi_key, connectivity_key, active_moiety_id, material_form, source_id, review_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(compound_id) DO NOTHING`)
        .run(example.compound.compound_id, example.compound.preferred_name ?? null,
          example.compound.smiles ?? null, example.compound.inchi ?? null,
          example.compound.inchi_key ?? null, example.compound.connectivity_key ?? null,
          example.compound.active_moiety_id, example.compound.material_form ?? null,
          sourceId, example.review.status);
      db.prepare(`INSERT INTO evidence_span
        (evidence_span_id, publication_number, source_id, artifact_sha256, section_type,
         paragraph_id, char_start, char_end, evidence_text, text_sha256, evidence_status,
         extraction_method, extractor_version, review_status, source_url, retrieved_at,
         license_code, redistribution_class)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(evidence_span_id) DO UPDATE SET review_status=excluded.review_status`)
        .run(evidenceId, publicationNumber, sourceId, example.evidence.source_artifact_sha256,
          example.evidence.section_type ?? 'example', example.evidence.paragraph_id,
          example.evidence.char_start, example.evidence.char_end, example.evidence.text,
          example.evidence.span_sha256, example.evidence.status, example.evidence.extraction_method,
          example.evidence.extractor_version ?? null, example.review.status,
          example.evidence.source_url ?? null, example.evidence.retrieved_at,
          example.evidence.license, example.evidence.redistribution_class);
      db.prepare(`INSERT INTO process_route
        (route_id, active_moiety_id, target_compound_id, route_fingerprint, review_status)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(route_id) DO NOTHING`)
        .run(example.route.route_id, example.compound.active_moiety_id,
          example.compound.compound_id, example.route.route_fingerprint ?? null, example.review.status);
      db.prepare(`INSERT INTO process_step
        (step_id, route_id, evidence_span_id, step_order, transformation_key,
         product_compound_id, operation_summary, evidence_status, review_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(step_id) DO UPDATE SET review_status=excluded.review_status`)
        .run(example.route.step_id, example.route.route_id, evidenceId,
          example.route.step_order ?? null, example.route.transformation_key ?? null,
          example.compound.compound_id, example.route.operation_summary ?? null,
          example.evidence.status, example.review.status);

      example.quantities.forEach((quantity, index) => {
        const factor = policy.mass_units_to_g[quantity.unit];
        db.prepare(`INSERT INTO quantity_observation
          (quantity_id, step_id, quantity_kind, original_value, original_unit,
           normalized_value, normalized_unit, material_compound_id, is_range, confidence)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
          ON CONFLICT(quantity_id) DO UPDATE SET original_value=excluded.original_value`)
          .run(`${quantityPrefix}:${index}`, example.route.step_id, quantity.kind,
            quantity.value, quantity.unit, factor === undefined ? null : quantity.value * factor,
            factor === undefined ? null : 'g', quantity.material_compound_id ?? null,
            quantity.is_range ? 1 : 0, quantity.confidence ?? 1);
      });

      if (example.outcome) {
        db.prepare(`INSERT INTO outcome_observation
          (outcome_id, step_id, yield_percent, purity_percent, outcome_type, original_text, confidence)
          VALUES (?, ?, ?, ?, ?, ?, ?)
          ON CONFLICT(outcome_id) DO UPDATE SET yield_percent=excluded.yield_percent`)
          .run(`${example.example_id}:outcome`, example.route.step_id,
            example.outcome.yield_percent ?? null, example.outcome.purity_percent ?? null,
            example.outcome.outcome_type ?? 'isolated', example.outcome.original_text ?? null,
            example.outcome.confidence ?? 1);
      }
      db.prepare(`INSERT INTO scale_label
        (scale_label_id, step_id, scale_band, policy_version, basis_kind, basis_value_g,
         development_stage, development_stage_basis, confidence, review_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(scale_label_id) DO UPDATE SET scale_band=excluded.scale_band`)
        .run(`${example.example_id}:scale`, example.route.step_id, example.labels.scale_band,
          policy.policy_version, scale.basis_kind, scale.basis_value_g,
          example.labels.development_stage, example.labels.development_stage_basis ?? null,
          example.labels.confidence ?? 1, example.review.status);
      db.prepare(`INSERT INTO dataset_example
        (dataset_version_id, example_id, step_id, family_id, active_moiety_id, scale_band, is_synthetic, payload_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(dataset_version_id, example_id) DO UPDATE SET payload_json=excluded.payload_json`)
        .run(datasetVersion, example.example_id, example.route.step_id, example.patent.family_id,
          example.compound.active_moiety_id, example.labels.scale_band, example.is_synthetic ? 1 : 0,
          JSON.stringify(example));
    }
    db.exec('COMMIT;');
  } catch (error) {
    db.exec('ROLLBACK;');
    throw error;
  }
  return { dataset_version: datasetVersion, ingested: examples.length };
}

export function runQualityChecks(db) {
  const scalar = (sql) => Number(db.prepare(sql).get().count);
  const checks = [
    {
      check_name: 'orphan_family_members', severity: 'critical',
      affected_count: scalar(`SELECT count(*) AS count FROM patent_family_member m
        LEFT JOIN patent_document p ON p.publication_number=m.publication_number
        WHERE p.publication_number IS NULL`)
    },
    {
      check_name: 'orphan_process_evidence', severity: 'critical',
      affected_count: scalar(`SELECT count(*) AS count FROM process_step s
        LEFT JOIN evidence_span e ON e.evidence_span_id=s.evidence_span_id
        WHERE e.evidence_span_id IS NULL`)
    },
    {
      check_name: 'negative_quantities', severity: 'critical',
      affected_count: scalar(`SELECT count(*) AS count FROM quantity_observation WHERE original_value < 0`)
    },
    {
      check_name: 'scale_labels_without_mass_basis', severity: 'critical',
      affected_count: scalar(`SELECT count(*) AS count FROM scale_label
        WHERE scale_band <> 'unknown' AND basis_value_g IS NULL`)
    },
    {
      check_name: 'stage_derived_from_scale', severity: 'critical',
      affected_count: scalar(`SELECT count(*) AS count FROM scale_label WHERE development_stage_basis='scale'`)
    },
    {
      check_name: 'synthetic_examples_in_released_dataset', severity: 'critical',
      affected_count: scalar(`SELECT count(*) AS count FROM dataset_example e
        JOIN dataset_version v ON v.dataset_version_id=e.dataset_version_id
        WHERE v.status='released' AND e.is_synthetic=1`)
    },
    {
      check_name: 'family_split_leakage', severity: 'critical',
      affected_count: scalar(`SELECT count(*) AS count FROM (
        SELECT e.dataset_version_id, e.family_id
        FROM dataset_example e JOIN dataset_split s
          ON s.dataset_version_id=e.dataset_version_id AND s.example_id=e.example_id
        WHERE e.family_id IS NOT NULL
        GROUP BY e.dataset_version_id, e.family_id HAVING count(DISTINCT s.split_name) > 1
      )`)
    },
    {
      check_name: 'active_moiety_split_leakage', severity: 'high',
      affected_count: scalar(`SELECT count(*) AS count FROM (
        SELECT e.dataset_version_id, e.active_moiety_id
        FROM dataset_example e JOIN dataset_split s
          ON s.dataset_version_id=e.dataset_version_id AND s.example_id=e.example_id
        WHERE e.active_moiety_id IS NOT NULL
        GROUP BY e.dataset_version_id, e.active_moiety_id HAVING count(DISTINCT s.split_name) > 1
      )`)
    },
    {
      check_name: 'catalogue_drugs_without_coverage', severity: 'high',
      affected_count: scalar(`SELECT count(*) AS count FROM drug_entity d
        LEFT JOIN drug_coverage c USING (drug_id) WHERE c.drug_id IS NULL`)
    },
    {
      check_name: 'exact_patent_matches_without_local_inchi_key', severity: 'critical',
      affected_count: scalar(`SELECT count(*) AS count FROM patent_candidate pc
        JOIN compound c USING (compound_id)
        WHERE pc.match_type='exact_structure' AND c.inchi_key IS NULL`)
    }
  ];
  return checks.map((check) => ({
    ...check,
    status: check.affected_count === 0 ? 'pass' : 'fail'
  }));
}
