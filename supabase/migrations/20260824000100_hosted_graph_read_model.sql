-- Read-only hosted projection. Local SQLite remains the authoritative ingest store.
create table if not exists public.rxn2_graph_node (
  node_id text primary key,
  node_type text not null,
  record_id text,
  label text not null,
  source_table text,
  review_status text not null,
  properties_json jsonb not null default '{}'::jsonb
);

create table if not exists public.rxn2_graph_edge (
  edge_id text primary key,
  source_node_id text not null references public.rxn2_graph_node(node_id),
  target_node_id text not null references public.rxn2_graph_node(node_id),
  predicate text not null,
  source_table text,
  source_record_id text,
  validation_status text not null,
  review_status text not null,
  confidence double precision,
  evidence_span_id text,
  properties_json jsonb not null default '{}'::jsonb
);

create table if not exists public.rxn2_graph_snapshot (
  snapshot_key text primary key,
  payload jsonb not null,
  updated_at timestamptz not null default now()
);

create table if not exists public.rxn2_molecule_structure (
  compound_id text primary key,
  preferred_name text,
  molecular_formula text,
  molecular_weight double precision,
  inchi_key text,
  smiles text not null,
  atoms jsonb not null,
  bonds jsonb not null
);

create index if not exists rxn2_graph_node_type_idx on public.rxn2_graph_node(node_type);
create index if not exists rxn2_graph_node_label_lower_idx on public.rxn2_graph_node(lower(label));
create index if not exists rxn2_graph_edge_source_idx on public.rxn2_graph_edge(source_node_id, validation_status);
create index if not exists rxn2_graph_edge_target_idx on public.rxn2_graph_edge(target_node_id, validation_status);
create index if not exists rxn2_graph_edge_predicate_idx on public.rxn2_graph_edge(predicate, validation_status);

-- Data is served only by the Edge Function using service-role credentials.
alter table public.rxn2_graph_node enable row level security;
alter table public.rxn2_graph_edge enable row level security;
alter table public.rxn2_graph_snapshot enable row level security;
alter table public.rxn2_molecule_structure enable row level security;

revoke all on public.rxn2_graph_node, public.rxn2_graph_edge,
  public.rxn2_graph_snapshot, public.rxn2_molecule_structure from anon, authenticated;
