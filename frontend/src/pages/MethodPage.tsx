import { FileCheck2, Layers, ListChecks, Scale, ShieldCheck, Sparkles } from 'lucide-react';
import { Badge } from '../components/Badge';
import { modelLabel } from '../components/Sidebar';
import type { Setup } from '../types';

export function MethodPage({ setup }: { setup: Setup | null }) {
  if (!setup) return <p className="empty">Loading…</p>;
  return <>
    <div className="page-heading">
      <div><div className="eyebrow">Standard setup {setup.version}</div><h1>Method</h1>
        <p>{setup.name}. The same instructions, taxonomy and rules run on every benchmark, so two consultants with the same input get comparable output.</p></div>
    </div>
    <div className="method-grid">
      <section className="panel">
        <div className="panel-heading"><div><h2>How findings are kept honest</h2><p>Rules the code enforces, not the model.</p></div></div>
        <ul className="rules">
          <li><span className="soft-icon green"><FileCheck2 size={17} /></span><div><strong>Every quote is checked against the PDF</strong>A disclosed risk counts only if its quote is found on the cited page (match score ≥ {setup.verify.min_score}, at least {setup.verify.min_quote_chars} characters).</div></li>
          <li><span className="soft-icon"><Sparkles size={17} /></span><div><strong>Sources come from the API</strong>Profile facts and peer figures link to the pages the model actually read during web search.</div></li>
          <li><span className="soft-icon amber"><ShieldCheck size={17} /></span><div><strong>Absence of disclosure is not absence of practice</strong>Gaps read "not publicly evident". A client without its own risk disclosure reads "insufficient evidence" and gets no gap list.</div></li>
          <li><span className="soft-icon"><ListChecks size={17} /></span><div><strong>People decide</strong>You confirm peers and sources, then review themes and evidence before export. Every decision is logged.</div></li>
        </ul>
      </section>
      <div className="stack">
        <section className="panel">
          <div className="panel-heading"><div><h2>Model</h2><p>Set in setup/{setup.version}/method.yaml. Recorded on every run.</p></div></div>
          <div className="panel-body"><div className="kv-grid" style={{ gridTemplateColumns: '1fr 1fr' }}>
            <div className="kv"><span>Model</span><strong>{modelLabel(setup.model)}</strong></div>
            <div className="kv"><span>Provider</span><strong>{setup.provider}</strong></div>
          </div></div>
        </section>
        <section className="panel">
          <div className="panel-heading"><div><h2 className="row"><Scale size={16} />Market-cap buckets</h2><p>Optional filter for the peer scan.</p></div>
            {setup.bucket_status === 'provisional' && <Badge tone="amber" dot>Provisional</Badge>}</div>
          <table className="bucket-table"><thead><tr><th>Bucket</th><th>Range ({setup.bucket_currency})</th></tr></thead>
            <tbody>{setup.buckets.map(b => <tr key={b.key}><td><strong>{b.label}</strong></td><td className="muted">{b.description.split(': ')[1] ?? b.description}</td></tr>)}</tbody>
          </table>
        </section>
      </div>
    </div>
    <section className="panel" style={{ marginTop: 16 }}>
      <div className="panel-heading"><div><h2 className="row"><Layers size={16} />Risk taxonomy</h2><p>The seven enterprise-level categories from the Client X appendix. Each company's own categories are mapped onto these.</p></div></div>
      <div className="taxonomy-grid">
        {setup.taxonomy.map(c => <div className="tax-card" key={c.key}>
          <h3>{c.name}</h3><p>{c.description}</p>
          <div className="chip-row">{c.examples.map(e => <span className="chip" key={e}>{e}</span>)}</div>
        </div>)}
      </div>
    </section>
  </>;
}
