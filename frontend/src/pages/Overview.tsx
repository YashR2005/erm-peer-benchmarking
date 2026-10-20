import { ArrowRight, BadgeCheck, ClipboardCheck, FileSearch, FolderKanban, MessageSquareText, Plus, ShieldCheck, Sparkles, Users, Wallet } from 'lucide-react';
import { RunsTable } from '../components/RunsTable';
import type { Route } from '../lib/useHashRoute';
import type { RunSummary } from '../types';

function greeting(now = new Date()) {
  const hour = Number(new Intl.DateTimeFormat('en-SG', { timeZone: 'Asia/Singapore', hour: 'numeric', hourCycle: 'h23' }).format(now));
  return hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
}

export function Overview({ runs, loading, navigate }:
  { runs: RunSummary[] | null; loading: boolean; navigate: (r: Route) => void }) {
  const all = runs ?? [];
  const review = all.filter(r => r.status === 'awaiting_scope' || r.status === 'awaiting_findings');
  const spent = all.reduce((total, r) => total + (r.usd ?? 0), 0);
  const exported = all.filter(r => r.status === 'exported');

  return <>
    <div className="page-heading">
      <div>
        <div className="eyebrow">Risk profile benchmarking</div>
        <h1>{greeting()}.</h1>
        <p>Benchmark a client's public risk disclosures against listed peers, with every finding traced to a verified quote.</p>
      </div>
      <div className="heading-actions">
        <button className="btn primary" onClick={() => navigate({ page: 'new' })}><Plus size={16} />New benchmark</button>
      </div>
    </div>

    <section className="hero">
      <div className="hero-copy">
        <span className="hero-kicker"><Sparkles size={14} />Phase 1 · Product A</span>
        <h2>See how a client's risk disclosures compare with its listed peers.</h2>
        <p>Name the client. The assistant profiles it from live sources, proposes listed peers and reads each annual report's risk section. You confirm the scope and review every finding before export.</p>
        <div className="hero-actions">
          <button className="btn sky large" onClick={() => navigate({ page: 'new' })}>Start a benchmark<ArrowRight size={17} /></button>
          <button className="btn ghost large" onClick={() => navigate({ page: 'method' })}>How it works</button>
        </div>
      </div>
      <div className="hero-art" aria-hidden="true">
        <img className="mark" src="/marsh-mark.png" alt="" />
        <div className="mini-matrix">
          <div className="mm-head"><span>Risk theme</span><span>Client</span><span>A</span><span>B</span><span>C</span></div>
          {[['Interest rate', 1, 1, 1, 1], ['Property market', 1, 1, 0, 1], ['Cybersecurity', 0, 1, 1, 0], ['Climate', 2, 1, 0, 1]].map(([name, ...cells]) =>
            <div className="mm-row" key={name as string}><span>{name}</span>{cells.map((c, i) => <i key={i} className={`mm-dot ${c === 0 ? 'off' : c === 2 ? 'gap' : ''}`} />)}</div>)}
        </div>
        <div className="mini-quote">
          <q>Rising interest rates may increase the Group's financing costs on its borrowings.</q>
          <footer><BadgeCheck size={14} />Verified on page 21</footer>
        </div>
      </div>
    </section>

    <section className="stats-grid">
      <Stat icon={FolderKanban} label="Benchmarks" value={all.length} foot="All runs on this machine" onClick={() => navigate({ page: 'benchmarks' })} />
      <Stat icon={ClipboardCheck} label="Waiting for you" value={review.length} foot="Scope to confirm or findings to review" tone="amber" onClick={() => navigate({ page: 'benchmarks' })} />
      <Stat icon={Wallet} label="Estimated spend" value={`$${spent.toFixed(2)}`} foot="Model calls and web searches, all runs" />
      <Stat icon={BadgeCheck} label="Exported" value={exported.length} foot="Excel workbooks produced" tone="green" />
    </section>

    <div className="overview-grid">
      <section className="panel">
        <div className="panel-heading">
          <div><h2>Recent benchmarks</h2><p>Open one to continue where you left off.</p></div>
          <button className="text-link" onClick={() => navigate({ page: 'benchmarks' })}>All benchmarks<ArrowRight size={14} /></button>
        </div>
        {loading && !runs ? <p className="empty">Loading…</p> : <RunsTable runs={all.slice(0, 6)} navigate={navigate} />}
      </section>
      <section className="panel">
        <div className="panel-heading"><div><h2>How a benchmark runs</h2><p>Fixed steps on every run, so results are comparable.</p></div></div>
        <ul className="steps-list">
          <li><span className="soft-icon"><Users size={17} /></span><div><strong>Profile and peers</strong><span>Live web research with cited sources. Listed peers only, optionally within a market-cap bucket.</span></div></li>
          <li><span className="soft-icon"><ClipboardCheck size={17} /></span><div><strong>You confirm the scope</strong><span>Tick peers and check the annual reports before anything is read.</span></div></li>
          <li><span className="soft-icon"><FileSearch size={17} /></span><div><strong>Risk sections read and verified</strong><span>Every quote is checked against the PDF text before it counts.</span></div></li>
          <li><span className="soft-icon"><MessageSquareText size={17} /></span><div><strong>You review, then export</strong><span>Coverage matrix, gaps and category structures, exported to Excel.</span></div></li>
        </ul>
        <div className="note principle"><ShieldCheck size={16} /><span>A risk that isn't publicly disclosed may still be managed. Gaps mean "not publicly evident", never "not managed".</span></div>
      </section>
    </div>
  </>;
}

function Stat({ icon: Icon, label, value, foot, tone, onClick }:
  { icon: typeof Users; label: string; value: number | string; foot: string; tone?: string; onClick?: () => void }) {
  const content = <>
    <div className="stat-top"><span>{label}</span><span className={`soft-icon ${tone ?? ''}`}><Icon size={16} /></span></div>
    <div className="stat-number">{value}</div>
    <div className="stat-foot">{foot}</div>
  </>;
  return onClick ? <button className="panel stat-card" onClick={onClick}>{content}</button>
    : <div className="panel stat-card">{content}</div>;
}
