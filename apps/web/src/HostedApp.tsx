import LargeGraphExplorer from './LargeGraphExplorer'

export default function HostedApp() {
  return <div className="app-shell">
    <header>
      <a className="brand" href="#top" aria-label="RXN2 Graph home"><span className="brand-mark">R</span><span><b>RXN2</b><small>PUBLIC EVIDENCE GRAPH</small></span></a>
      <nav><a className="active" href="#large-graph">Knowledge graph</a><a href="#principles">Methods</a></nav>
      <span className="local-status"><i /> Hosted read-only graph</span>
    </header>
    <main id="top">
      <section className="hero hosted-hero">
        <div><span className="kicker">Evidence-bounded process intelligence</span><h1>Explore the public<br /><em>drug–patent graph.</em></h1><p>Search curated drugs and compounds, inspect patent-linked evidence, and explore provisional transformations. No route is presented as accepted chemistry unless it has passed review.</p></div>
        <div className="hero-stat"><span>Data stance</span><strong>0</strong><p>invented reactions</p><strong>100<span>%</span></strong><p>provenance-linked edges</p></div>
      </section>
      <LargeGraphExplorer />
      <section className="methods" id="principles"><span className="kicker">How to interpret it</span><h2>Evidence first. Chemistry review second.</h2><div className="method-grid"><article><span>01</span><h3>Source linked</h3><p>Every displayed relationship is derived from the curated catalogue, patent candidate index, or extracted evidence span.</p></article><article><span>02</span><h3>Provisional by default</h3><p>Unreviewed relationships remain visible for research, but they are never shown as accepted manufacturing instructions.</p></article><article><span>03</span><h3>Explore structure</h3><p>Select a compound to inspect its RDKit-derived atom-and-bond representation and connected evidence.</p></article></div></section>
    </main>
    <footer><span>RXN2 · public evidence graph</span><span>Research support only—not manufacturing instructions.</span></footer>
  </div>
}
