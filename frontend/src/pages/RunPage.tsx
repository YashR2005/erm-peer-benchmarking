import { ArrowLeft, Download, FileSpreadsheet, LoaderCircle, MessageSquareText, RotateCcw, TriangleAlert } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { Badge } from '../components/Badge';
import { Assistant } from '../components/run/Assistant';
import { FindingsView } from '../components/run/Findings';
import { ProgressCard, Stepper } from '../components/run/Progress';
import { ScopeView } from '../components/run/Scope';
import { modelLabel } from '../components/Sidebar';
import { useToast } from '../components/Toast';
import { initials, listing, money, statusInfo } from '../lib/format';
import type { Route } from '../lib/useHashRoute';
import type { RunState, Setup } from '../types';

const finishedMessage: Record<string, string> = {
  research: 'Research finished. Check the peers and sources.',
  analysis: 'Analysis finished. Review the findings.',
  add_peer: 'Peer added.',
  rescan: 'Peer scan updated.',
  chat: 'Done.',
};

function SpendBadge({ spend }: { spend: RunState['spend'] }) {
  const share = Math.max(spend.max_usd ? spend.usd / spend.max_usd : 0,
                         spend.max_searches ? spend.searches / spend.max_searches : 0);
  const tone = share >= 1 ? 'red' : share >= 0.8 ? 'amber' : 'outline';
  return <Badge tone={tone} title={`Estimated from ${spend.input_tokens.toLocaleString()} input and ${spend.output_tokens.toLocaleString()} output tokens. The run stops at its cap.`}>
    ≈ ${spend.usd.toFixed(2)}{spend.max_usd ? ` of $${spend.max_usd.toFixed(2)}` : ''}
    {spend.max_searches ? ` · ${spend.searches}/${spend.max_searches} searches` : ''}
  </Badge>;
}

export type RunActions = {
  state: RunState;
  busy: boolean;
  /** Run an API call that returns the new run state; a started job begins polling. */
  perform: (call: () => Promise<RunState>, success?: string) => Promise<boolean>;
};

