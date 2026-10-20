import { ArrowRight, LoaderCircle, Minus, Plus, Sparkles } from 'lucide-react';
import { useState } from 'react';
import type { FormEvent } from 'react';
import { api } from '../api';
import { useToast } from '../components/Toast';
import type { Route } from '../lib/useHashRoute';
import type { BucketKey, Setup } from '../types';

const thisYear = new Date().getFullYear();

export function NewBenchmark({ setup, navigate }: { setup: Setup | null; navigate: (r: Route) => void }) {
  const toast = useToast();
  const [client, setClient] = useState('');
  const [year, setYear] = useState<string>('');
  const [peers, setPeers] = useState(setup?.default_peer_count ?? 3);
  const [bucket, setBucket] = useState<BucketKey | null>(null);
  const [regions, setRegions] = useState('');
  const [notes, setNotes] = useState('');
  const [sentence, setSentence] = useState('');
  const [busy, setBusy] = useState<'form' | 'sentence' | null>(null);
  const bucketInfo = setup?.buckets.find(b => b.key === bucket);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!client.trim()) { toast.error("Enter the client's name."); return; }
    setBusy('form');
    try {
      const state = await api.createRun({
        client_name: client.trim(), reporting_year: year ? Number(year) : null, peer_count: peers,
        market_cap_bucket: bucket, notes: notes.trim(),
        focus_regions: regions.split(',').map(r => r.trim()).filter(Boolean),
      });
      navigate({ page: 'run', id: state.run.run_id });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not start the benchmark.');
    } finally { setBusy(null); }
  }

  async function describe(event: FormEvent) {
    event.preventDefault();
    if (!sentence.trim()) return;
    setBusy('sentence');
    try {
      const result = await api.startByChat(sentence.trim());
      if (result.run_id) navigate({ page: 'run', id: result.run_id });
      else toast.info(result.reply ?? 'Which company should I benchmark?');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not start the benchmark.');
    } finally { setBusy(null); }
  }

  return <>
    <div className="page-heading">
      <div><div className="eyebrow">New benchmark</div><h1>Who are we benchmarking?</h1>
        <p>Only the client's name is required. Everything else has a sensible default, and you can change the peers before anything is read.</p></div>
    </div>
    <div className="new-grid">
      <form className="panel" onSubmit={submit}>
        <div className="panel-heading"><div><h2>Benchmark details</h2><p>Risk profile benchmarking (Product A). ERM framework scoring (Product B) comes in phase 2.</p></div></div>
        <div className="form-grid">
          <label className="field full">Client
            <input autoFocus value={client} onChange={e => setClient(e.target.value)} placeholder="e.g. City Developments" aria-required="true" />
            <small>The name is resolved to the listed entity, with its exchange, ticker and market cap.</small>
          </label>
          <label className="field">Reporting year
            <select value={year} onChange={e => setYear(e.target.value)}>
              <option value="">Latest available</option>
              {[1, 2, 3, 4].map(n => <option key={n} value={thisYear - n}>FY{thisYear - n}</option>)}
            </select>
          </label>
          <div className="field" role="group" aria-label="Number of peers">
            <label className="field" htmlFor="peer-count">Peers to benchmark against</label>
            <div className="stepper-input">
              <button type="button" aria-label="Fewer peers" disabled={peers <= 1} onClick={() => setPeers(p => p - 1)}><Minus size={15} /></button>
              <span id="peer-count" aria-live="polite">{peers}</span>
              <button type="button" aria-label="More peers" disabled={peers >= 10} onClick={() => setPeers(p => p + 1)}><Plus size={15} /></button>
            </div>
          </div>
          <div className="field full">
            <span className="field-label">Market-cap bucket <small>(optional)</small></span>
            <div className="segmented" role="radiogroup" aria-label="Market-cap bucket" style={{ marginTop: 7 }}>
              <button type="button" role="radio" aria-checked={bucket === null} className={bucket === null ? 'on' : ''} onClick={() => setBucket(null)}>All listed</button>
              {setup?.buckets.map(b => <button type="button" role="radio" aria-checked={bucket === b.key} key={b.key} className={bucket === b.key ? 'on' : ''} onClick={() => setBucket(b.key)}>{b.label}</button>)}
            </div>
            <p className="bucket-hint">{bucketInfo ? `${bucketInfo.description}.` : 'Peers are listed companies of any size.'}
              {setup?.bucket_status === 'provisional' && ' Bucket ranges are provisional until Marsh confirms them.'}</p>
          </div>
          <label className="field">Focus regions <small>Optional, comma separated</small>
            <input value={regions} onChange={e => setRegions(e.target.value)} placeholder="e.g. Singapore, China" />
          </label>
          <label className="field">Notes for the assistant <small>Optional</small>
            <input value={notes} onChange={e => setNotes(e.target.value)} placeholder="e.g. reviewing its risk categories" />
          </label>
        </div>
        <div className="form-foot">
          <span className="muted" style={{ fontSize: 12.5 }}>Research takes a few minutes. You can leave this page.</span>
          <button className="btn primary large push" type="submit" disabled={!!busy}>
            {busy === 'form' ? <LoaderCircle size={17} className="spin" /> : null}Start benchmark<ArrowRight size={17} />
          </button>
        </div>
      </form>

      <div className="stack">
        <form className="panel describe-box" onSubmit={describe}>
          <div className="panel-heading"><div><h2 className="row"><Sparkles size={16} color="var(--navy-600)" />Or describe it</h2><p>Write it the way you would ask a colleague.</p></div></div>
          <div className="panel-body">
            <textarea aria-label="Describe the benchmark" value={sentence} onChange={e => setSentence(e.target.value)}
              placeholder="Benchmark City Developments for FY2025 against 3 large-cap Singapore developers" />
            <button className="btn sky" type="submit" disabled={!!busy || !sentence.trim()} style={{ marginTop: 10, width: '100%' }}>
              {busy === 'sentence' ? <LoaderCircle size={16} className="spin" /> : <Sparkles size={16} />}Start from description
            </button>
          </div>
        </form>
        <section className="panel">
          <div className="panel-heading"><div><h2>What happens next</h2></div></div>
          <ul className="timeline">
            <li><i /><strong>Research</strong><span>Sourced client profile, listed peer candidates, and a light check of each annual report.</span></li>
            <li><i /><strong>You confirm the scope</strong><span>Tick peers, change the bucket, fix or upload sources.</span></li>
            <li><i /><strong>Analysis</strong><span>Risk sections read for the client and shortlisted peers only; quotes verified against the PDF.</span></li>
            <li><i /><strong>You review and export</strong><span>Coverage matrix, gaps and category structures, then an Excel workbook.</span></li>
          </ul>
        </section>
      </div>
    </div>
  </>;
}
