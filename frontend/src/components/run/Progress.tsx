import { Check, CircleCheck, LoaderCircle } from 'lucide-react';
import { Fragment, useEffect, useState } from 'react';
import { clock } from '../../lib/format';
import type { Job, RunStatus } from '../../types';

const steps = [
  { label: 'Research', hint: 'Profile and peers' },
  { label: 'Confirm scope', hint: 'Your checkpoint' },
  { label: 'Analysis', hint: 'Read and verify' },
  { label: 'Review findings', hint: 'Your checkpoint' },
  { label: 'Export', hint: 'Excel workbook' },
];
const position: Record<RunStatus, number> = {
  created: 0, researching: 0, awaiting_scope: 1, analysing: 2, awaiting_findings: 3, exported: 5,
};

export function Stepper({ status }: { status: RunStatus }) {
  const at = position[status];
  return <nav className="panel stepper" aria-label="Benchmark stages">
    {steps.map((step, i) => <Fragment key={step.label}>
      {i > 0 && <span className={`step-line ${i <= at ? 'done' : ''}`} />}
      <div className={`step ${i < at ? 'done' : i === at ? 'current' : ''}`} aria-current={i === at ? 'step' : undefined}>
        <span className="step-num">{i < at ? <Check size={14} /> : i + 1}</span>
        <div>{step.label}<small>{step.hint}</small></div>
      </div>
    </Fragment>)}
  </nav>;
}

const titles: Record<string, (client: string) => string> = {
  research: c => `Researching ${c}`,
  analysis: () => 'Reading and verifying risk sections',
  add_peer: () => 'Adding a peer',
  rescan: () => 'Re-running the peer scan',
  chat: () => 'Working on your instruction',
};
const subtitles: Record<string, string> = {
  research: 'Live web research, then a light check of every annual report. Usually a few minutes.',
  analysis: 'Only the client and the shortlisted peers are read. Every quote is checked against the PDF.',
  add_peer: 'Researching the company and checking its annual report.',
  rescan: 'Finding listed peers in the new bucket and checking their annual reports.',
  chat: 'The assistant is applying your request to the fixed steps.',
};

export function ProgressCard({ job, client }: { job: Job; client: string }) {
  const [, setNow] = useState(0);
  useEffect(() => { const t = window.setInterval(() => setNow(n => n + 1), 1000); return () => window.clearInterval(t); }, []);
  const seconds = Math.max(0, Math.round((Date.now() - new Date(job.started_at).getTime()) / 1000));
  const elapsed = seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  return <section className="panel progress-card" aria-live="polite">
    <div className="progress-orb"><LoaderCircle size={22} className="spin" /></div>
    <div>
      <div className="row"><h2>{(titles[job.kind] ?? (() => 'Working'))(client)}</h2><span className="push mono subtle">{elapsed}</span></div>
      <p>{subtitles[job.kind] ?? ''}</p>
      <ul className="progress-log">
        {job.progress.length === 0 && <li className="now"><LoaderCircle size={15} className="spin" />Starting…</li>}
        {job.progress.map((entry, i) => {
          const last = i === job.progress.length - 1;
          return <li key={i} className={last ? 'now' : 'done'}>
            {last ? <LoaderCircle size={15} className="spin" /> : <CircleCheck size={15} />}{entry.message}
            <time>{clock(entry.at)}</time>
          </li>;
        })}
      </ul>
    </div>
  </section>;
}
