import { BookOpen, FolderKanban, LayoutDashboard, Plus } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { Route } from '../lib/useHashRoute';
import type { Setup } from '../types';

// 'claude-opus-5-5' -> 'Claude Opus 5.5'; 'nvidia/nemotron-3.5-super-vl' -> 'Nemotron 3.5 Super VL'
export function modelLabel(model: string): string {
  const name = (model.split('/').pop() ?? model).replace(/(\d)-(?=\d)/g, '$1.');
  return name.split('-').map(w => (/^\d/.test(w) ? w : w.length <= 2 ? w.toUpperCase() : w[0].toUpperCase() + w.slice(1))).join(' ');
}

const providerLabel = (provider: string) =>
  ({ openrouter: 'via OpenRouter', anthropic: 'via Claude API' } as Record<string, string>)[provider] ?? provider;

export function Sidebar({ route, navigate, setup, awaiting }:
  { route: Route; navigate: (r: Route) => void; setup: Setup | null; awaiting: number }) {
  const items: { label: string; icon: LucideIcon; to: Route; count?: number }[] = [
    { label: 'Overview', icon: LayoutDashboard, to: { page: 'overview' } },
    { label: 'New benchmark', icon: Plus, to: { page: 'new' } },
    { label: 'Benchmarks', icon: FolderKanban, to: { page: 'benchmarks' }, count: awaiting },
    { label: 'Method', icon: BookOpen, to: { page: 'method' } },
  ];
  const active = route.page === 'run' ? 'benchmarks' : route.page;
  return <aside className="sidebar">
    <a className="brand" href="#/" aria-label="ERM Peer Benchmarking home">
      <img className="wordmark" src="/marsh-wordmark.png" alt="Marsh" />
      <div className="product">ERM Peer Benchmarking<span>Product A · Phase 1</span></div>
    </a>
    <nav aria-label="Main">
      <div className="nav-label">WORKSPACE</div>
      {items.map(item => <button key={item.label} className={`nav-item ${active === item.to.page ? 'active' : ''}`}
        aria-current={active === item.to.page ? 'page' : undefined} onClick={() => navigate(item.to)}>
        <item.icon size={18} />{item.label}
        {!!item.count && <span className="nav-count" title="Waiting for your review">{item.count}</span>}
      </button>)}
    </nav>
    <div className="sidebar-foot">
      {setup && <div className="model-card" title={`${setup.model} (${setup.provider})`}>
        <img src="/marsh-mark.png" alt="" />
        <div><strong>{modelLabel(setup.model)}</strong><small>{providerLabel(setup.provider)} · method {setup.version}</small></div>
      </div>}
      <p className="sidebar-note">Marsh × NTU prototype. Public disclosures only; every finding links to a verified quote.</p>
    </div>
  </aside>;
}
