import { ArrowRight, FolderOpen, LoaderCircle } from 'lucide-react';
import { ago, initials, shortExchange, statusInfo } from '../lib/format';
import type { Route } from '../lib/useHashRoute';
import type { RunSummary } from '../types';
import { Badge } from './Badge';

export function RunsTable({ runs, navigate, emptyText }:
  { runs: RunSummary[]; navigate: (r: Route) => void; emptyText?: string }) {
  if (!runs.length) {
    return <div className="empty"><FolderOpen size={32} /><h3>No benchmarks yet</h3>
      <p>{emptyText ?? 'Start a benchmark to see it here.'}</p></div>;
  }
  return <div className="table-scroll"><table className="run-table">
    <thead><tr><th>Client</th><th>Status</th><th>Peers</th><th>Year</th><th>Cost</th><th>Updated</th><th /></tr></thead>
    <tbody>
      {runs.map(run => {
        // A run in a working state with no job running was stopped (or the server restarted).
        const stopped = !run.busy && ['created', 'researching', 'analysing'].includes(run.status);
        const status = stopped ? { label: 'Stopped', tone: 'amber' }
          : statusInfo[run.status] ?? { label: run.status, tone: 'gray' };
        return <tr key={run.run_id} onClick={() => navigate({ page: 'run', id: run.run_id })}>
          <td><div className="client-cell">
            <span className="client-avatar">{initials(run.client)}</span>
            <div><strong>{run.legal_name ?? run.client}</strong><small>{run.listing ? run.listing.replace(/^[^:]+/, x => shortExchange(x)) : 'Listing not checked yet'}</small></div>
          </div></td>
          <td>{run.busy
            ? <Badge tone="blue"><LoaderCircle size={12} className="spin" />Working</Badge>
            : <Badge tone={status.tone} dot>{status.label}</Badge>}</td>
          <td><div className="peer-list" title={run.peers.join(', ')}>{run.peers.join(', ') || '—'}</div></td>
          <td>{run.reporting_year ? `FY${run.reporting_year}` : <span className="muted">Latest</span>}</td>
          <td title="Estimated spend on model calls and web searches">{run.usd ? `$${run.usd.toFixed(2)}` : <span className="muted">—</span>}</td>
          <td className="muted">{ago(run.updated_at ?? run.created_at)}</td>
          <td><a className="icon-btn" href={`#/runs/${run.run_id}`} aria-label={`Open ${run.client}`} onClick={e => e.stopPropagation()}><ArrowRight size={16} /></a></td>
        </tr>;
      })}
    </tbody>
  </table></div>;
}
