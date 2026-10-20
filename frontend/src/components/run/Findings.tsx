import { BadgeCheck, CircleAlert, Ellipsis, ExternalLink, Eye, Layers, ListTree, MessageSquarePlus, Pencil, RotateCcw, ShieldAlert, Sparkles, Trash2, TriangleAlert, Undo2 } from 'lucide-react';
import { Fragment, useEffect, useRef, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { api } from '../../api';
import { plural } from '../../lib/format';
import type { RunActions } from '../../pages/RunPage';
import type { Findings, Risk, Setup, Theme } from '../../types';
import { Badge } from '../Badge';
import { Modal } from '../Modal';

type Props = RunActions & { setup: Setup | null };
type Tab = 'coverage' | 'gaps' | 'structure' | 'context' | 'evidence';

export function FindingsView(props: Props) {
  const f = props.state.findings!;
  const [tab, setTab] = useState<Tab>('coverage');
  const [cell, setCell] = useState<{ theme: Theme; companyId: string } | null>(null);
  const names = Object.fromEntries(f.companies.map(c => [c.id, c.name]));
  const structured = f.structure.columns.filter(c => c.has_structure).length;
  const tabs: { key: Tab; label: string; count?: number }[] = [
    { key: 'coverage', label: 'Coverage matrix', count: f.rows.length },
    { key: 'gaps', label: 'Gaps', count: f.gaps.length },
    { key: 'structure', label: 'Category structure' },
    { key: 'context', label: 'Context risks', count: f.context_risks.length },
    { key: 'evidence', label: 'Evidence', count: f.risks.length },
  ];

  return <>
    <section className="stats-grid" style={{ margin: 0 }}>
      <Stat label="Disclosed risks" value={`${f.stats.verified}`} foot={`verified quotes of ${f.stats.risks} extracted${f.stats.rejected ? ` · ${f.stats.rejected} rejected` : ''}`} icon={<BadgeCheck size={16} />} tone="green" />
      <Stat label="Risk themes" value={`${f.stats.themes}`} foot={`across ${plural(f.companies.length, 'company', 'companies')}`} icon={<Layers size={16} />} />
      <Stat label="Gaps" value={f.client_disclosed ? `${f.stats.gaps}` : '—'} foot={f.client_disclosed ? 'where the client is silent or differs' : 'not reported: no client disclosure'} icon={<TriangleAlert size={16} />} tone="amber" />
      <Stat label="Category structures" value={`${structured}/${f.companies.length}`} foot="companies that group their risks" icon={<ListTree size={16} />} />
    </section>

    {!f.client_disclosed && <div className="note amber"><ShieldAlert size={16} /><span>
      <strong>Insufficient evidence for the client.</strong> It has no verified risk disclosure of its own, so its column reads "insufficient evidence" and no client gaps are reported. Peers show current practice.</span></div>}
    {f.extractions.filter(e => !e.has_risk_disclosure).map(e => <div key={e.company_id} className="note"><CircleAlert size={16} />
      <span><strong>{names[e.company_id]}:</strong> {e.note || 'no risk disclosure found'}</span></div>)}

    <section className="panel">
      <div className="tabs" role="tablist">
        {tabs.map(t => <button key={t.key} role="tab" aria-selected={tab === t.key} className={`tab ${tab === t.key ? 'on' : ''}`} onClick={() => setTab(t.key)}>
          {t.label}{t.count !== undefined && <span className="count">{t.count}</span>}</button>)}
      </div>
      {tab === 'coverage' && <Coverage {...props} onOpen={(theme, companyId) => setCell({ theme, companyId })} />}
      {tab === 'gaps' && <Gaps f={f} />}
      {tab === 'structure' && <Structure {...props} />}
      {tab === 'context' && <ContextRisks f={f} />}
      {tab === 'evidence' && <EvidenceTab {...props} />}
    </section>

    {cell && <Modal wide title={`${cell.theme.name} · ${names[cell.companyId]}`} subtitle={`${cell.theme.category_name} · evidence behind this cell`} onClose={() => setCell(null)}>
      <CellEvidence risks={f.risks.filter(r => r.theme_key === cell.theme.key && r.company_id === cell.companyId)}
        names={names} {...props} />
    </Modal>}
  </>;
}

function CellEvidence({ risks, names, ...props }: { risks: Risk[]; names: Record<string, string> } & Props) {
  const counted = risks.filter(r => r.counts);
  const other = risks.filter(r => !r.counts);
  return <div className="evidence-list">
    {counted.map(r => <EvidenceCard key={r.id} risk={r} company={names[r.company_id]} {...props} />)}
    {counted.length === 0 && <p className="muted">No quote counts for this cell any more.</p>}
    {other.length > 0 && <>
      <div className="evidence-group">Not counted: not found in the PDF, or rejected by you</div>
      {other.map(r => <EvidenceCard key={r.id} risk={r} company={names[r.company_id]} {...props} />)}
    </>}
  </div>;
}

function Stat({ label, value, foot, icon, tone }: { label: string; value: string; foot: string; icon: ReactNode; tone?: string }) {
  return <div className="panel stat-card">
    <div className="stat-top"><span>{label}</span><span className={`soft-icon ${tone ?? ''}`}>{icon}</span></div>
    <div className="stat-number">{value}</div><div className="stat-foot">{foot}</div>
  </div>;
}

// ------------------------------------------------------------------ Coverage

function Coverage({ state, busy, perform, setup, onOpen }: Props & { onOpen: (t: Theme, c: string) => void }) {
  const f = state.findings!;
  const runId = state.run.run_id;
  const [renaming, setRenaming] = useState<Theme | null>(null);
  const groups: { category: string; name: string; rows: Findings['rows'] }[] = [];
  for (const row of f.rows) {
    const last = groups[groups.length - 1];
    if (last?.category === row.theme.category) last.rows.push(row);
    else groups.push({ category: row.theme.category, name: row.theme.category_name, rows: [row] });
  }
  const change = (key: string, body: { removed?: boolean; name?: string; category?: string }, done: string) =>
    perform(() => api.changeTheme(runId, key, body), done);

  return <>
    <div className="matrix-wrap">
      <table className="matrix">
        <thead><tr>
          <th>Risk theme<small>{plural(f.rows.length, 'theme')} · grouped by category</small></th>
          {f.companies.map(c => <th key={c.id} className={c.id === 'client' ? 'client-col' : ''}>{c.name}<small>{c.id === 'client' ? 'Client' : c.id}</small></th>)}
        </tr></thead>
        <tbody>
          {groups.map(g => <Fragment key={g.category}>
            <tr className="cat-row"><td colSpan={f.companies.length + 1}>{g.name}</td></tr>
            {g.rows.map(row => <tr key={row.theme.key} className="theme-row">
              <td><div className="theme-cell">
                <strong title={row.theme.description}>{row.theme.name}</strong>
                {row.context_risk_ids.length > 0 && <span className="ctx" title={`Context-inferred for the client: ${row.context_risk_ids.join(', ')}`}>context</span>}
                <ThemeMenu theme={row.theme} setup={setup} disabled={busy}
                  onRename={() => setRenaming(row.theme)}
                  onMove={category => change(row.theme.key, { category }, 'Theme moved.')}
                  onRemove={() => change(row.theme.key, { removed: true }, 'Theme removed from the matrix.')} />
              </div></td>
              {f.companies.map(c => {
                const ids = row.cells[c.id] ?? [];
                const client = c.id === 'client';
                return <td key={c.id} className={client ? 'client-col' : ''}>
                  {client && !f.client_disclosed ? <span className="cell-insufficient">insufficient evidence</span>
                    : ids.length ? <button className="cell-hit" onClick={() => onOpen(row.theme, c.id)}
                        aria-label={`${row.theme.name}: ${c.name} discloses it (${ids.length}). Show evidence`}>✓{ids.length > 1 ? ` ${ids.length}` : ''}</button>
                    : <span className="cell-miss" aria-label="Not publicly evident" title="Not publicly evident">—</span>}
                </td>;
              })}
            </tr>)}
          </Fragment>)}
        </tbody>
      </table>
    </div>
    <div className="legend">
      <span><span className="cell-hit">✓</span>Disclosed, verified quote (click for evidence)</span>
      <span><span className="cell-miss">—</span>Not publicly evident (not the same as unmanaged)</span>
      <span><span className="theme-cell"><span className="ctx">context</span></span>Inferred as relevant from the client's profile</span>
    </div>
    {f.removed_themes.length > 0 && <div className="doc-group">
      <h3>Removed themes</h3>
      <div className="chip-row">{f.removed_themes.map(t => <span key={t.key} className="chip">{t.name}
        <button className="text-link" disabled={busy} onClick={() => change(t.key, { removed: false }, 'Theme restored.')}><Undo2 size={12} />Restore</button></span>)}</div>
    </div>}
    {renaming && <RenameModal theme={renaming} onClose={() => setRenaming(null)}
      onSave={async name => { if (await change(renaming.key, { name }, 'Theme renamed.')) setRenaming(null); }} />}
  </>;
}

function ThemeMenu({ theme, setup, disabled, onRename, onMove, onRemove }: {
  theme: Theme; setup: Setup | null; disabled: boolean; onRename: () => void; onMove: (c: string) => void; onRemove: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', esc);
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', esc); };
  }, [open]);
  const pick = (fn: () => void) => () => { setOpen(false); fn(); };
  return <div className="menu theme-menu" ref={ref}>
    <button className="icon-btn" aria-label={`Actions for ${theme.name}`} aria-expanded={open} disabled={disabled} onClick={() => setOpen(o => !o)}><Ellipsis size={16} /></button>
    {open && <div className="menu-pop" role="menu">
      <button role="menuitem" onClick={pick(onRename)}><Pencil size={14} />Rename theme</button>
      <div className="menu-label">Move to category</div>
      {(setup?.taxonomy ?? []).filter(c => c.key !== theme.category).map(c =>
        <button role="menuitem" key={c.key} onClick={pick(() => onMove(c.key))}><Layers size={14} />{c.name}</button>)}
      <hr />
      <button role="menuitem" onClick={pick(onRemove)} style={{ color: 'var(--red)' }}><Trash2 size={14} />Remove from matrix</button>
    </div>}
  </div>;
}

