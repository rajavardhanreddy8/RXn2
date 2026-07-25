#!/usr/bin/env node
import { readdirSync, statSync, mkdirSync, writeFileSync } from 'node:fs';
import { dirname, relative, resolve, sep } from 'node:path';
import {
  deterministicSplit,
  getByPath,
  readJson,
  readJsonl,
  sha256,
  sha256File,
  validateExample,
  validateSourceRegistry
} from './core.js';
import {
  ingestExamples,
  initializeDatabase,
  openDatabase,
  registerSources,
  runQualityChecks
} from './database.js';

function parseOptions(tokens) {
  const options = {};
  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index];
    if (!token.startsWith('--')) throw new Error(`Unexpected argument: ${token}`);
    const key = token.slice(2).replaceAll('-', '_');
    const next = tokens[index + 1];
    if (!next || next.startsWith('--')) {
      options[key] = true;
    } else {
      options[key] = next;
      index += 1;
    }
  }
  return options;
}

function loadContext(options) {
  const policyPath = options.policy ?? 'configs/scale_policy.json';
  const registryPath = options.sources ?? 'configs/sources.json';
  return {
    policyPath,
    registryPath,
    policy: readJson(resolve(policyPath)),
    registry: readJson(resolve(registryPath))
  };
}

function validateAll(examples, policy, registry) {
  const registryResult = validateSourceRegistry(registry);
  const sourceIds = new Set(registry.sources?.map((source) => source.id));
  const results = examples.map((example, index) => ({
    index,
    example_id: example.example_id ?? null,
    ...validateExample(example, policy, sourceIds)
  }));
  const duplicateIds = [...new Set(
    examples.map((example) => example.example_id).filter((id, index, values) => id && values.indexOf(id) !== index)
  )];
  return {
    registry: registryResult,
    examples: results,
    duplicate_example_ids: duplicateIds,
    error_count: registryResult.errors.length + duplicateIds.length + results.reduce((sum, result) => sum + result.errors.length, 0),
    warning_count: registryResult.warnings.length + results.reduce((sum, result) => sum + result.warnings.length, 0)
  };
}

function printJson(value) {
  process.stdout.write(`${JSON.stringify(value, null, 2)}\n`);
}

function commandValidate(options) {
  if (!options.input) throw new Error('--input is required');
  const { policy, registry, policyPath, registryPath } = loadContext(options);
  const examples = readJsonl(resolve(options.input));
  const result = validateAll(examples, policy, registry);
  printJson({
    input: resolve(options.input),
    policy: resolve(policyPath),
    sources: resolve(registryPath),
    rows: examples.length,
    ...result
  });
  if (result.error_count > 0) process.exitCode = 1;
}

function commandInitDb(options) {
  if (!options.db) throw new Error('--db is required');
  const { registry } = loadContext(options);
  const registryResult = validateSourceRegistry(registry);
  if (registryResult.errors.length) throw new Error(`source registry has ${registryResult.errors.length} errors`);
  const db = openDatabase(options.db);
  try {
    initializeDatabase(db, options.schema ?? 'sql/schema.sql');
  } finally {
    db.close();
  }
  printJson({ database: resolve(options.db), initialized: true });
}

function commandIngest(options) {
  if (!options.db || !options.input) throw new Error('--db and --input are required');
  const { policy, registry } = loadContext(options);
  const examples = readJsonl(resolve(options.input));
  const validation = validateAll(examples, policy, registry);
  if (validation.error_count) {
    printJson(validation);
    throw new Error(`refusing to ingest ${validation.error_count} validation errors`);
  }
  const db = openDatabase(options.db);
  let result;
  try {
    initializeDatabase(db, options.schema ?? 'sql/schema.sql');
    result = ingestExamples(db, examples, policy, registry, options.dataset_version ?? 'working-fixtures-v1');
  } finally {
    db.close();
  }
  printJson({ database: resolve(options.db), ...result, validation_warnings: validation.warning_count });
}

