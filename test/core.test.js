import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  deriveScale,
  deterministicSplit,
  readJson,
  readJsonl,
  validateExample,
  validateSourceRegistry,
  sha256File
} from '../src/core.js';
import {
  ingestExamples,
  initializeDatabase,
  openDatabase,
  runQualityChecks
} from '../src/database.js';

const policy = readJson('configs/scale_policy.json');
const registry = readJson('configs/sources.json');
const examples = readJsonl('examples/curated_examples.jsonl');
const sourceIds = new Set(registry.sources.map((source) => source.id));

test('source registry prevents runtime and PATENTSCOPE automation dependencies', () => {
  const result = validateSourceRegistry(registry);
  assert.deepEqual(result.errors, []);
  const wipo = registry.sources.find((source) => source.id === 'wipo_patentscope_public');
  assert.equal(wipo.automated_acquisition_allowed, false);
  assert.ok(registry.sources.every((source) => source.runtime_dependency === false));
});

test('all curated fixtures validate and scale is derived from mass', () => {
  for (const example of examples) {
    const result = validateExample(example, policy, sourceIds);
    assert.deepEqual(result.errors, [], `${example.example_id}: ${JSON.stringify(result.errors)}`);
    assert.equal(result.derived.band, example.labels.scale_band);
  }
  assert.deepEqual(
    deriveScale([{ kind: 'product_mass', value: 25, unit: 'kg' }], policy),
    { band: 'pilot', basis_kind: 'product_mass', basis_value_g: 25000 }
  );
});

test('evidence hash tampering is rejected', () => {
  const changed = structuredClone(examples[0]);
  changed.evidence.text = `${changed.evidence.text} changed`;
  const result = validateExample(changed, policy, sourceIds);
  assert.ok(result.errors.some((error) => error.code === 'span_hash_mismatch'));
});

test('file hashing is byte-stable and streaming-safe', () => {
  const directory = mkdtempSync(join(tmpdir(), 'scaleup-hash-'));
  const path = join(directory, 'artifact.bin');
  try {
    writeFileSync(path, Buffer.from([0, 1, 2, 3, 255]));
    assert.equal(sha256File(path), 'ff5d8507b6a72bee2debce2c0054798deaccdc5d8a1b945b6280ce8aa9cba52e');
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test('deterministic group splitting keeps an active moiety together', () => {
  const seed = 'test-seed';
  const byGroup = new Map();
  for (const example of examples) {
    const group = example.compound.active_moiety_id;
    const split = deterministicSplit(group, seed);
    if (byGroup.has(group)) assert.equal(byGroup.get(group), split);
    byGroup.set(group, split);
  }
  assert.equal(byGroup.size, 3);
});

test('SQLite evidence store ingests valid examples and passes core checks', () => {
  const directory = mkdtempSync(join(tmpdir(), 'scaleup-data-'));
  const dbPath = join(directory, 'test.sqlite');
  const db = openDatabase(dbPath);
  try {
    initializeDatabase(db);
    const result = ingestExamples(db, examples, policy, registry, 'test-fixtures-v1');
    assert.equal(result.ingested, 4);
    assert.equal(Number(db.prepare('SELECT count(*) AS count FROM dataset_example').get().count), 4);
    assert.equal(Number(db.prepare('SELECT count(*) AS count FROM process_step').get().count), 4);
    assert.equal(Number(db.prepare('SELECT count(*) AS count FROM active_moiety').get().count), 3);
    const checks = runQualityChecks(db);
    assert.ok(checks.every((check) => check.status === 'pass'), JSON.stringify(checks));
  } finally {
    db.close();
    rmSync(directory, { recursive: true, force: true });
  }
});