export function RunPage({ runId, setup, navigate, onChanged }:
  { runId: string; setup: Setup | null; navigate: (r: Route) => void; onChanged: () => void }) {
  const toast = useToast();
  const [state, setState] = useState<RunState | null>(null);
  const [loadError, setLoadError] = useState('');
  const [pending, setPending] = useState(false);
  const [poll, setPoll] = useState(0);
  const [assistantOpen, setAssistantOpen] = useState(true);
  const wasRunning = useRef<string | null>(null);

  // Poll while a background job runs, and announce how it ended.
  useEffect(() => {
    let active = true;
    let timer: number | undefined;
    async function tick() {
      try {
        const next = await api.run(runId);
        if (!active) return;
        setState(next);
        setLoadError('');
        const job = next.job;
        if (job?.running) {
          wasRunning.current = job.kind;
          timer = window.setTimeout(tick, 1200);
        } else if (wasRunning.current && job) {
          if (job.error) toast.error(job.error);
          else toast.success(finishedMessage[job.kind] ?? 'Done.');
          wasRunning.current = null;
          onChanged();
        }
      } catch (e) {
        if (active) setLoadError(e instanceof Error ? e.message : 'Could not load this benchmark.');
      }
    }
    void tick();
    return () => { active = false; window.clearTimeout(timer); };
  }, [runId, poll, toast, onChanged]);

  const perform = useCallback(async (call: () => Promise<RunState>, success?: string) => {
    setPending(true);
    try {
      const next = await call();
      setState(next);
      if (next.job?.running) setPoll(p => p + 1);
      else if (success) toast.success(success);
      return true;
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'That did not work.');
      return false;
    } finally { setPending(false); }
  }, [toast]);

  async function exportWorkbook() {
    setPending(true);
    try {
      const out = await api.exportRun(runId);
      setState(out.state);
      window.location.href = out.url;
      toast.success('Workbook exported.');
      onChanged();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Export failed.');
    } finally { setPending(false); }
  }

  if (!state) {
    return loadError
      ? <div className="empty"><TriangleAlert size={30} /><h3>Couldn't open this benchmark</h3><p>{loadError}</p>
          <button className="btn" style={{ marginTop: 14 }} onClick={() => navigate({ page: 'benchmarks' })}>Back to benchmarks</button></div>
      : <div className="empty"><LoaderCircle size={28} className="spin" /><p>Loading…</p></div>;
  }

  const { run, profile, job } = state;
  const running = !!job?.running;
  const busy = running || pending;
  const status = statusInfo[run.status];
  const interrupted = !running && ['created', 'researching', 'analysing'].includes(run.status);
  const name = profile?.legal_name ?? state.request.client_name;
  const latestExport = state.exports[state.exports.length - 1];
  const actions: RunActions = { state, busy, perform };

  return <>
    <button className="text-link" style={{ marginBottom: 14 }} onClick={() => navigate({ page: 'benchmarks' })}><ArrowLeft size={14} />All benchmarks</button>
    <div className="run-header">
      <span className="client-avatar">{initials(profile?.short_name ?? state.request.client_name)}</span>
      <div className="grow">
        <div className="eyebrow">Risk profile benchmark</div>
        <h1>{name}</h1>
        <div className="run-meta">
          <Badge tone={running ? 'blue' : status.tone} dot>{running ? 'Working' : status.label}</Badge>
          {profile && <span>{listing(profile)}</span>}
          {profile?.market_cap_sgd_millions != null && <span>{money(profile.market_cap_sgd_millions)} market cap</span>}
          <span>{state.request.reporting_year ? `FY${state.request.reporting_year}` : 'Latest annual report'}</span>
          <span title={run.model}>{modelLabel(run.model)}</span>
          <SpendBadge spend={state.spend} />
          <span className="mono subtle">{run.run_id}</span>
        </div>
      </div>
      <div className="heading-actions">
        <button className="btn" onClick={() => setAssistantOpen(open => !open)} aria-pressed={assistantOpen}>
          <MessageSquareText size={16} />{assistantOpen ? 'Hide assistant' : 'Assistant'}
        </button>
        {latestExport && <a className="btn" href={api.exportUrl(run.run_id, latestExport)}><Download size={16} />Latest workbook</a>}
        {state.findings && <button className="btn primary" disabled={busy} onClick={exportWorkbook}><FileSpreadsheet size={16} />Export to Excel</button>}
      </div>
    </div>

    <Stepper status={run.status} />

    <div className={`workspace ${assistantOpen ? '' : 'no-assistant'}`}>
      <div className="stack" style={{ minWidth: 0 }}>
        {running && job && <ProgressCard job={job} client={state.request.client_name} limits={setup?.time_limits} />}
        {!running && job?.error && <div className="note red" role="alert"><TriangleAlert size={16} />
          <div className="grow"><strong>The last step failed.</strong> {job.error}</div>
          {interrupted && <button className="btn small" onClick={() => perform(() => api.resume(run.run_id))}><RotateCcw size={14} />Try again</button>}
        </div>}
        {interrupted && !job?.error && <div className="note amber"><TriangleAlert size={16} />
          <div className="grow">This benchmark stopped while {run.status === 'analysing' ? 'analysing' : 'researching'} (the server was restarted).</div>
          <button className="btn small" onClick={() => perform(() => api.resume(run.run_id))}><RotateCcw size={14} />Resume</button>
        </div>}
        {run.status === 'awaiting_scope' && state.peers && <ScopeView {...actions} setup={setup} />}
        {(run.status === 'awaiting_findings' || run.status === 'exported') && state.findings &&
          <FindingsView {...actions} setup={setup} />}
      </div>
      {assistantOpen && <Assistant {...actions} />}
    </div>
  </>;
}