function commandSplit(options) {
  if (!options.input || !options.output) throw new Error('--input and --output are required');
  const { policy, registry } = loadContext(options);
  const examples = readJsonl(resolve(options.input));
  const validation = validateAll(examples, policy, registry);
  if (validation.error_count) throw new Error(`refusing to split ${validation.error_count} validation errors`);
  const groupField = options.group_field ?? 'compound.active_moiety_id';
  const seed = options.seed ?? 'scaleup-data-v1';
  const groupAssignments = new Map();
  const assignments = examples.map((example) => {
    const group = getByPath(example, groupField);
    if (group === undefined || group === null || group === '') {
      throw new Error(`missing split group ${groupField} for ${example.example_id}`);
    }
    if (!groupAssignments.has(group)) groupAssignments.set(group, deterministicSplit(String(group), seed));
    return {
      example_id: example.example_id,
      split: groupAssignments.get(group),
      leakage_group: String(group)
    };
  });
  const counts = assignments.reduce((acc, assignment) => {
    acc[assignment.split] = (acc[assignment.split] ?? 0) + 1;
    return acc;
  }, {});
  const payload = {
    created_at: new Date().toISOString(),
    input_sha256: sha256(examples.map((example) => JSON.stringify(example)).join('\n')),
    group_field: groupField,
    seed,
    policy_version: policy.policy_version,
    counts,
    assignments
  };
  const output = resolve(options.output);
  mkdirSync(dirname(output), { recursive: true });
  writeFileSync(output, `${JSON.stringify(payload, null, 2)}\n`, 'utf8');
  let database = null;
  if (options.db) {
    const datasetVersion = options.dataset_version ?? 'working-fixtures-v1';
    const db = openDatabase(options.db);
    try {
      initializeDatabase(db, options.schema ?? 'sql/schema.sql');
      db.exec('BEGIN IMMEDIATE;');
      try {
        const insert = db.prepare(`INSERT INTO dataset_split
          (dataset_version_id, example_id, split_name, split_policy, leakage_group)
          VALUES (?, ?, ?, ?, ?)
          ON CONFLICT(dataset_version_id, example_id) DO UPDATE SET
            split_name=excluded.split_name, split_policy=excluded.split_policy,
            leakage_group=excluded.leakage_group`);
        for (const assignment of assignments) {
          insert.run(datasetVersion, assignment.example_id, assignment.split,
            JSON.stringify({ group_field: groupField, seed }), assignment.leakage_group);
        }
        db.exec('COMMIT;');
      } catch (error) {
        db.exec('ROLLBACK;');
        throw error;
      }
    } finally {
      db.close();
    }
    database = resolve(options.db);
  }
  printJson({ output, database, groups: groupAssignments.size, counts });
}

function mediaTypeFor(path) {
  const lower = path.toLowerCase();
  if (lower.endsWith('.parquet')) return 'application/vnd.apache.parquet';
  if (lower.endsWith('.json') || lower.endsWith('.jsonl')) return 'application/json';
  if (lower.endsWith('.xml')) return 'application/xml';
  if (lower.endsWith('.zip')) return 'application/zip';
  if (lower.endsWith('.csv')) return 'text/csv';
  if (lower.endsWith('.txt')) return 'text/plain';
  return 'application/octet-stream';
}

function commandRegisterArtifact(options) {
  for (const key of ['db', 'source', 'release', 'file']) {
    if (!options[key]) throw new Error(`--${key.replaceAll('_', '-')} is required`);
  }
  const { registry } = loadContext(options);
  const registryResult = validateSourceRegistry(registry);
  if (registryResult.errors.length) throw new Error(`source registry has ${registryResult.errors.length} errors`);
  const source = registry.sources.find((entry) => entry.id === options.source);
  if (!source) throw new Error(`unknown source: ${options.source}`);
  if (source.collection_mode === 'manual_lookup_only') {
    throw new Error(`${source.id} does not permit bulk artifact registration; use a licensed data-product source entry`);
  }
  const file = resolve(options.file);
  const workspace = resolve('.');
  const relativePath = relative(workspace, file);
  if (relativePath.startsWith('..') || relativePath === '') {
    throw new Error('artifact must be placed inside the workspace, normally under data/raw');
  }
  const stats = statSync(file);
  if (!stats.isFile()) throw new Error('--file must point to a regular file');
  const checksum = sha256File(file);
  const releaseId = `${source.id}:${options.release}`;
  const artifactId = `${releaseId}:${checksum.slice(0, 16)}`;
  const db = openDatabase(options.db);
  try {
    initializeDatabase(db, options.schema ?? 'sql/schema.sql');
    registerSources(db, registry);
    db.prepare(`INSERT INTO source_release
      (release_id, source_id, released_on, acquired_at, parser_version, schema_version, notes)
      VALUES (?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(release_id) DO UPDATE SET
        acquired_at=excluded.acquired_at, parser_version=excluded.parser_version,
        schema_version=excluded.schema_version, notes=excluded.notes`)
      .run(releaseId, source.id, options.released_on ?? null, new Date().toISOString(),
        options.parser_version ?? null, options.source_schema_version ?? null, options.notes ?? null);
    db.prepare(`INSERT INTO artifact
      (artifact_id, release_id, relative_path, sha256, size_bytes, media_type)
      VALUES (?, ?, ?, ?, ?, ?)
      ON CONFLICT(artifact_id) DO UPDATE SET relative_path=excluded.relative_path`)
      .run(artifactId, releaseId, relativePath.split(sep).join('/'), checksum, stats.size, mediaTypeFor(file));
  } finally {
    db.close();
  }
  printJson({
    database: resolve(options.db),
    source_id: source.id,
    release_id: releaseId,
    artifact_id: artifactId,
    relative_path: relativePath.split(sep).join('/'),
    size_bytes: stats.size,
    sha256: checksum
  });
}

