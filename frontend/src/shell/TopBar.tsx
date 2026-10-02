import { Bookmark, FileUser, RefreshCw, Send, Settings2, Sparkles, type LucideIcon } from 'lucide-react';
import { useApp } from '../app_context';
import { paths, useRoute, type RouteName } from '../router';

export function LogoMark({ size = 28 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <path
        d="M3 8.5V6A3 3 0 0 1 6 3H8.5M15.5 3H18A3 3 0 0 1 21 6V8.5M21 15.5V18A3 3 0 0 1 18 21H15.5M8.5 21H6A3 3 0 0 1 3 18V15.5"
        strokeWidth={2.2}
      />
      <path d="M8.5 10.5H15.5" strokeWidth={2.4} />
      <path d="M8.5 14.25H15.5" stroke="var(--track)" strokeWidth={1.6} />
      <path d="M8.5 14.25H13.25" strokeWidth={1.6} />
    </svg>
  );
}

const NAV: { label: string; icon: LucideIcon; path: string; match: RouteName[] }[] = [
  { label: 'Dopasowane', icon: Sparkles, path: paths.matched(), match: ['dopasowane'] },
  { label: 'Zapisane', icon: Bookmark, path: paths.saved(), match: ['zapisane'] },
  { label: 'Aplikacje', icon: Send, path: paths.applications(), match: ['aplikacje'] },
  { label: 'Moje CV', icon: FileUser, path: paths.cv, match: ['cv', 'cv-instrukcje', 'cv-nowa', 'cv-wersja'] },
];

/** `minimal`: tylko logo i Ustawienia (ekrany 07 i 10). */
export function TopBar({ minimal }: { minimal: boolean }) {
  const route = useRoute();
  const { openLaunch, openSettings } = useApp();

  return (
    <header className="tb">
      <a className="tb-brand" href={`#${paths.matched()}`} aria-label="jobfinder">
        <LogoMark />
        <span className="tb-wordmark">jobfinder</span>
      </a>
      {!minimal && (
        <nav className="tb-nav glass" aria-label="Kategorie">
          {NAV.map(({ label, icon: Icon, path, match }) => {
            const active = match.includes(route.name);
            return (
              <a
                key={path}
                href={`#${path}`}
                className={`tb-tab${active ? ' is-active' : ''}`}
                aria-current={active ? 'page' : undefined}
              >
                <Icon />
                {label}
              </a>
            );
          })}
        </nav>
      )}
      <div className="tb-spacer" />
      {!minimal && (
        <button type="button" className="btn" onClick={openLaunch}>
          <RefreshCw />
          Szukaj nowych ofert
        </button>
      )}
      <button type="button" className="btn" onClick={openSettings}>
        <Settings2 />
        Ustawienia
      </button>
    </header>
  );
}
