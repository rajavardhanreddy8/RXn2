import { createHash } from 'node:crypto';
import { closeSync, openSync, readFileSync, readSync } from 'node:fs';

export const SCALE_BANDS = new Set([
  'sub_gram',
  'laboratory',
  'kilo_lab',
  'pilot',
  'manufacturing',
  'unknown'
]);

export const DEVELOPMENT_STAGES = new Set([
  'discovery',
  'preclinical',
  'phase_1',
  'phase_2',
  'phase_3',
  'approved',
  'post_approval',
  'unknown'
]);

export const EVIDENCE_STATUSES = new Set([
  'performed',
  'historical',
  'prophetic',
  'generic',
  'ambiguous'
]);

export function sha256(value) {
  return createHash('sha256').update(value).digest('hex');
}

export function sha256File(path) {
  const hash = createHash('sha256');
  const descriptor = openSync(path, 'r');
  const buffer = Buffer.allocUnsafe(4 * 1024 * 1024);
  try {
    let bytesRead;
    do {
      bytesRead = readSync(descriptor, buffer, 0, buffer.length, null);
      if (bytesRead > 0) hash.update(buffer.subarray(0, bytesRead));
    } while (bytesRead > 0);
  } finally {
    closeSync(descriptor);
  }
  return hash.digest('hex');
}

export function readJson(path) {
  return JSON.parse(readFileSync(path, 'utf8'));
}

export function readJsonl(path) {
  const text = readFileSync(path, 'utf8');
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line, index) => {
      try {
        return JSON.parse(line);
      } catch (error) {
        throw new Error(`Invalid JSONL at ${path}:${index + 1}: ${error.message}`);
      }
    });
}

export function normalizePatentNumber(value) {
  if (typeof value !== 'string' || !value.trim()) return null;
  const upper = value.trim().toUpperCase().replace(/[\s/]/g, '-').replace(/-+/g, '-');
  const dashed = upper.match(/^([A-Z]{2})-([A-Z0-9]+)-([A-Z]\d?)$/);
  if (dashed) return `${dashed[1]}-${dashed[2]}-${dashed[3]}`;
  const compact = upper.replace(/-/g, '').match(/^([A-Z]{2})([A-Z0-9]+?)([A-Z]\d)$/);
  if (compact) return `${compact[1]}-${compact[2]}-${compact[3]}`;
  return null;
}

export function deriveScale(quantities, policy) {
  const candidates = [];
  for (const quantity of quantities ?? []) {
    const factor = policy.mass_units_to_g[quantity.unit];
    if (factor === undefined || typeof quantity.value !== 'number' || quantity.value < 0) continue;
    candidates.push({
      kind: quantity.kind,
      value_g: quantity.value * factor,
      original_value: quantity.value,
      original_unit: quantity.unit
    });
  }

  let basis = null;
  for (const preferredKind of policy.basis_priority) {
    const matches = candidates.filter((candidate) => candidate.kind === preferredKind);
    if (matches.length) {
      basis = matches.reduce((largest, candidate) =>
        candidate.value_g > largest.value_g ? candidate : largest
      );
      break;
    }
  }

  if (!basis) {
    return { band: policy.unknown_label, basis_kind: null, basis_value_g: null };
  }

  const band = policy.bands.find((entry) =>
    basis.value_g >= entry.min_g_inclusive &&
    (entry.max_g_exclusive === null || basis.value_g < entry.max_g_exclusive)
  );
  return {
    band: band?.label ?? policy.unknown_label,
    basis_kind: basis.kind,
    basis_value_g: basis.value_g
  };
}

function issue(code, path, message) {
  return { code, path, message };
}

function isSha256(value) {
  return typeof value === 'string' && /^[a-f0-9]{64}$/i.test(value);
}

function isIsoDate(value) {
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}(T.*Z)?$/.test(value) && !Number.isNaN(Date.parse(value));
}

