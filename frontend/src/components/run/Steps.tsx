import { ExternalLink, ListTree, LoaderCircle, Radio, X } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../../api';
import { clock, domain } from '../../lib/format';
import type { Obj, StepDetail, StepList, StepRow, StepStatus } from '../../types';
import { Badge } from '../Badge';

const tone: Record<StepStatus, string> = {
  'not started': 'gray', done: 'green', running: 'blue', stopped: 'amber', failed: 'red',
};

const dur = (s: number | null | undefined) =>
  s == null ? 'not recorded' : s < 60 ? `${Math.round(s)}s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
const usd = (c: number | null | undefined) => c == null ? 'not recorded' : `$${c.toFixed(c < 0.1 ? 3 : 2)}`;
const text = (v: unknown) => typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean'
  ? String(v) : JSON.stringify(v);

function Sources({ sources }: { sources: { url: string; title?: string }[] }) {
  if (!sources?.length) return <span className="subtle">no source</span>;
  return <>{sources.slice(0, 3).map((s, i) =>
    <a key={i} className="text-link" href={s.url} target="_blank" rel="noreferrer" title={s.title}>
      {domain(s.url)}<ExternalLink size={11} /></a>)}{' '}</>;
}

function Output({ out }: { out: NonNullable<StepDetail['output']> }) {
  switch (out.kind) {
    case 'profile': return <>
      <dl className="kv">{Object.entries(out.fields as Obj).filter(([, v]) => v != null && v !== '' && !(Array.isArray(v) && !v.length))
        .map(([k, v]) => <div key={k} style={{ display: 'contents' }}><dt>{k.replace(/_/g, ' ')}</dt><dd>{Array.isArray(v) ? v.join(', ') : text(v)}</dd></div>)}</dl>
      <h3 className="sec">Facts and their sources</h3>
      {(out.facts as Obj[]).map(f => <div className="step-card" key={f.id}>
        <strong>{f.id}</strong> <span className="subtle">{f.field}</span> {f.text}
        <div><Sources sources={f.sources} />{!f.sources.length && <Badge tone="amber">unsourced</Badge>}</div></div>)}</>;
    case 'peers': return <>
      {out.scan_notes && <p className="subtle" style={{ marginBottom: 8 }}>{out.scan_notes}</p>}
      {(out.candidates as Obj[]).map(c => <div className="step-card" key={c.id}>
        <div className="row"><strong>{c.name}</strong>
          <Badge tone={c.listed ? 'outline' : 'amber'}>{c.listed ? `${c.exchange ?? '?'}: ${c.ticker ?? '?'}` : 'Unlisted'}</Badge>
          <Badge tone="gray">{c.market_cap_sgd_millions ? `S$${Math.round(c.market_cap_sgd_millions)}M` : 'no market cap'}{c.bucket ? ` · ${c.bucket}` : ''}</Badge>
          {c.selected && <Badge tone="green">selected</Badge>}{c.recommended && <Badge tone="blue">recommended</Badge>}</div>
        <div>{c.rationale}</div><div className="subtle">Limitation: {c.key_limitation}</div>
        {c.warnings?.map((w: string, i: number) => <div className="step-warn" key={i}>{w}</div>)}
        <div><Sources sources={c.sources} /></div></div>)}</>;
    case 'documents': return <>{(out.documents as Obj[]).map(d => <div className="step-card" key={d.id}>
      <div className="row"><strong>{d.title}</strong><Badge tone={d.status === 'ok' || d.status === 'uploaded' ? 'green' : d.status === 'found' ? 'gray' : 'red'}>{d.status}</Badge></div>
      <div className="subtle">{d.id} · {d.doc_type.replace(/_/g, ' ')} · FY{d.fiscal_year ?? '?'}{d.page_count ? ` · ${d.page_count} pages` : ''}</div>
      {d.sha256 && <div>Company match: {text(d.checks.company_match)} · Year found: {text(d.checks.year_found)} (match: {text(d.checks.year_match)}) · Risk section: {text(d.checks.has_risk_section)} · Low-text pages: {d.checks.low_text_pages}</div>}
      {d.risk_pages?.length > 0 && <div>Risk pages located: {d.risk_pages.join(', ')}</div>}
      {d.note && <div className="step-warn">{d.note}</div>}
      {d.url && <a className="text-link" href={d.url} target="_blank" rel="noreferrer">{domain(d.url)}<ExternalLink size={11} /></a>}</div>)}</>;
    case 'extraction': return <>
      <p>{out.note}</p>{out.category_structure_note && <p className="subtle" style={{ margin: '6px 0' }}>{out.category_structure_note}</p>}
      {out.categories.length > 0 && <p><strong>Categories:</strong> {out.categories.map((k: Obj) => k.name).join(', ')}</p>}
      <h3 className="sec">Risks, with quote and page</h3>
      {(out.risks as Obj[]).map(r => <div className="step-card" key={r.id}>
        <div className="row"><strong>{r.title}</strong><span className="subtle">{r.id}</span>
          <Badge tone={r.verified ? 'green' : 'red'}>{r.verified ? `verified ${r.score}%` : 'not verified'}</Badge></div>
        <blockquote>{r.quote}</blockquote>
        <div className="subtle">Page {r.page}{r.company_category ? ` · company category: ${r.company_category}` : ''}{r.note ? ` · ${r.note}` : ''}</div></div>)}
      {!out.risks.length && <p className="subtle">No risks were extracted.</p>}</>;
    case 'context_risks': return <>{(out.risks as Obj[]).map(r => <div className="step-card" key={r.id}>
      <strong>{r.id} {r.title}</strong> <span className="subtle">{r.category}</span>
      <div>{r.driver}</div><div className="subtle">Based on: {r.driver_facts.map((f: Obj) => `${f.id}`).join(', ') || 'no fact'}</div>
      {r.warnings?.map((w: string, i: number) => <div className="step-warn" key={i}>{w}</div>)}</div>)}</>;
    case 'themes': return <>{(out.themes as Obj[]).map(t => <div className="step-card" key={t.key}>
      <strong>{t.name}</strong> <Badge tone="outline">{t.category}</Badge> <Badge tone="gray">{t.risks} risks</Badge>
      {t.removed && <Badge tone="amber">removed by you</Badge>}<div className="subtle">{t.description}</div></div>)}</>;
    case 'scope': return <dl className="kv"><dt>Peers</dt><dd>{(out.scope.peer_ids as string[]).join(', ')}</dd>
      <dt>Locked at</dt><dd>{out.scope.locked_at}</dd>
      <dt>Documents chosen</dt><dd>{Object.entries(out.scope.documents as Obj).map(([c, d]) => `${c}: ${d ?? 'none'}`).join(' · ')}</dd></dl>;
    case 'review': return out.remarks.length ? <ul>{out.remarks.map((r: string, i: number) => <li key={i}>{r}</li>)}</ul>
      : <p className="subtle">No remarks written.</p>;
    case 'export': return <ul>{out.files.map((f: string) => <li key={f}>{f}</li>)}</ul>;
    default: return <dl className="kv">{Object.entries(out.fields ?? {}).filter(([, v]) => v != null && v !== '' && !(Array.isArray(v) && !v.length))
      .map(([k, v]) => <div key={k} style={{ display: 'contents' }}><dt>{k.replace(/_/g, ' ')}</dt><dd>{text(v)}</dd></div>)}</dl>;
  }
}

function Detail({ runId, step, actions }:
  { runId: string; step: StepDetail; actions?: React.ReactNode }) {
  const [raw, setRaw] = useState<Obj | null>(null);
  const [full, setFull] = useState<Record<number, unknown>>({});
  useEffect(() => { setRaw(null); setFull({}); }, [step.id]);
  async function toggleRaw() {
    if (raw) { setRaw(null); return; }
    setRaw((await api.step(runId, step.id, { raw: true })).raw ?? {});
  }
  async function showFull(index: number) {
    const d = await api.step(runId, step.id, { full: true });
    const call = d.calls.find(c => c.index === index);
    if (call) setFull(f => ({ ...f, [index]: call.output }));
  }
  return <div className="step-detail" aria-label={`Details of ${step.label}`}>
    <div className="row"><h2>{step.label}</h2><Badge tone={tone[step.status]} dot>{step.status}</Badge>
      <span className="push" />{actions}</div>
    <p className="subtle" style={{ marginTop: 4 }}>{step.summary}</p>
    {step.warnings.length > 0 && <>{<h3 className="sec">Warnings and stop reasons</h3>}
      {step.warnings.map((w, i) => <div className="step-warn" key={i}>{w}</div>)}</>}
    {step.inputs.length > 0 && <><h3 className="sec">What went in</h3>
      <dl className="kv">{step.inputs.map((r, i) => <div key={i} style={{ display: 'contents' }}><dt>{r.label}</dt><dd>{text(r.value)}</dd></div>)}</dl></>}
    <h3 className="sec">What came out</h3>
    {step.output ? <Output out={step.output} /> : <p className="subtle">Nothing saved for this step yet.</p>}
    <h3 className="sec">Time and cost</h3>
    <dl className="kv">
      <dt>Time in model calls</dt><dd>{dur(step.time.seconds_in_model_calls)}</dd>
      <dt>Finished</dt><dd>{step.time.finished_at ? clock(step.time.finished_at) : 'not recorded'}</dd>
      <dt>Cost</dt><dd>{step.cost.calls ? `${usd(step.cost.cost_usd)} · ${step.cost.calls} call(s) · ${step.cost.input_tokens.toLocaleString()} in / ${step.cost.output_tokens.toLocaleString()} out tokens · ${step.cost.searches} searches` : 'not recorded (no model call)'}</dd>
      {step.cost.earlier_calls && <><dt>Before you went back</dt><dd>{step.cost.earlier_calls.calls} call(s), {usd(step.cost.earlier_calls.cost_usd)}</dd></>}
    </dl>
    {step.time.note && <p className="subtle" style={{ marginTop: 4 }}>{step.time.note}</p>}
    <h3 className="sec">Your actions at this step</h3>
    {step.actions.length ? <ul>{step.actions.map((a, i) => <li key={i}><time className="subtle">{clock(a.at)}</time>{' '}
      <strong>{a.action.replace(/_/g, ' ')}</strong> {a.target}{a.reason ? ` · ${a.reason}` : ''}</li>)}</ul>
      : <p className="subtle">None.</p>}
    {step.calls.length > 0 && <><h3 className="sec">Model calls</h3>
      {step.calls.map(c => <details className="step-card" key={c.index}>
        <summary>{c.stage} · {dur(c.seconds)} · {usd(c.cost_usd)} · {c.stop_reason}</summary>
        <div className="subtle">{c.prompt} · {c.model} · effort {c.effort}</div>
        <pre className="step-raw">{typeof (full[c.index] ?? c.output) === 'string' ? String(full[c.index] ?? c.output) : JSON.stringify(full[c.index] ?? c.output, null, 1)}</pre>
        {c.output_truncated && full[c.index] === undefined &&
          <button className="btn small" onClick={() => showFull(c.index)}>Show full output ({c.output_chars.toLocaleString()} characters)</button>}
      </details>)}</>}
    <div style={{ marginTop: 16 }}>
      <button className="btn small" onClick={toggleRaw}>{raw ? 'Hide raw' : 'Show raw'}</button>
      {raw && <pre className="step-raw" style={{ marginTop: 8 }}>{JSON.stringify(raw, null, 2)}</pre>}
    </div>
  </div>;
}

export function StepsPanel({ runId, busy, refreshKey, onClose, renderActions }:
  { runId: string; busy: boolean; refreshKey: string; onClose: () => void;
    renderActions?: (row: StepRow, list: StepList) => React.ReactNode }) {
  const [list, setList] = useState<StepList | null>(null);
  const [selected, setSelected] = useState<string | null>(null);   // null = follow the live step
  const [detail, setDetail] = useState<StepDetail | null>(null);
  const [error, setError] = useState('');
  const [tick, setTick] = useState(0);
  const selectedRef = useRef(selected);
  selectedRef.current = selected;

  // Poll while the run is busy; otherwise reload when the run changes.
  useEffect(() => {
    let active = true;
    let timer: number | undefined;
    async function load() {
      try {
        const next = await api.steps(runId);
        if (!active) return;
        setList(next); setError('');
        const id = selectedRef.current ?? next.live_step;
        if (id) {
          const d = await api.step(runId, id);
          if (active && (selectedRef.current ?? next.live_step) === id) setDetail(d);
        }
        if (active && (busy || next.running)) timer = window.setTimeout(load, 1500);
      } catch (e) {
        if (active) setError(e instanceof Error ? e.message : 'Could not load the steps.');
      }
    }
    void load();
    return () => { active = false; window.clearTimeout(timer); };
  }, [runId, busy, refreshKey, tick]);

  const choose = useCallback((id: string) => { setSelected(id); setTick(t => t + 1); }, []);
  const follow = useCallback(() => { setSelected(null); setTick(t => t + 1); }, []);

  const current = selected ?? list?.live_step ?? null;
  const row = list?.steps.find(s => s.id === current);
  return <section className="panel" aria-label="Steps">
    <div className="steps-bar">
      <ListTree size={18} /><h2>Steps</h2>
      <span className="subtle">What happened at each step of this run, from the files saved on disk.</span>
      <span className="push" />
      {selected !== null && selected !== list?.live_step && list?.live_step &&
        <button className="btn small sky" onClick={follow}><Radio size={13} />Jump to live</button>}
      {selected === null && list?.running && <Badge tone="blue" dot>Following live</Badge>}
      <button className="icon-btn" aria-label="Close steps" onClick={onClose}><X size={18} /></button>
    </div>
    {error && <p className="note red" style={{ margin: 12 }}>{error}</p>}
    {!list ? <p style={{ padding: 20 }}><LoaderCircle size={16} className="spin" /> Loading steps…</p> :
      <div className="steps-panel">
        <nav className="steps-nav" aria-label="Run steps">
          {list.groups.map(g => {
            const rows = list.steps.filter(s => s.group === g.key);
            return rows.length > 0 && <div key={g.key}><h4>{g.label}</h4>
              {rows.map(s => <button key={s.id} className={`step-item ${s.id === current ? 'on' : ''} ${s.id === list.live_step && list.running ? 'live' : ''}`}
                onClick={() => choose(s.id)} aria-current={s.id === current ? 'true' : undefined}>
                <span className={`step-dot ${s.status === 'not started' ? '' : s.status}`}>{s.status === 'running' && <LoaderCircle size={9} className="spin" />}</span>
                <span><span className="step-title">{s.label}</span>
                  <span className="step-sum" style={{ display: 'block' }}>{s.status === 'not started' ? 'Not started' : s.summary}{s.edits ? ` · ${s.edits} edit(s) by you` : ''}</span></span>
              </button>)}</div>;
          })}
        </nav>
        {detail && detail.id === current
          ? <Detail runId={runId} step={detail}
              actions={row && list && renderActions ? renderActions(row, list) : null} />
          : <div className="step-detail"><LoaderCircle size={16} className="spin" /></div>}
      </div>}
  </section>;
}
