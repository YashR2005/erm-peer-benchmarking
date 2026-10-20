import { LoaderCircle, SendHorizontal } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type { FormEvent, KeyboardEvent } from 'react';
import { api } from '../../api';
import type { RunActions } from '../../pages/RunPage';

const suggestions: Record<string, string[]> = {
  awaiting_scope: ['Only large caps', 'Use 4 peers', 'Add UOL Group', 'go'],
  awaiting_findings: ['Show evidence for interest rates', 'Remove the unmapped theme', 'export'],
  exported: ['Show evidence for interest rates', 'export'],
};

const intro: Record<string, string> = {
  awaiting_scope: 'Tell me what to change: drop or add peers, change the market-cap bucket, or fix a source. Say "go" to lock the scope and run the analysis.',
  awaiting_findings: 'Ask me to remove, rename or move a theme, reject a quote by its id (e.g. R-P1-3), add a remark on the category structures, or "export".',
  exported: 'You can still change themes and evidence; export again afterwards.',
};

// Chat over the fixed steps: every message becomes an action on the current stage.
export function Assistant({ state, busy, perform }: RunActions) {
  const [text, setText] = useState('');
  const end = useRef<HTMLDivElement>(null);
  const status = state.run.status;
  const thinking = !!state.job?.running && state.job.kind === 'chat';
  const canChat = ['awaiting_scope', 'awaiting_findings', 'exported'].includes(status);

  useEffect(() => { end.current?.scrollIntoView({ block: 'end' }); }, [state.chat.length, thinking]);

  async function send(message: string) {
    if (!message.trim() || busy || !canChat) return;
    if (await perform(() => api.chat(state.run.run_id, message.trim()))) setText('');
  }
  const submit = (e: FormEvent) => { e.preventDefault(); void send(text); };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send(text); }
  };

  return <aside className="panel assistant" aria-label="Assistant">
    <div className="assistant-head">
      <img src="/marsh-mark.png" alt="" />
      <div><strong>Assistant</strong><small>Chat over the fixed steps</small></div>
    </div>
    <div className="messages">
      {intro[status] && <div className="msg system">{intro[status]}</div>}
      {state.chat.map((m, i) => <div key={i} className={`msg ${m.role}`}>{m.text.replace(/\*\*/g, '')}</div>)}
      {thinking && <div className="msg assistant"><LoaderCircle size={14} className="spin" /> Working…</div>}
      {!canChat && !thinking && <div className="msg system">The assistant is available at each checkpoint.</div>}
      <div ref={end} />
    </div>
    {canChat && <div className="suggestions">
      {(suggestions[status] ?? []).map(s => <button key={s} disabled={busy} onClick={() => void send(s)}>{s}</button>)}
    </div>}
    <form className="composer" onSubmit={submit}>
      <textarea aria-label="Message the assistant" rows={1} value={text} onChange={e => setText(e.target.value)} onKeyDown={onKey}
        placeholder={canChat ? 'Ask for a change…' : 'Waiting for the current step…'} disabled={!canChat} />
      <button className="btn primary" type="submit" aria-label="Send" disabled={busy || !canChat || !text.trim()}><SendHorizontal size={17} /></button>
    </form>
  </aside>;
}