export function validateSourceRegistry(registry) {
  const errors = [];
  const warnings = [];
  if (!registry || !Array.isArray(registry.sources)) {
    return { errors: [issue('registry_shape', 'sources', 'sources must be an array')], warnings };
  }
  const ids = new Set();
  for (const [index, source] of registry.sources.entries()) {
    const path = `sources[${index}]`;
    for (const key of ['id', 'name', 'authority', 'role', 'collection_mode', 'license']) {
      if (typeof source[key] !== 'string' || !source[key]) {
        errors.push(issue('required', `${path}.${key}`, `${key} is required`));
      }
    }
    if (ids.has(source.id)) errors.push(issue('duplicate_source', `${path}.id`, source.id));
    ids.add(source.id);
    if (source.runtime_dependency !== false) {
      errors.push(issue('runtime_dependency', `${path}.runtime_dependency`, 'all registered external sources must be acquisition-only'));
    }
    if (source.id === 'wipo_patentscope_public' && source.automated_acquisition_allowed !== false) {
      errors.push(issue('wipo_terms', path, 'PATENTSCOPE public-site automation must remain disabled'));
    }
    if (source.redistribution === 'restricted' && source.automated_acquisition_allowed) {
      warnings.push(issue('restricted_acquisition', path, 'restricted source is marked for automated acquisition'));
    }
  }
  return { errors, warnings };
}

