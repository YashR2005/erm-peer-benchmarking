import { useCallback, useEffect, useState } from 'react';
import { useToastError } from '../components/Toast';

// Callers provide a stable loader. Aborting prevents stale results on navigation.
export function useRemoteData<T>(load: (signal: AbortSignal) => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState('');
  useToastError(error);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const reload = useCallback(() => setRevision(value => value + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setLoading(true);
    setError('');
    load(controller.signal).then(result => { if (active) setData(result); })
      .catch(reason => { if (active && !controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Could not load data.'); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; controller.abort(); };
  }, [load, revision]);

  return { data, error, loading, reload, setData };
}