function RenameModal({ theme, onClose, onSave }: { theme: Theme; onClose: () => void; onSave: (name: string) => void }) {
  const [name, setName] = useState(theme.name);
  const submit = (e: FormEvent) => { e.preventDefault(); if (name.trim()) onSave(name.trim()); };
  return <Modal title="Rename theme" subtitle={theme.description} onClose={onClose}>
    <form className="stack" onSubmit={submit}>
      <label className="field">Theme name<input autoFocus value={name} onChange={e => setName(e.target.value)} /></label>
      <div className="row"><button type="button" className="btn push" onClick={onClose}>Cancel</button>
        <button type="submit" className="btn primary" disabled={!name.trim() || name.trim() === theme.name}>Save</button></div>
    </form>
  </Modal>;
}

// ------------------------------------------------------------------ Gaps

const gapTypes = [
  { type: 'relevant_not_disclosed', tone: 'amber', text: 'Inferred as relevant from the client\'s business, but it does not disclose it.' },
  { type: 'peers_disclose_client_silent', tone: 'red', text: 'At least one peer discloses it; the client does not.' },
  { type: 'disclosed_not_in_context', tone: 'blue', text: 'The client discloses it, but it wasn\'t in the context-inferred list.' },
];

function Gaps({ f }: { f: Findings }) {
  if (!f.client_disclosed) return <div className="empty"><ShieldAlert size={30} /><h3>No gaps reported</h3>
    <p>The client has no verified risk disclosure of its own, so gaps against peers would not be meaningful.</p></div>;
  return <div className="gap-grid">
    {gapTypes.map(g => {
      const items = f.gaps.filter(x => x.type === g.type);
      return <div className="gap-card" key={g.type}>
        <h3><Badge tone={g.tone}>{items.length}</Badge>{items[0]?.label ?? labelFor(g.type)}</h3>
        <p>{g.text}</p>
        <ul>{items.length ? items.map(i => <li key={i.theme_key}><strong style={{ fontWeight: 600 }}>{i.theme_name}</strong><span>{i.detail}</span></li>)
          : <li className="muted">None</li>}</ul>
      </div>;
    })}
  </div>;
}
const labelFor = (type: string) => ({ relevant_not_disclosed: 'Relevant to the client, not disclosed by it',
  peers_disclose_client_silent: 'Disclosed by peers, not by the client',
  disclosed_not_in_context: 'Disclosed by the client, missing from the context-inferred list' } as Record<string, string>)[type];

