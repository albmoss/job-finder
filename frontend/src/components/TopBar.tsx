import React from 'react';
import { CircleHelp } from 'lucide-react';
import { OFFER_TABS, TOOL_VIEWS, VIEW_ICON } from '../views';
import '../styles/topbar.css';

interface TopBarProps {
  activeView: string;
  onSelectView: (view: string) => void;
  /** Widoki z cichą kropką przy ikonie (np. aplikacje czekające na ruch). */
  badges: Record<string, boolean>;
  /** Pasek pipeline'u zajmuje resztę szerokości. */
  pill: React.ReactNode;
  onOpenHelp: () => void;
}

export const TopBar: React.FC<TopBarProps> = ({ activeView, onSelectView, badges, pill, onOpenHelp }) => {
  const tab = (view: string) => {
    const Icon = VIEW_ICON[view];
    const active = view === activeView;
    return (
      <button
        key={view}
        type="button"
        className={`nav-item${active ? ' is-active' : ''}`}
        aria-current={active ? 'page' : undefined}
        aria-label={view}
        data-tip={active ? undefined : view}
        onClick={() => onSelectView(view)}
      >
        <Icon aria-hidden="true" />
        <span className="label">{view}</span>
        {badges[view] && <span className="badge" aria-hidden="true" />}
      </button>
    );
  };

  return (
    <header className="topbar">
      <div className="brand glass">jobfinder</div>
      <nav className="nav glass" aria-label="Kategorie">
        {OFFER_TABS.map(tab)}
        <span className="nav-sep" aria-hidden="true" />
        {TOOL_VIEWS.map(tab)}
      </nav>
      {pill}
      <button
        type="button"
        className="help-btn glass press"
        data-tip="Pomoc i skróty"
        data-kbd="?"
        aria-label="Pomoc i skróty klawiszowe"
        onClick={onOpenHelp}
      >
        <CircleHelp aria-hidden="true" />
      </button>
    </header>
  );
};
