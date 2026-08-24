import "jsr:@supabase/functions-js/edge-runtime.d.ts"
import { createClient } from "https://esm.sh/@supabase/supabase-js@2.49.1"

const headers = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Content-Type": "application/json",
}
const client = createClient(
  Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
)
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers })

async function snapshot(key: string) {
  const { data, error } = await client.from("rxn2_graph_snapshot").select("payload").eq("snapshot_key", key).single()
  if (error) throw error
  return data.payload
}

Deno.serve(async (request) => {
  if (request.method === "OPTIONS") return new Response("ok", { headers })
  try {
    const url = new URL(request.url)
    const operation = url.searchParams.get("op") || "stats"
    if (operation === "stats" || operation === "overview" || operation === "routes") return json(await snapshot(operation))
    if (operation === "search") {
      const query = (url.searchParams.get("query") || "").trim()
      if (!query) return json({ items: [] })
      let queryBuilder = client.from("rxn2_graph_node")
        .select("node_id,node_type,label,review_status,source_table,record_id,properties_json")
        .ilike("label", `%${query}%`).limit(30)
      const nodeType = url.searchParams.get("node_type")
      if (nodeType) queryBuilder = queryBuilder.eq("node_type", nodeType)
      const { data, error } = await queryBuilder
      if (error) throw error
      return json({ items: data })
    }
    if (operation === "structure") {
      const compoundId = url.searchParams.get("compound_id")
      if (!compoundId) return json({ detail: "compound_id is required" }, 400)
      const { data, error } = await client.from("rxn2_molecule_structure").select("*").eq("compound_id", compoundId).single()
      if (error) return json({ detail: "Molecular structure unavailable" }, 404)
      return json(data)
    }
    return json({ detail: "Unknown graph operation" }, 404)
  } catch (error) {
    return json({ detail: error instanceof Error ? error.message : "Hosted graph failed" }, 500)
  }
})
