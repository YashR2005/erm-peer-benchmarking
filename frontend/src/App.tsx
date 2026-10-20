import { useCallback, useEffect } from 'react';
import { api } from './api';
import { Sidebar } from './components/Sidebar';
import { ToastProvider } from './components/Toast';
import { useHashRoute } from './lib/useHashRoute';
import { useRemoteData } from './lib/useRemoteData';
import { BenchmarksPage } from './pages/BenchmarksPage';
import { MethodPage } from './pages/MethodPage';
import { NewBenchmark } from './pages/NewBenchmark';
import { Overview } from './pages/Overview';
import { RunPage } from './pages/RunPage';

export default function App() {
  return <ToastProvider><Shell /></ToastProvider>;
}

function Shell() {
  const { route, navigate } = useHashRoute();
  const setup = useRemoteData(api.setup);
  const loadRuns = useCallback((signal: AbortSignal) => api.runs(signal), []);
  const runs = useRemoteData(loadRuns);
  const { reload } = runs;
  useEffect(() => { if (route.page !== 'run') reload(); }, [route.page, reload]);
  const awaiting = (runs.data ?? []).filter(r => r.status === 'awaiting_scope' || r.status === 'awaiting_findings').length;

  return <div className="app-shell">
    <Sidebar route={route} navigate={navigate} setup={setup.data} awaiting={awaiting} />
    <div className="main-shell">
      <main>
        {route.page === 'overview' && <Overview runs={runs.data} loading={runs.loading} navigate={navigate} />}
        {route.page === 'benchmarks' && <BenchmarksPage runs={runs.data} loading={runs.loading} navigate={navigate} />}
        {route.page === 'new' && <NewBenchmark setup={setup.data} navigate={navigate} />}
        {route.page === 'method' && <MethodPage setup={setup.data} />}
        {route.page === 'run' && <RunPage key={route.id} runId={route.id} setup={setup.data} navigate={navigate} onChanged={reload} />}
      </main>
    </div>
  </div>;
}