export function validateExample(example, policy, sourceIds = null) {
  const errors = [];
  const warnings = [];
  const requiredString = (path, value) => {
    if (typeof value !== 'string' || !value.trim()) errors.push(issue('required', path, `${path} is required`));
  };

  requiredString('schema_version', example.schema_version);
  requiredString('example_id', example.example_id);
  if (typeof example.is_synthetic !== 'boolean') {
    errors.push(issue('required_boolean', 'is_synthetic', 'is_synthetic must be boolean'));
  }

  const publication = normalizePatentNumber(example.patent?.publication_number);
  if (!publication) errors.push(issue('patent_number', 'patent.publication_number', 'use CC-NUMBER-KIND format'));
  if (example.patent?.publication_date && !isIsoDate(example.patent.publication_date)) {
    errors.push(issue('date', 'patent.publication_date', 'publication_date must be ISO-8601'));
  }
  requiredString('patent.family_id', example.patent?.family_id);

  requiredString('compound.compound_id', example.compound?.compound_id);
  requiredString('compound.active_moiety_id', example.compound?.active_moiety_id);
  if (example.compound?.inchi_key && !/^[A-Z]{14}-[A-Z]{10}-[A-Z]$/.test(example.compound.inchi_key)) {
    errors.push(issue('inchi_key', 'compound.inchi_key', 'invalid standard InChIKey'));
  }
  if (example.compound?.connectivity_key && !/^[A-Z]{14}$/.test(example.compound.connectivity_key)) {
    errors.push(issue('connectivity_key', 'compound.connectivity_key', 'connectivity key must be 14 uppercase characters'));
  }

  requiredString('route.route_id', example.route?.route_id);
  requiredString('route.step_id', example.route?.step_id);
  requiredString('evidence.source_id', example.evidence?.source_id);
  if (sourceIds && !sourceIds.has(example.evidence?.source_id)) {
    errors.push(issue('unknown_source', 'evidence.source_id', `${example.evidence?.source_id} is not registered`));
  }
  if (!isSha256(example.evidence?.source_artifact_sha256)) {
    errors.push(issue('sha256', 'evidence.source_artifact_sha256', 'artifact SHA-256 is required'));
  }
  requiredString('evidence.paragraph_id', example.evidence?.paragraph_id);
  requiredString('evidence.text', example.evidence?.text);
  if (!isSha256(example.evidence?.span_sha256)) {
    errors.push(issue('sha256', 'evidence.span_sha256', 'span SHA-256 is required'));
  } else if (typeof example.evidence?.text === 'string' && sha256(example.evidence.text) !== example.evidence.span_sha256) {
    errors.push(issue('span_hash_mismatch', 'evidence.span_sha256', 'span hash does not match evidence text'));
  }
  if (!Number.isInteger(example.evidence?.char_start) || example.evidence.char_start < 0) {
    errors.push(issue('offset', 'evidence.char_start', 'char_start must be a non-negative integer'));
  }
  if (!Number.isInteger(example.evidence?.char_end) || example.evidence.char_end < example.evidence?.char_start) {
    errors.push(issue('offset', 'evidence.char_end', 'char_end must be >= char_start'));
  }
  if (!EVIDENCE_STATUSES.has(example.evidence?.status)) {
    errors.push(issue('evidence_status', 'evidence.status', 'invalid evidence status'));
  }
  if (!isIsoDate(example.evidence?.retrieved_at)) {
    errors.push(issue('date', 'evidence.retrieved_at', 'retrieved_at must be ISO-8601'));
  }
  requiredString('evidence.extraction_method', example.evidence?.extraction_method);
  requiredString('evidence.license', example.evidence?.license);
  requiredString('evidence.redistribution_class', example.evidence?.redistribution_class);

  if (!Array.isArray(example.quantities)) {
    errors.push(issue('quantities', 'quantities', 'quantities must be an array'));
  } else {
    for (const [index, quantity] of example.quantities.entries()) {
      const path = `quantities[${index}]`;
      requiredString(`${path}.kind`, quantity.kind);
      requiredString(`${path}.unit`, quantity.unit);
      if (typeof quantity.value !== 'number' || quantity.value < 0) {
        errors.push(issue('quantity_value', `${path}.value`, 'quantity must be a non-negative number'));
      }
      if (quantity.confidence !== undefined && (quantity.confidence < 0 || quantity.confidence > 1)) {
        errors.push(issue('confidence', `${path}.confidence`, 'confidence must be between 0 and 1'));
      }
    }
  }

  const derived = deriveScale(example.quantities, policy);
  if (!SCALE_BANDS.has(example.labels?.scale_band)) {
    errors.push(issue('scale_band', 'labels.scale_band', 'invalid scale band'));
  } else if (example.labels.scale_band !== derived.band) {
    errors.push(issue('scale_mismatch', 'labels.scale_band', `expected ${derived.band} from ${derived.basis_value_g ?? 'no'} g basis`));
  }
  if (!DEVELOPMENT_STAGES.has(example.labels?.development_stage)) {
    errors.push(issue('development_stage', 'labels.development_stage', 'invalid development stage'));
  }
  if (example.labels?.development_stage !== 'unknown' && !example.labels?.development_stage_basis) {
    errors.push(issue('stage_basis', 'labels.development_stage_basis', 'non-unknown stage requires independent dated evidence'));
  }
  if (example.labels?.development_stage_basis === 'scale') {
    errors.push(issue('stage_from_scale', 'labels.development_stage_basis', 'clinical stage cannot be inferred from process scale'));
  }

  if (example.outcome?.yield_percent !== undefined && (example.outcome.yield_percent < 0 || example.outcome.yield_percent > 105)) {
    errors.push(issue('yield', 'outcome.yield_percent', 'yield must be in [0, 105]'));
  }
  if (example.outcome?.purity_percent !== undefined && (example.outcome.purity_percent < 0 || example.outcome.purity_percent > 100)) {
    errors.push(issue('purity', 'outcome.purity_percent', 'purity must be in [0, 100]'));
  }

  const linkTypes = new Set(['exact_structure', 'same_connectivity', 'active_moiety', 'regulatory_patent', 'family_only', 'name_only', 'analogue']);
  if (!linkTypes.has(example.linkage?.match_type)) {
    errors.push(issue('link_type', 'linkage.match_type', 'invalid link type'));
  }
  if (typeof example.linkage?.confidence !== 'number' || example.linkage.confidence < 0 || example.linkage.confidence > 1) {
    errors.push(issue('confidence', 'linkage.confidence', 'confidence must be between 0 and 1'));
  }
  if (['name_only', 'family_only', 'analogue'].includes(example.linkage?.match_type) && example.review?.status === 'accepted') {
    warnings.push(issue('weak_identity', 'linkage.match_type', 'weak identity relationship is accepted; it must not become a same-drug label'));
  }

  const reviewStatuses = new Set(['accepted', 'rejected', 'needs_review']);
  if (!reviewStatuses.has(example.review?.status)) {
    errors.push(issue('review_status', 'review.status', 'invalid review status'));
  }
  requiredString('review.reviewer_id', example.review?.reviewer_id);
  if (!isIsoDate(example.review?.reviewed_at)) {
    errors.push(issue('date', 'review.reviewed_at', 'reviewed_at must be ISO-8601'));
  }
  if (!example.is_synthetic && example.review?.status === 'accepted' && !['performed', 'historical'].includes(example.evidence?.status)) {
    errors.push(issue('training_evidence_status', 'evidence.status', 'accepted real training evidence must be performed or historical'));
  }

  return { errors, warnings, derived, normalized_publication_number: publication };
}

export function getByPath(object, dottedPath) {
  return dottedPath.split('.').reduce((value, key) => value?.[key], object);
}

export function deterministicSplit(group, seed, ratios = { train: 0.8, validation: 0.1, test: 0.1 }) {
  const total = ratios.train + ratios.validation + ratios.test;
  if (Math.abs(total - 1) > 1e-9) throw new Error('split ratios must sum to 1');
  const value = Number.parseInt(sha256(`${seed}|${group}`).slice(0, 13), 16) / 0x10000000000000;
  if (value < ratios.train) return 'train';
  if (value < ratios.train + ratios.validation) return 'validation';
  return 'test';
}