// ------------------------------------------------------------------ Category structure

function Structure({ state, busy, perform, setup }: Props) {
  const f = state.findings!;
  const [remark, setRemark] = useState('');
  const keys = [...(setup?.taxonomy.map(t => t.key) ?? []), 'other'];
  const nameOf = (key: string) => setup?.taxonomy.find(t => t.key === key)?.name ?? 'Outside the taxonomy';
  const columns = f.structure.columns;
  const companyName = (id: string) => f.companies.find(c => c.id === id)?.name ?? id;
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (remark.trim() && await perform(() => api.addRemark(state.run.run_id, remark.trim()), 'Remark added.')) setRemark('');
  };
  return <>
    <div style={{ overflowX: 'auto' }}>
      <table className="structure-table">
        <thead><tr><th>Standard category</th>{columns.map(c => <th key={c.company_id}>{companyName(c.company_id)}</th>)}</tr></thead>
        <tbody>
          <tr className="count-row"><td>Categories used</td>{columns.map(c => <td key={c.company_id}>{c.has_structure ? c.categories.length : 'None found'}</td>)}</tr>
          {keys.filter(k => k !== 'other' || columns.some(c => (f.structure.by_taxonomy[k]?.[c.company_id] ?? []).length)).map(k =>
            <tr key={k}><td><strong style={{ color: 'var(--ink)', fontWeight: 600 }}>{nameOf(k)}</strong></td>
              {columns.map(c => {
                const own = f.structure.by_taxonomy[k]?.[c.company_id] ?? [];
                return <td key={c.company_id}>{own.length ? <div className="chip-row">{own.map(n => <span key={n} className="chip sky">{n}</span>)}</div>
                  : <span className="cell-miss">—</span>}</td>;
              })}</tr>)}
        </tbody>
      </table>
    </div>
    <div className="method-grid" style={{ padding: 20 }}>
      <div>
        <h3 className="row" style={{ marginBottom: 10 }}><Sparkles size={15} color="var(--navy-600)" />Observations</h3>
        <ul className="observations">{f.structure.observations.map(o => <li key={o}><Eye size={14} />{o}</li>)}</ul>
      </div>
      <form onSubmit={submit}>
        <h3 className="row" style={{ marginBottom: 6 }}><MessageSquarePlus size={15} color="var(--navy-600)" />Your remarks</h3>
        <p className="muted" style={{ fontSize: 12.5 }}>As in the Client X appendix, e.g. which categories the client could adopt.</p>
        <div className="remarks">{f.remarks.map((r, i) => <div className="remark" key={i}>{r}</div>)}</div>
        <textarea aria-label="Add a remark" rows={3} value={remark} onChange={e => setRemark(e.target.value)}
          placeholder="e.g. Both peers lack a Sustainability category; the client could start with the 7 standard categories." />
        <button className="btn" style={{ marginTop: 8 }} disabled={busy || !remark.trim()} type="submit">Add remark</button>
      </form>
    </div>
  </>;
}

