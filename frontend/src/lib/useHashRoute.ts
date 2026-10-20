import { useCallback, useEffect, useState } from 'react';

// Routes live in the URL hash (#/runs/<id>, #/new, #/method) so a refresh keeps your place.
export type Route =
  | { page: 'overview' } | { page: 'benchmarks' } | { page: 'new' } | { page: 'method' }
  | { page: 'run'; id: string };

function parse(hash: string): Route {
  const parts = hash.replace(/^#\/?/, '').split('/').filter(Boolean);
  if (parts[0] === 'runs' && parts[1]) return { page: 'run', id: decodeURIComponent(parts[1]) };
  if (parts[0] === 'new') return { page: 'new' };
  if (parts[0] === 'method') return { page: 'method' };
  if (parts[0] === 'benchmarks') return { page: 'benchmarks' };
  return { page: 'overview' };
}

export function href(route: Route): string {
  return route.page === 'run' ? `#/runs/${encodeURIComponent(route.id)}`
    : route.page === 'overview' ? '#/' : `#/${route.page}`;
}

export function useHashRoute() {
  const [route, setRoute] = useState<Route>(() => parse(window.location.hash));
  useEffect(() => {
    const update = () => { setRoute(parse(window.location.hash)); window.scrollTo(0, 0); };
    window.addEventListener('hashchange', update);
    return () => window.removeEventListener('hashchange', update);
  }, []);
  const navigate = useCallback((next: Route) => { window.location.hash = href(next); }, []);
  return { route, navigate };
}
