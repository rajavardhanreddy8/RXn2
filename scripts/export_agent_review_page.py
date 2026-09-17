"""Build an offline, evidence-linked review page. No database writes."""
import hashlib
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / 'data/processed/review/process-patent-groq-qwen-full-20260831'
SOURCE = ROOT / 'data/processed/epo_ops/process-patent-examples-with-family-v2-20260831/example_blocks.jsonl'

def read(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]

def main():
    sources = {r['example_id']: r for r in read(SOURCE)}
    selected = []
    seen = set()
    for path in sorted(RUN.glob('worker-*/results/agent_review_proposals.jsonl')):
        for row in read(path):
            if row['consensus']['status'] != 'agent_disagreement':
                continue
            assert row['example_id'] not in seen, 'Duplicate example'
            seen.add(row['example_id'])
            source = sources[row['example_id']]
            assert source['text_sha256'] == row['text_sha256'], 'Source hash mismatch'
            assert hashlib.sha256(source['text'].encode()).hexdigest() == row['text_sha256']
            assert not any(row['local_validation_failures'].values()), 'Invalid review'
            selected.append({'source': source, 'proposal': row})
    selected.sort(key=lambda r: (len(r['source']['text']), r['source']['example_id']))
    assert len(selected) == 55, f'Expected 55 disagreements; found {len(selected)}'
    payload = json.dumps(selected, ensure_ascii=False).replace('<', '\\u003c')
    page = TEMPLATE.replace('__DATA__', payload)
    output = RUN / 'human-review.html'
    output.write_text(page, encoding='utf-8')
    (RUN / 'human-review-queue.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in selected), encoding='utf-8')
    print(json.dumps({'records':len(selected), 'page':str(output), 'source_hashes_verified':True}))

TEMPLATE = r'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RXN2 · Patent review</title><style>
body{font:16px/1.5 system-ui;margin:0;background:#f4f6f8;color:#182530}header,main{max-width:1400px;margin:auto;padding:24px}h1{margin:0}button,select,textarea,input{font:inherit;padding:8px;border:1px solid #9cabb7;border-radius:6px}button{cursor:pointer;background:#fff}nav{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:16px 0}.grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}section{background:white;padding:20px;border-radius:10px;margin-bottom:16px}pre{white-space:pre-wrap;font:inherit;overflow-wrap:anywhere}.item{border-top:1px solid #ddd;padding:10px 0}blockquote{margin:5px 0;padding:8px;background:#edf4f8}textarea{box-sizing:border-box;width:100%;min-height:100px}.muted{color:#536675}.source{max-height:75vh;overflow:auto}label{display:block;margin:12px 0}#notice{color:#8a4300}@media(max-width:850px){.grid{grid-template-columns:1fr}}
</style><header><h1>RXN2 patent review</h1><p>55 examples where the agents differ. Start with the shortest example. Read the patent text, then compare each claim and its quotation.</p><p>Choose “Text checked” only for source matching. Chemical identities, missing inputs and process completeness still need expert review. Saving here does not approve a gold record.</p><nav><button id="prev">← Previous</button><select id="jump" aria-label="Choose example"></select><button id="next">Next →</button><button id="export">Download my decisions</button></nav><p id="progress"></p><p id="notice">Download your decisions before closing; browser storage is only a convenience copy.</p></header><main><h2 id="title"></h2><p id="diff"></p><div class="grid"><section><h3>Patent example</h3><p id="citation" class="muted"></p><pre id="source" class="source"></pre></section><section><h3>Your review notes</h3><p>Check starting materials, reagents and solvents, amounts, conditions, product, yield, and references to earlier examples. If something is unclear, write what you need to check.</p><label>Reviewer name <input id="reviewer" placeholder="Your name"></label><label>Decision <select id="decision"><option value="unreviewed">Not reviewed</option><option value="text_checked">Text checked — expert approval pending</option><option value="needs_expert">Needs expert</option><option value="needs_source">Needs more patent context</option><option value="reject_proposal">Reject these proposals</option></select></label><label>Corrections, missing information and questions<textarea id="notes" placeholder="Example: Agent A omitted a solvent mentioned in paragraph …"></textarea></label><p id="saved" class="muted"></p></section></div><div class="grid" id="agents"></div><details><summary>Full provenance and original agent output</summary><pre id="raw"></pre></details></main>
<script id="data" type="application/json">__DATA__</script><script>
const records=JSON.parse(document.getElementById('data').textContent), $=id=>document.getElementById(id);
const key='rxn2-review-c70b453a-v1';let decisions={},i=0;
try{decisions=JSON.parse(localStorage.getItem(key)||'{}')}catch(e){$('notice').textContent='Browser storage unavailable. Download decisions before closing.'}
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
records.forEach((r,n)=>{const o=document.createElement('option');o.value=n;o.textContent=`${n+1}. ${r.source.publication_number} · ${r.source.heading}`;$('jump').append(o)});
function show(){const r=records[i],s=r.source,p=r.proposal,d=decisions[s.example_id]||{};
$('jump').value=i;$('title').textContent=`${i+1} / ${records.length} · ${s.publication_number} · ${s.heading}`;
$('diff').textContent='Differences flagged: '+p.consensus.disagreements.join(', ')+'. Differences may include wording or grouping, as well as real omissions.';
$('citation').textContent=`Paragraphs ${(s.paragraph_labels||[]).join(', ')} · Source: ${s.source_artifact}`;$('source').textContent=s.text;
$('decision').value=d.decision||'unreviewed';$('notes').value=d.notes||'';$('reviewer').value=d.reviewer||'';$('saved').textContent=d.updated_at?'Saved '+d.updated_at:'';
$('agents').innerHTML=Object.entries(p.reviews).map(([role,a])=>`<section><h3>${esc(role==='source_completeness'?'Agent A · Source completeness':'Agent B · Chemistry challenge')}</h3><p>${esc(a.rationale)}</p><h4>Materials and roles</h4>${(a.materials||[]).map(m=>`<div class="item"><strong>${esc(m.surface_text)}</strong> · ${esc(m.role)}${m.uncertain?' · uncertain':''}<blockquote>${esc(m.evidence_quote)}</blockquote></div>`).join('')}<h4>Conditions, amounts and other facts</h4>${(a.facts||[]).map(f=>`<div class="item"><strong>${esc(f.field)}</strong>: ${esc(f.value_text)}${f.uncertain?' · uncertain':''}<blockquote>${esc(f.evidence_quote)}</blockquote></div>`).join('')}<h4>Missing information and issues</h4><pre>${esc(JSON.stringify({missing:a.missing_fields,issues:a.issues},null,2))}</pre></section>`).join('');
$('raw').textContent=JSON.stringify(r,null,2);$('prev').disabled=i===0;$('next').disabled=i===records.length-1;progress()}
function progress(){$('progress').textContent=Object.values(decisions).filter(d=>d.decision!=='unreviewed').length+' of 55 annotated. Gold approvals: 0.'}
function save(){const s=records[i].source;decisions[s.example_id]={example_id:s.example_id,text_sha256:s.text_sha256,decision:$('decision').value,notes:$('notes').value,reviewer:$('reviewer').value,updated_at:new Date().toISOString(),gold_accepted:false};try{localStorage.setItem(key,JSON.stringify(decisions));$('saved').textContent='Saved in this browser. Download a copy when finished.'}catch(e){$('saved').textContent='Held in memory only — download before closing.'}progress()}
['notes','decision','reviewer'].forEach(id=>$(id).addEventListener('input',save));$('prev').onclick=()=>{i--;show()};$('next').onclick=()=>{i++;show()};$('jump').onchange=()=>{i=Number($('jump').value);show()};
$('export').onclick=()=>{const blob=new Blob([JSON.stringify({schema_version:'rxn2-source-review-notes-v1',gold_accepted:false,decisions:Object.values(decisions)},null,2)],{type:'application/json'});const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='rxn2-review-decisions.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)};show();
</script></html>'''

if __name__ == '__main__':
    main()
