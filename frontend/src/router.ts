import { useSyncExternalStore } from 'react';

export type RouteName =
  | 'dopasowane'
  | 'zapisane'
  | 'aplikacje'
  | 'cv'
  | 'cv-instrukcje'
  | 'cv-nowa'
  | 'cv-wersja'
  | 'postep'
  | 'start';

export interface Route {
  name: RouteName;
  /** Ścieżka bez `#`, np. "/cv/wersja/abc". */
  path: string;
  /** `id` dla `cv-wersja`. */
  params: Record<string, string>;
  query: URLSearchParams;
}

export const DEFAULT_PATH = '/dopasowane';

export const paths = {
  matched: (offerLink?: string) => withOffer('/dopasowane', offerLink),
  saved: (offerLink?: string) => withOffer('/zapisane', offerLink),
  applications: (offerLink?: string) => withOffer('/aplikacje', offerLink),
  cv: '/cv',
  instructions: '/cv/instrukcje',
  tailorNew: (offerLink: string) => withOffer('/cv/nowa', offerLink),
  cvVersion: (id: string) => `/cv/wersja/${encodeURIComponent(id)}`,
  progress: '/postep',
  start: '/start',
};

function withOffer(path: string, offerLink?: string): string {
  return offerLink ? `${path}?${new URLSearchParams({ oferta: offerLink })}` : path;
}

const STATIC: Record<string, RouteName> = {
  '/dopasowane': 'dopasowane',
  '/zapisane': 'zapisane',
  '/aplikacje': 'aplikacje',
  '/cv': 'cv',
  '/cv/instrukcje': 'cv-instrukcje',
  '/cv/nowa': 'cv-nowa',
  '/postep': 'postep',
  '/start': 'start',
};

export function parseRoute(hash: string): Route {
  const raw = hash.replace(/^#/, '') || DEFAULT_PATH;
  const qIdx = raw.indexOf('?');
  const path = (qIdx >= 0 ? raw.slice(0, qIdx) : raw).replace(/\/+$/, '') || DEFAULT_PATH;
  const query = new URLSearchParams(qIdx >= 0 ? raw.slice(qIdx + 1) : '');
  const name = STATIC[path];
  if (name) return { name, path, params: {}, query };
  const version = /^\/cv\/wersja\/([^/]+)$/.exec(path);
  if (version) return { name: 'cv-wersja', path, params: { id: decodeURIComponent(version[1]) }, query };
  return { name: 'dopasowane', path: DEFAULT_PATH, params: {}, query: new URLSearchParams() };
}

let current = parseRoute(window.location.hash);

window.addEventListener('hashchange', () => {
  current = parseRoute(window.location.hash);
  listeners.forEach((fn) => fn());
});

const listeners = new Set<() => void>();

function subscribe(fn: () => void) {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

export function useRoute(): Route {
  return useSyncExternalStore(subscribe, () => current);
}

/** `replace` nie dodaje wpisu do historii (przekierowania, zmiana parametrów listy). */
export function navigate(path: string, opts: { replace?: boolean } = {}) {
  const hash = `#${path}`;
  if (window.location.hash === hash) return;
  if (opts.replace) {
    window.history.replaceState(null, '', hash);
    current = parseRoute(hash);
    listeners.forEach((fn) => fn());
  } else {
    window.location.hash = path;
  }
}
