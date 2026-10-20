import { ArrowRight, Ban, ChevronDown, ChevronRight, CircleCheck, CircleX, ExternalLink, FileText, Link2, LoaderCircle, Minus, Plus, ShieldAlert, TriangleAlert, Upload } from 'lucide-react';
import { Fragment, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { api } from '../../api';
import { availabilityInfo, bucketLabel, docStatusInfo, docTypeLabel, domain, initials, money, plural } from '../../lib/format';
import type { RunActions } from '../../pages/RunPage';
import type { Availability, BucketKey, Document, PeerCandidate, Setup } from '../../types';
import { Badge } from '../Badge';

type Props = RunActions & { setup: Setup | null };

export function ScopeView(props: Props) {
  const { state } = props;
  const selected = state.peers!.candidates.filter(c => c.selected);
  const companies = ['client', ...selected.map(c => c.id)];
  const ready = companies.filter(id => state.companies[id]?.primary_doc_id).length;
  return <>
    <div className="scope-top">
      <ClientCard {...props} />
      <DisclosureCard {...props} />
    </div>
    <PeersPanel {...props} />
    <SourcesPanel {...props} />
    <div className="action-bar">
      <div className="grow">
        <strong>{plural(selected.length, 'peer')} selected</strong><br />
        <span>Usable annual report for {ready} of {companies.length} companies. Companies without one read "insufficient evidence".</span>
      </div>
      <button className="btn sky large" disabled={props.busy || !selected.length}
        onClick={() => props.perform(() => api.lock(state.run.run_id))}>
        Lock scope and run analysis<ArrowRight size={17} />
      </button>
    </div>
  </>;
}

function Meter({ value }: { value: Availability }) {
  return <span className={`meter ${value}`} aria-hidden="true"><i /><i /><i /></span>;
}

function SourceList({ sources }: { sources: { url: string; cited_text: string; title: string }[] }) {
  const unique = [...new Map(sources.map(s => [s.url, s])).values()];
  return <span className="row wrap" style={{ gap: 8 }}>
    {unique.map(s => <a key={s.url} className="source-link" href={s.url} target="_blank" rel="noreferrer" title={s.cited_text || s.url}>
      <ExternalLink size={11} />{domain(s.url)}</a>)}
  </span>;
}

function ClientCard({ state }: Props) {
  const p = state.profile!;
  const sourced = p.facts.filter(f => f.sources.length).length;
  return <section className="panel">
    <div className="panel-heading">
      <div className="profile-head">
        <span className="client-avatar">{initials(p.short_name)}</span>
        <div>
          <h2>{p.legal_name}</h2>
          <div className="row wrap" style={{ marginTop: 6, gap: 6 }}>
            <Badge tone={p.listed ? 'navy' : 'amber'}>{p.listed ? `${p.exchange}: ${p.ticker}` : 'Unlisted'}</Badge>
            <Badge tone="sky">{bucketLabel(p.bucket)}</Badge>
            <Badge tone="outline">{money(p.market_cap_sgd_millions)}{p.market_cap_as_of ? ` · ${p.market_cap_as_of}` : ''}</Badge>
            {p.headquarters && <Badge tone="outline">{p.headquarters}</Badge>}
          </div>
        </div>
      </div>
      <Badge tone="gray" title="Sector guide used for search and typical risks">{p.sector_guide.replace('_', ' ')} guide</Badge>
    </div>
    <div className="panel-body">
      <p className="profile-desc" style={{ marginTop: 0 }}>{p.business_description}</p>
      <div className="kv-grid">
        <div className="kv"><span>Total assets</span><strong>{p.total_assets ?? '—'}</strong></div>
        <div className="kv"><span>Revenue</span><strong>{p.revenue ?? '—'}</strong></div>
        <div className="kv"><span>EBITDA</span><strong>{p.ebitda ?? '—'}</strong></div>
        <div className="kv"><span>Employees</span><strong>{p.employees ?? '—'}</strong></div>
      </div>
      {p.segments.length > 0 && <><div className="label-sm">Segments</div><div className="chip-row">{p.segments.map(s => <span key={s} className="chip">{s}</span>)}</div></>}
      {p.countries.length > 0 && <><div className="label-sm">Countries</div><div className="chip-row">{p.countries.map(s => <span key={s} className="chip sky">{s}</span>)}</div></>}
      {p.warnings.length > 0 && <div className="stack" style={{ gap: 8, marginTop: 14 }}>
        {p.warnings.map(w => <div key={w} className="note amber"><TriangleAlert size={15} /><span>{w}</span></div>)}
      </div>}
      <details className="disclosure">
        <summary><ChevronRight size={14} />Sourced facts ({sourced} of {p.facts.length} with a source)</summary>
        <ul className="facts">
          {p.facts.map(f => <li key={f.id}><span className="mono">{f.id}</span>
            <span className="grow">{f.text} {f.sources.length ? <SourceList sources={f.sources} /> : <Badge tone="amber">unsourced</Badge>}</span></li>)}
        </ul>
      </details>
    </div>
  </section>;
}

function DisclosureCard({ state }: Props) {
  const info = state.companies.client;
  const availability = info?.availability ?? 'none';
  const doc = state.documents.find(d => d.id === info?.primary_doc_id);
  const a = availabilityInfo[availability];
  return <section className="panel">
    <div className="panel-heading"><div><h2>Client disclosure</h2><p>What the client publishes about its own risks.</p></div></div>
    <div className="panel-body stack" style={{ gap: 12 }}>
      <div className="availability"><Meter value={availability} />
        <div><strong>{a.label} disclosure</strong><small>{a.text}</small></div></div>
      {doc && <div className="row" style={{ fontSize: 13 }}><FileText size={16} color="var(--navy-600)" />
        <div className="grow"><strong style={{ color: 'var(--ink)' }}>{doc.title}</strong>
          <div className="muted" style={{ fontSize: 12 }}>{doc.fiscal_year ? `FY${doc.fiscal_year} · ` : ''}{doc.page_count} pages · risk section on {plural(doc.risk_pages.length, 'page')}</div></div>
        {doc.url && <a className="icon-btn" href={doc.url} target="_blank" rel="noreferrer" aria-label="Open document"><ExternalLink size={15} /></a>}
      </div>}
      {availability !== 'good'
        ? <div className="note amber"><ShieldAlert size={15} /><span>With little public disclosure, the client column will read "insufficient evidence" and no client gaps are reported (peer reference only). You can add a document below under Sources.</span></div>
        : <div className="note"><CircleCheck size={15} /><span>The client has its own risk disclosure, so gaps against peers will be reported.</span></div>}
    </div>
  </section>;
}

function PeersPanel({ state, busy, perform, setup }: Props) {
  const peers = state.peers!;
  const [name, setName] = useState('');
  const [open, setOpen] = useState<string | null>(null);
  const runId = state.run.run_id;
  const docsById = Object.fromEntries(state.documents.map(d => [d.id, d]));

  const addPeer = async (e: FormEvent) => {
    e.preventDefault();
    if (name.trim() && await perform(() => api.addPeer(runId, name.trim()))) setName('');
  };
  const setBucket = (bucket: BucketKey | null) => {
    if (bucket !== peers.bucket_filter) void perform(() => api.setBucket(runId, bucket));
  };

  return <section className="panel">
    <div className="panel-heading">
      <div><h2>Peer candidates</h2><p>{peers.scan_notes || 'Listed companies proposed by the assistant, with the columns of Marsh\'s peer selection table.'}</p></div>
      <Badge tone="sky">{plural(peers.candidates.filter(c => c.selected).length, 'peer')} selected</Badge>
    </div>
    <div className="toolbar">
      <div className="segmented" role="radiogroup" aria-label="Market-cap bucket">
        <button role="radio" aria-checked={!peers.bucket_filter} disabled={busy} className={!peers.bucket_filter ? 'on' : ''} onClick={() => setBucket(null)}>All listed</button>
        {(setup?.buckets ?? []).map(b => <button key={b.key} role="radio" aria-checked={peers.bucket_filter === b.key} disabled={busy}
          title={b.description} className={peers.bucket_filter === b.key ? 'on' : ''} onClick={() => setBucket(b.key)}>{b.label}</button>)}
      </div>
      <div className="stepper-input" role="group" aria-label="Peers to use" title="Peers to use (recommended first)">
        <button aria-label="Fewer peers" disabled={busy || peers.peer_count <= 1} onClick={() => perform(() => api.setPeerCount(runId, peers.peer_count - 1))}><Minus size={14} /></button>
        <span>{peers.peer_count}</span>
        <button aria-label="More peers" disabled={busy || peers.peer_count >= 10} onClick={() => perform(() => api.setPeerCount(runId, peers.peer_count + 1))}><Plus size={14} /></button>
      </div>
      <form className="add-inline push" onSubmit={addPeer}>
        <input aria-label="Add a peer by name" placeholder="Add a company by name" value={name} onChange={e => setName(e.target.value)} disabled={busy} />
        <button className="btn" type="submit" disabled={busy || !name.trim()}><Plus size={15} />Add peer</button>
      </form>
    </div>
    <div style={{ overflowX: 'auto' }}>
      <table className="peer-table">
        <thead><tr><th style={{ width: 60 }}>Use</th><th>Peer</th><th>Market cap</th><th>Annual report</th><th>Disclosure</th><th>Pick</th><th /></tr></thead>
        <tbody>
          {peers.candidates.map(c => {
            const company = state.companies[c.id];
            const doc = company?.primary_doc_id ? docsById[company.primary_doc_id] : undefined;
            const availability = company?.availability ?? 'none';
            const expanded = open === c.id;
            return <Fragment key={c.id}>
              <tr className={`peer-row ${c.selected ? '' : 'off'}`}>
                <td><button className={`toggle ${c.selected ? 'on' : ''}`} role="switch" aria-checked={c.selected}
                  aria-label={`Use ${c.name}`} disabled={busy} onClick={() => perform(() => api.selectPeer(runId, c.id, !c.selected))} /></td>
                <td><div className="peer-name"><strong>{c.name} <span className="mono subtle">{c.id}</span></strong><small>{c.listed ? `${c.exchange}: ${c.ticker}` : 'Unlisted'}{c.headquarters ? ` · ${c.headquarters}` : ''}</small></div></td>
                <td><div style={{ fontWeight: 650, color: 'var(--ink)' }}>{money(c.market_cap_sgd_millions)}</div><Badge tone={peers.bucket_filter && c.bucket !== peers.bucket_filter ? 'amber' : 'sky'}>{bucketLabel(c.bucket)}</Badge></td>
                <td>{doc ? <><Badge tone={docStatusInfo[doc.status].tone} dot>{doc.fiscal_year ? `FY${doc.fiscal_year}` : docStatusInfo[doc.status].label}</Badge>
                  <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>{doc.checks.has_risk_section ? 'Risk section found' : 'No risk section found'}</div></>
                  : <Badge tone="red" dot>None usable</Badge>}</td>
                <td><span className="row" style={{ gap: 8 }}><Meter value={availability} />{availabilityInfo[availability].label}</span></td>
                <td>{c.added_by === 'consultant' ? <Badge tone="blue">Added by you</Badge> : c.recommended ? <Badge tone="navy">Recommended</Badge> : <Badge tone="gray">Alternative</Badge>}
                  {c.warnings.length > 0 && <span title={c.warnings.join('\n')} style={{ marginLeft: 6, color: 'var(--amber)' }}><TriangleAlert size={14} /></span>}</td>
                <td><button className="icon-btn" aria-expanded={expanded} aria-label={`Details for ${c.name}`} onClick={() => setOpen(expanded ? null : c.id)}>
                  {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}</button></td>
              </tr>
              {expanded && <tr className="details"><td colSpan={7}><PeerDetails c={c} /></td></tr>}
            </Fragment>;
          })}
        </tbody>
      </table>
    </div>
  </section>;
}

function PeerDetails({ c }: { c: PeerCandidate }) {
  return <div className="peer-details">
    <div>
      <h4>Why comparable</h4><p>{c.rationale || '—'}</p>
      <h4>Key limitation</h4><p>{c.key_limitation || '—'}</p>
      {c.warnings.length > 0 && <><h4>Warnings</h4>{c.warnings.map(w => <p key={w} style={{ color: 'var(--amber)' }}>{w}</p>)}</>}
    </div>
    <div>
      <h4>Profile</h4>
      <div className="kv-grid" style={{ gridTemplateColumns: '1fr 1fr' }}>
        <div className="kv"><span>Total assets</span><strong>{c.total_assets ?? '—'}</strong></div>
        <div className="kv"><span>Revenue</span><strong>{c.revenue ?? '—'}</strong></div>
        <div className="kv"><span>EBITDA</span><strong>{c.ebitda ?? '—'}</strong></div>
        <div className="kv"><span>Employees</span><strong>{c.employees ?? '—'}</strong></div>
      </div>
      <h4>Legal name</h4><p>{c.legal_name}</p>
      <h4>Segments · markets</h4><p>{c.segments.join(', ') || '—'} · {c.main_markets.join(', ') || '—'} · {c.operating_countries ?? '?'} countries</p>
      {c.sources.length > 0 && <><h4>Sources</h4><SourceList sources={c.sources} /></>}
    </div>
  </div>;
}

function SourcesPanel({ state, busy, perform }: Props) {
  const runId = state.run.run_id;
  const ids = ['client', ...state.peers!.candidates.filter(c => c.selected).map(c => c.id)];
  const name = (id: string) => id === 'client' ? state.profile!.short_name : state.peers!.candidates.find(c => c.id === id)?.name ?? id;
  return <section className="panel">
    <div className="panel-heading"><div><h2>Sources</h2><p>Documents found by the light scan for the client and selected peers. Only these are read in the analysis.</p></div></div>
    <div className="doc-list">
      {ids.map(id => <DocGroup key={id} companyId={id} name={name(id)} primary={state.companies[id]?.primary_doc_id ?? null}
        docs={state.documents.filter(d => d.company_id === id)} busy={busy}
        onNa={doc => perform(() => api.markNa(runId, doc.id, 'Marked not available by the consultant'), 'Marked not available.')}
        onUrl={url => perform(() => api.addDocumentUrl(runId, id, url), 'Document added.')}
        onFile={file => perform(() => api.uploadDocument(runId, id, file), 'Document uploaded.')} />)}
    </div>
  </section>;
}

function DocGroup({ companyId, name, docs, primary, busy, onNa, onUrl, onFile }: {
  companyId: string; name: string; docs: Document[]; primary: string | null; busy: boolean;
  onNa: (d: Document) => void; onUrl: (url: string) => Promise<boolean>; onFile: (f: File) => Promise<boolean>;
}) {
  const [url, setUrl] = useState('');
  const [adding, setAdding] = useState(false);
  const file = useRef<HTMLInputElement>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!url.trim()) return;
    setAdding(true);
    if (await onUrl(url.trim())) setUrl('');
    setAdding(false);
  };
  return <div className="doc-group">
    <h3>{name}{companyId === 'client' && <Badge tone="navy">Client</Badge>}</h3>
    {docs.length === 0 && <p className="muted" style={{ fontSize: 12.5 }}>No documents found.</p>}
    {docs.map(d => {
      const s = docStatusInfo[d.status];
      const Icon = d.status === 'ok' || d.status === 'uploaded' ? CircleCheck : d.status === 'na' ? Ban : d.status === 'found' ? FileText : CircleX;
      return <div className="doc-row" key={d.id}>
        <Icon size={17} color={`var(--${s.tone === 'gray' ? 'subtle' : s.tone})`} />
        <div style={{ minWidth: 0 }}>
          <div className="doc-title">{d.title} {d.id === primary && <Badge tone="sky">Will be read</Badge>}</div>
          <div className="doc-note">{docTypeLabel(d.doc_type)}{d.fiscal_year ? ` · FY${d.fiscal_year}` : ''}{d.page_count ? ` · ${d.page_count} pages` : ''}{d.risk_pages.length ? ` · risk pages ${d.risk_pages[0]}–${d.risk_pages[d.risk_pages.length - 1]}` : ''}{d.note ? ` · ${d.note}` : ''}</div>
        </div>
        <div className="doc-actions">
          <Badge tone={s.tone}>{s.label}</Badge>
          {d.url && <a className="icon-btn" href={d.url} target="_blank" rel="noreferrer" aria-label={`Open ${d.title}`}><ExternalLink size={14} /></a>}
          {d.status !== 'na' && <button className="btn ghost small" disabled={busy} onClick={() => onNa(d)}>Mark n/a</button>}
        </div>
      </div>;
    })}
    <form className="doc-add" onSubmit={submit}>
      <input aria-label={`PDF link for ${name}`} placeholder="Paste a PDF or SGX announcement link" value={url} onChange={e => setUrl(e.target.value)} disabled={busy} />
      <button className="btn small" type="submit" disabled={busy || !url.trim()}>{adding ? <LoaderCircle size={14} className="spin" /> : <Link2 size={14} />}Use link</button>
      <button className="btn small" type="button" disabled={busy} onClick={() => file.current?.click()}><Upload size={14} />Upload PDF</button>
      <input ref={file} type="file" accept="application/pdf" hidden onChange={e => { const f = e.target.files?.[0]; if (f) void onFile(f); e.target.value = ''; }} />
    </form>
  </div>;
}