function walkFiles(root, current = root) {
  const files = [];
  for (const entry of readdirSync(current, { withFileTypes: true })) {
    const path = resolve(current, entry.name);
    if (entry.isDirectory()) files.push(...walkFiles(root, path));
    else if (entry.isFile()) files.push(path);
  }
  return files;
}

function commandManifest(options) {
  if (!options.root || !options.output) throw new Error('--root and --output are required');
  const root = resolve(options.root);
  const output = resolve(options.output);
  const entries = walkFiles(root)
    .filter((path) => path !== output)
    .map((path) => ({
      path: relative(root, path).split(sep).join('/'),
      size_bytes: statSync(path).size,
      sha256: sha256File(path)
    }))
    .sort((a, b) => a.path.localeCompare(b.path));
  const payload = {
    manifest_version: '1.0.0',
    created_at: new Date().toISOString(),
    root,
    entries
  };
  const canonical = JSON.stringify(payload);
  payload.manifest_sha256 = sha256(canonical);
  mkdirSync(dirname(output), { recursive: true });
  writeFileSync(output, `${JSON.stringify(payload, null, 2)}\n`, 'utf8');
  printJson({ output, files: entries.length, manifest_sha256: payload.manifest_sha256 });
}

function commandQuality(options) {
  if (!options.db) throw new Error('--db is required');
  const db = openDatabase(options.db);
  let checks;
  try {
    initializeDatabase(db, options.schema ?? 'sql/schema.sql');
    checks = runQualityChecks(db);
  } finally {
    db.close();
  }
  const failed = checks.filter((check) => check.status === 'fail');
  printJson({
    database: resolve(options.db),
    status: failed.length ? 'fail' : 'pass',
    failed: failed.length,
    checks
  });
  if (failed.some((check) => check.severity === 'critical')) process.exitCode = 1;
}

function usage() {
  process.stdout.write(`Usage:
  node src/cli.js validate --input FILE [--policy FILE] [--sources FILE]
  node src/cli.js init-db --db FILE [--schema FILE]
  node src/cli.js ingest --db FILE --input FILE [--dataset-version ID]
  node src/cli.js register-artifact --db FILE --source ID --release ID --file FILE
  node src/cli.js split --input FILE --output FILE [--group-field PATH] [--seed VALUE] [--db FILE --dataset-version ID]
  node src/cli.js manifest --root DIR --output FILE
  node src/cli.js quality --db FILE
`);
}

const [command, ...tokens] = process.argv.slice(2);
try {
  if (!command || ['help', '--help', '-h'].includes(command)) {
    usage();
  } else {
    const options = parseOptions(tokens);
    const commands = {
      validate: commandValidate,
      'init-db': commandInitDb,
      ingest: commandIngest,
      'register-artifact': commandRegisterArtifact,
      split: commandSplit,
      manifest: commandManifest,
      quality: commandQuality
    };
    if (!commands[command]) throw new Error(`Unknown command: ${command}`);
    commands[command](options);
  }
} catch (error) {
  process.stderr.write(`ERROR: ${error.message}\n`);
  process.exitCode = 1;
}
