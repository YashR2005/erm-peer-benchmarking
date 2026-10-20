import { Plus, Search } from 'lucide-react';
import { useState } from 'react';
import { RunsTable } from '../components/RunsTable';
import type { Route } from '../lib/useHashRoute';
import type { RunSummary } from '../types';

const filters = [
  { key: 'all', label: 'All' },
  { key: 'review', label: 'Waiting for you' },
  { key: 'working', label: 'In progress' },
  { key: 'exported', label: 'Exported' },
] as const;

export function BenchmarksPage({ runs, loading, navigate }:
  { runs: RunSummary[] | null; loading: boolean; navigate: (r: Route) => void }) {
  const [filter, setFilter] = useState<(typeof filters)[number]['key']>('all');
  const [query, setQuery] = useState('');
  const shown = (runs ?? []).filter(run => {
    const text = `${run.client} ${run.legal_name ?? ''} ${run.peers.join(' ')}`.toLowerCase();
    if (query && !text.includes(query.toLowerCase())) return false;
    if (filter === 'review') return run.status === 'awaiting_scope' || run.status === 'awaiting_findings';
    if (filter === 'working') return run.busy || run.status === 'researching' || run.status === 'analysing';
    if (filter === 'exported') return run.status === 'exported';
    return true;
  });
  return <>
    <div className="page-heading">
      <div><div className="eyebrow">Benchmarks</div><h1>All benchmarks</h1>
        <p>Every run keeps its sources, decisions and evidence, so it can be reopened and reproduced.</p></div>
      <div className="heading-actions"><button className="btn primary" onClick={() => navigate({ page: 'new' })}><Plus size={16} />New benchmark</button></div>
    </div>
    <section className="panel">
      <div className="toolbar" style={{ paddingTop: 16 }}>
        <div className="segmented" role="tablist">
          {filters.map(f => <button key={f.key} role="tab" aria-selected={filter === f.key} className={filter === f.key ? 'on' : ''} onClick={() => setFilter(f.key)}>{f.label}</button>)}
        </div>
        <label className="push row" style={{ position: 'relative' }}>
          <Search size={15} style={{ position: 'absolute', left: 11, color: 'var(--subtle)' }} />
          <input aria-label="Search benchmarks" placeholder="Search client or peer" value={query} onChange={e => setQuery(e.target.value)} style={{ paddingLeft: 32, width: 260 }} />
        </label>
      </div>
      {loading && !runs ? <p className="empty">Loading…</p>
        : <RunsTable runs={shown} navigate={navigate} emptyText={query || filter !== 'all' ? 'No benchmarks match.' : undefined} />}
    </section>
  </>;
}
