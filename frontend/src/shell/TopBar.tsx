import { useLayoutEffect, useRef, useState, type RefObject } from 'react';
import { Bookmark, FileUser, RefreshCw, Send, Sparkles, type LucideIcon } from 'lucide-react';
import { useApp } from '../app_context';
import { paths, useRoute, type RouteName } from '../router';
import { CandidateMenu } from './CandidateMenu';
import { LogoMark } from './LogoMark';
import { RunPill } from './RunPill';

const NAV: { label: string; icon: LucideIcon; path: string; match: RouteName[] }[] = [
  { label: 'Dopasowane', icon: Sparkles, path: paths.matched(), match: ['dopasowane'] },
  { label: 'Zapisane', icon: Bookmark, path: paths.saved(), match: ['zapisane'] },
  { label: 'Aplikacje', icon: Send, path: paths.applications(), match: ['aplikacje'] },
  { label: 'Moje CV', icon: FileUser, path: paths.cv, match: ['cv', 'cv-instrukcje', 'cv-nowa', 'cv-wersja'] },
];

function useActivePill(navRef: RefObject<HTMLElement | null>, activeIndex: number) {
  const [pill, setPill] = useState<{ x: number; width: number } | null>(null);
  const [ready, setReady] = useState(false);

  useLayoutEffect(() => {
    const nav = navRef.current;
    if (!nav) return;
    const measure = () => {
      const tab = nav.querySelectorAll<HTMLElement>('.tb-tab')[activeIndex];
      if (!tab) return setPill(null);
      const box = tab.getBoundingClientRect();
      setPill({ x: box.left - nav.getBoundingClientRect().left, width: box.width });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(nav);
    return () => observer.disconnect();
  }, [navRef, activeIndex]);

  useLayoutEffect(() => {
    if (!pill || ready) return;
    const frame = requestAnimationFrame(() => setReady(true));
    return () => cancelAnimationFrame(frame);
  }, [pill, ready]);

  return { pill, ready };
}

export function TopBar({ minimal, running }: { minimal: boolean; running: boolean }) {
  const route = useRoute();
  const { openLaunch } = useApp();
  const showPill = !minimal && running && route.name !== 'postep';

  return (
    <header className={showPill ? 'tb is-running' : 'tb'}>
      <a className="tb-brand" href={`#${paths.matched()}`} aria-label="jobfinder">
        <LogoMark />
        <span className="tb-wordmark">jobfinder</span>
      </a>
      {!minimal && <TopNav route={route.name} />}
      <div className="tb-spacer" />
      {showPill && <RunPill />}
      {!minimal && !running && (
        <div className="tb-actions glass">
          <button type="button" className="tb-action" onClick={openLaunch}>
            <RefreshCw />
            Szukaj nowych ofert
          </button>
        </div>
      )}
      {(showPill || (!minimal && !running)) && <span className="tb-divider" aria-hidden="true" />}
      <CandidateMenu />
    </header>
  );
}

function TopNav({ route }: { route: RouteName }) {
  const navRef = useRef<HTMLElement>(null);
  const activeIndex = NAV.findIndex(({ match }) => match.includes(route));
  const { pill, ready } = useActivePill(navRef, activeIndex);

  return (
    <nav ref={navRef} className={`tb-nav glass${ready ? ' is-ready' : ''}`} aria-label="Kategorie">
      {pill && (
        <span
          className="tb-pill"
          aria-hidden="true"
          style={{ transform: `translateX(${pill.x}px)`, width: pill.width }}
        />
      )}
      {NAV.map(({ label, icon: Icon, path }, index) => {
        const active = index === activeIndex;
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
  );
}
