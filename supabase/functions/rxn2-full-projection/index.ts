import "jsr:@supabase/functions-js/edge-runtime.d.ts"
import { createClient } from "https://esm.sh/@supabase/supabase-js@2.49.1"

const headers = {
  "Access-Control-Allow-Origin": "*",
  "Content-Type": "application/json",
}
const client = createClient(
  Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
)
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers })
const VALID_STATUSES = ["validated", "unresolved", "rejected"]

Deno.serve(async (request) => {
  if (request.method === "OPTIONS") return new Response("ok", { headers })
  if (request.method !== "GET") return json({ detail: "Method not allowed" }, 405)
  try {
    const url = new URL(request.url)
    const kind = url.searchParams.get("kind")
    if (kind !== "nodes" && kind !== "edges") return json({ detail: "kind must be nodes or edges" }, 400)
    const offset = Math.max(0, Number(url.searchParams.get("offset") || "0"))
    // The hosted PostgREST read limit is 1,000 rows. Return that actual page
    // size so callers can advance without leaving gaps in the full projection.
    const limit = Math.min(1000, Math.max(1, Number(url.searchParams.get("limit") || "1000")))
    if (!Number.isSafeInteger(offset) || !Number.isSafeInteger(limit)) return json({ detail: "Invalid page" }, 400)

    const table = kind === "nodes" ? "rxn2_graph_node" : "rxn2_graph_edge"
    const order = kind === "nodes" ? "node_id" : "edge_id"
    const columns = kind === "nodes"
      ? "node_id,node_type,label,review_status,source_table,record_id"
      : "edge_id,source_node_id,target_node_id,predicate,validation_status,review_status,confidence,evidence_span_id,source_table,source_record_id"
    let query = client.from(table).select(columns, { count: "exact" }).order(order).range(offset, offset + limit - 1)
    if (kind === "edges") {
      const statuses = (url.searchParams.get("validation_statuses") || VALID_STATUSES.join(","))
        .split(",").filter((status) => VALID_STATUSES.includes(status))
      query = query.in("validation_status", statuses.length ? statuses : VALID_STATUSES)
    }
    const { data, error, count } = await query
    if (error) throw error
    return json({ kind, offset, limit, total: count || 0, items: data || [], automatic_acceptance: false })
  } catch (error) {
    return json({ detail: error instanceof Error ? error.message : "Full graph query failed" }, 500)
  }
})