// ------------------------------------------------------------------ Context risks

function ContextRisks({ f }: { f: Findings }) {
  const themeName = (key: string | null) => f.rows.find(r => r.theme.key === key)?.theme.name;
  return <div className="ctx-list">
    {f.context_risks.map(c => <div className="ctx-card" key={c.id}>
      <h3><span className="mono subtle">{c.id}</span>{c.title}</h3>
      <p><b>Why relevant:</b> {c.driver}</p>
      {c.driver_facts.length > 0 && <p className="muted" style={{ fontSize: 12 }}>{c.driver_facts.map(d => `${d.id}: ${d.text}`).join(' · ')}</p>}
      <p><b>Consequence:</b> {c.consequence}</p>
      <div className="row wrap" style={{ marginTop: 9, gap: 6 }}>
        <Badge tone="outline">{c.affected}</Badge>
        {themeName(c.theme_key) && <Badge tone="sky">{themeName(c.theme_key)}</Badge>}
        {c.warnings.map(w => <Badge key={w} tone="amber">{w}</Badge>)}
      </div>
    </div>)}
  </div>;
}

// ------------------------------------------------------------------ Evidence

function EvidenceTab(props: Props) {
  const f = props.state.findings!;
  const [company, setCompany] = useState('all');
  const [status, setStatus] = useState('all');
  const names = Object.fromEntries(f.companies.map(c => [c.id, c.name]));
  const shown = f.risks.filter(r => (company === 'all' || r.company_id === company) &&
    (status === 'all' || (status === 'verified' && r.verified && !r.rejected) || (status === 'unverified' && !r.verified) || (status === 'rejected' && r.rejected)));
  return <>
    <div className="filters">
      <select aria-label="Company" value={company} onChange={e => setCompany(e.target.value)}>
        <option value="all">All companies</option>{f.companies.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
      </select>
      <select aria-label="Status" value={status} onChange={e => setStatus(e.target.value)}>
        <option value="all">All quotes</option><option value="verified">Verified</option>
        <option value="unverified">Could not verify</option><option value="rejected">Rejected by you</option>
      </select>
      <span className="muted push" style={{ alignSelf: 'center', fontSize: 12.5 }}>{plural(shown.length, 'quote')}</span>
    </div>
    <div className="evidence-list" style={{ padding: '10px 20px 20px' }}>
      {shown.map(r => <EvidenceCard key={r.id} risk={r} company={names[r.company_id]} {...props} />)}
    </div>
  </>;
}

function EvidenceCard({ risk: r, company, state, busy, perform }: { risk: Risk; company: string } & Props) {
  const theme = state.findings!.rows.find(row => row.theme.key === r.theme_key)?.theme
    ?? state.findings!.removed_themes.find(t => t.key === r.theme_key);
  const pdf = r.url ? `${r.url}#page=${r.page}` : null;
  return <article className={`evidence ${r.rejected ? 'rejected' : ''}`}>
    <div className="evidence-head">
      <span className="mono subtle">{r.id}</span><strong>{r.title}</strong>
      <Badge tone="outline">{company}</Badge>
      {theme && <Badge tone="sky">{theme.name}</Badge>}
      {r.company_category && <Badge tone="gray">{r.company_category}</Badge>}
      <span className="push" />
      {r.rejected ? <Badge tone="gray">Rejected</Badge> : r.verified
        ? <Badge tone="green"><BadgeCheck size={12} />Verified · p.{r.page}</Badge>
        : <Badge tone="red" title={r.note}><CircleAlert size={12} />Not found in PDF</Badge>}
    </div>
    <blockquote>{r.quote}</blockquote>
    <div className="evidence-foot">
      <span>{r.summary}</span>
      <span className="push" />
      {r.note && r.verified && <span title="Verification note">{r.note}</span>}
      {pdf && <a className="source-link" href={pdf} target="_blank" rel="noreferrer"><ExternalLink size={12} />{r.doc_title ?? 'Source'} p.{r.page}</a>}
      {r.verified && (r.rejected
        ? <button className="btn small" disabled={busy} onClick={() => perform(() => api.setEvidence(state.run.run_id, r.id, false), 'Quote restored.')}><RotateCcw size={13} />Restore</button>
        : <button className="btn small danger" disabled={busy} onClick={() => perform(() => api.setEvidence(state.run.run_id, r.id, true), 'Quote rejected.')}><Trash2 size={13} />Reject</button>)}
    </div>
  </article>;
}
