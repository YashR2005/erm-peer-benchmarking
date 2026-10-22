import type { BucketKey, RunState, RunSummary, Setup, StepDetail, StepList } from './types';

// Relative URLs: Vite proxies /api to the Python API in development, and the API serves the
// built frontend in production.
async function call<T>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const { json, ...rest } = init ?? {};
  const response = await fetch(path, {
    ...rest,
    headers: json === undefined ? rest.headers : { 'Content-Type': 'application/json', ...rest.headers },
    body: json === undefined ? rest.body : JSON.stringify(json),
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const data = await response.json();
      if (typeof data?.detail === 'string') detail = data.detail;
    } catch { /* not JSON */ }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

const post = <T>(path: string, json?: unknown) => call<T>(path, { method: 'POST', json: json ?? {} });

export const api = {
  setup: (signal?: AbortSignal) => call<Setup>('/api/setup', { signal }),
  runs: (signal?: AbortSignal) => call<RunSummary[]>('/api/runs', { signal }),
  run: (id: string, signal?: AbortSignal) => call<RunState>(`/api/runs/${id}`, { signal }),

  steps: (id: string, signal?: AbortSignal) => call<StepList>(`/api/runs/${id}/steps`, { signal }),
  step: (id: string, stepId: string, opts: { full?: boolean; raw?: boolean } = {}, signal?: AbortSignal) =>
    call<StepDetail>(`/api/runs/${id}/steps/${encodeURIComponent(stepId)}?full=${!!opts.full}&raw=${!!opts.raw}`, { signal }),

  createRun: (body: { client_name: string; reporting_year: number | null; peer_count: number;
                      market_cap_bucket: BucketKey | null; focus_regions: string[]; notes: string }) =>
    post<RunState>('/api/runs', body),
  startByChat: (message: string) =>
    post<{ run_id: string | null; reply: string | null }>('/api/chat', { message }),
  resume: (id: string) => post<RunState>(`/api/runs/${id}/resume`),
  stop: (id: string) => post<RunState>(`/api/runs/${id}/stop`),
  chat: (id: string, message: string) => post<RunState>(`/api/runs/${id}/chat`, { message }),

  selectPeer: (id: string, peerId: string, selected: boolean) =>
    post<RunState>(`/api/runs/${id}/peers/${peerId}`, { selected }),
  addPeer: (id: string, name: string) => post<RunState>(`/api/runs/${id}/peers`, { name }),
  setBucket: (id: string, bucket: BucketKey | null) =>
    post<RunState>(`/api/runs/${id}/bucket`, { bucket }),
  setPeerCount: (id: string, count: number) =>
    post<RunState>(`/api/runs/${id}/peer-count`, { count }),
  addDocumentUrl: (id: string, companyId: string, url: string) =>
    post<RunState>(`/api/runs/${id}/documents`, { company_id: companyId, url }),
  uploadDocument: (id: string, companyId: string, file: File) => {
    const form = new FormData();
    form.append('company_id', companyId);
    form.append('file', file);
    return call<RunState>(`/api/runs/${id}/documents/upload`, { method: 'POST', body: form });
  },
  markNa: (id: string, docId: string, reason: string) =>
    post<RunState>(`/api/runs/${id}/documents/${docId}/na`, { reason }),
  restoreDocument: (id: string, docId: string) =>
    post<RunState>(`/api/runs/${id}/documents/${docId}/restore`),
  lock: (id: string) => post<RunState>(`/api/runs/${id}/lock`),

  changeTheme: (id: string, key: string, change: { removed?: boolean; name?: string; category?: string }) =>
    call<RunState>(`/api/runs/${id}/themes/${key}`, { method: 'PATCH', json: change }),
  setEvidence: (id: string, riskId: string, rejected: boolean) =>
    post<RunState>(`/api/runs/${id}/evidence/${riskId}`, { rejected }),
  addRemark: (id: string, text: string) => post<RunState>(`/api/runs/${id}/remarks`, { text }),
  exportRun: (id: string) =>
    post<{ file: string; url: string; state: RunState }>(`/api/runs/${id}/export`),
  exportUrl: (id: string, file: string) => `/api/runs/${id}/exports/${encodeURIComponent(file)}`,
};
