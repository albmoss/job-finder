import React, { useEffect, useState } from 'react';
import {
  X,
  Globe,
  Database,
  Check,
  AlertTriangle,
  Play,
  RefreshCw,
} from 'lucide-react';
import type { CVInfo, EnvField, PipelinePrerequisites } from '../types';
import { CvEditor, KeysEditor } from './PipelineSetup';
import '../styles/pipeline.css';

export type PipelineMode = 'full' | 'skip_scraping';

export interface PipelineLaunchModalProps {
  isOpen: boolean;
  onClose: () => void;
  prerequisites: PipelinePrerequisites | null;
  cvInfo: CVInfo | null;
  envFields: EnvField[];
  onStartPipeline: (mode: PipelineMode) => void;
  onRefreshPrerequisites: () => void;
  onRefreshCV: () => void;
  onRefreshEnvKeys: () => void;
  showToast: (message: string, action?: { label: string; run: () => void }, done?: boolean) => void;
}

export const PipelineLaunchModal: React.FC<PipelineLaunchModalProps> = ({
  isOpen,
  onClose,
  prerequisites,
  cvInfo,
  envFields,
  onStartPipeline,
  onRefreshPrerequisites,
  onRefreshCV,
  onRefreshEnvKeys,
  showToast,
}) => {
  const [mode, setMode] = useState<PipelineMode>('full');
  const [confirmedCosts, setConfirmedCosts] = useState(false);
  const [activeSetup, setActiveSetup] = useState<'cv' | 'keys' | 'scrapers' | null>(null);

  // Zgoda na koszty resetuje się przy każdym otwarciu okna
  useEffect(() => {
    if (isOpen) {
      setConfirmedCosts(false);
      setActiveSetup(null);
    }
  }, [isOpen]);

  // Obsługa klawisza Esc
  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  // Okno zamyka się tą samą drogą, którą weszło: po `isOpen=false` zostaje jeszcze na
  // czas animacji wyjścia (180 ms), dopiero potem znika z DOM.
  const [present, setPresent] = useState(isOpen);
  const [closing, setClosing] = useState(false);
  useEffect(() => {
    if (isOpen) {
      setPresent(true);
      setClosing(false);
      return;
    }
    setClosing(true);
    const timer = window.setTimeout(() => {
      setPresent(false);
      setClosing(false);
    }, 180);
    return () => window.clearTimeout(timer);
  }, [isOpen]);

  if (!present) return null;

  const isReady = mode === 'skip_scraping' ? prerequisites?.ready_skip : prerequisites?.ready_full;
  const issues = (mode === 'skip_scraping' ? prerequisites?.issues_skip : prerequisites?.issues) ?? [];

  const refreshAll = () => {
    onRefreshPrerequisites();
    onRefreshCV();
    onRefreshEnvKeys();
  };

  const configuredKeysCount = envFields.filter((f) => f.configured).length;
  const dbCount = prerequisites?.db_count ?? 0;

  // Wiersze gotowości
  const cvOk = Boolean(cvInfo?.ready);
  const keysOk = Boolean(prerequisites?.api_ready);
  const scrapersOk = Boolean(prerequisites?.playwright_ready);

  return (
    <div
      className={`modal-backdrop${closing ? ' is-closing' : ''}`}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="launch-modal-title"
    >
      <div className={`pp-modal${closing ? ' is-closing' : ''}`}>
        {/* Nagłówek okna */}
        <div className="pp-modal-head">
          <div>
            <h2 id="launch-modal-title">Uruchom pipeline</h2>
            <p>Postęp zobaczysz na żywo w pasku u góry.</p>
          </div>
          <button
            type="button"
            className="iconbtn"
            onClick={onClose}
            aria-label="Zamknij"
          >
            <X size={16} />
          </button>
        </div>

        {/* Wybór trybu (Run options) */}
        <div className="pp-mode-list" role="radiogroup" aria-label="Tryb przebiegu">
          {/* Pełny przebieg */}
          <div
            className={`pp-mode-card ${mode === 'full' ? 'is-active' : ''}`}
            onClick={() => setMode('full')}
            role="radio"
            aria-checked={mode === 'full'}
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                setMode('full');
              }
            }}
          >
            <div className="pp-mode-card-top">
              <span className="pp-mode-radio">
                {mode === 'full' && <span className="pp-mode-radio-dot" />}
              </span>
              <span className="pp-mode-title">Pełny przebieg</span>
            </div>
            <div className="pp-mode-desc">
              Pobiera oferty ze wszystkich portali, porządkuje bazę i ocenia nowe oferty względem CV.
            </div>
            <div className="pp-mode-meta">
              <Globe size={12} /> scraping + ocena AI
            </div>
          </div>

          {/* Tylko ocena AI */}
          <div
            className={`pp-mode-card ${mode === 'skip_scraping' ? 'is-active' : ''}`}
            onClick={() => setMode('skip_scraping')}
            role="radio"
            aria-checked={mode === 'skip_scraping'}
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                setMode('skip_scraping');
              }
            }}
          >
            <div className="pp-mode-card-top">
              <span className="pp-mode-radio">
                {mode === 'skip_scraping' && <span className="pp-mode-radio-dot" />}
              </span>
              <span className="pp-mode-title">Tylko ocena AI</span>
            </div>
            <div className="pp-mode-desc">
              Bez pobierania. Porządkuje lokalną bazę i ocenia oferty, które czekają na ocenę.
            </div>
            <div className="pp-mode-meta">
              <Database size={12} /> {dbCount.toLocaleString('pl-PL')} ofert w bazie
            </div>
          </div>
        </div>

        {/* Wiersze gotowości (Readiness rows) */}
        <div className="pp-ready-list">
          {/* CV */}
          <div className="pp-ready-row">
            <span className={`pp-ready-icon ${cvOk ? 'is-ok' : 'is-warn'}`}>
              {cvOk ? <Check size={15} /> : <AlertTriangle size={15} />}
            </span>
            <span className="pp-ready-label">CV</span>
            <span className="pp-ready-val">
              {cvOk ? `${cvInfo?.filename} · ${cvInfo?.words} słów` : 'brak pliku CV'}
            </span>
            <button
              type="button"
              className="pp-ready-btn"
              onClick={() => setActiveSetup(activeSetup === 'cv' ? null : 'cv')}
            >
              {activeSetup === 'cv' ? 'Zwiń' : cvOk ? 'Zmień' : 'Dodaj'}
            </button>
          </div>

          {/* Klucze API */}
          <div className="pp-ready-row">
            <span className={`pp-ready-icon ${keysOk ? 'is-ok' : 'is-warn'}`}>
              {keysOk ? <Check size={15} /> : <AlertTriangle size={15} />}
            </span>
            <span className="pp-ready-label">Klucze API</span>
            <span className="pp-ready-val">
              {keysOk
                ? `Gemini gotowy · ${configuredKeysCount} wpisów w .env`
                : 'brak klucza Gemini'}
            </span>
            <button
              type="button"
              className="pp-ready-btn"
              onClick={() => setActiveSetup(activeSetup === 'keys' ? null : 'keys')}
            >
              {activeSetup === 'keys' ? 'Zwiń' : keysOk ? 'Zmień' : 'Skonfiguruj'}
            </button>
          </div>

          {/* Scrapery (wymagane tylko w trybie full) */}
          {mode === 'full' && (
            <div className="pp-ready-row">
              <span className={`pp-ready-icon ${scrapersOk ? 'is-ok' : 'is-warn'}`}>
                {scrapersOk ? <Check size={15} /> : <AlertTriangle size={15} />}
              </span>
              <span className="pp-ready-label">Scrapery</span>
              <span className="pp-ready-val">
                {scrapersOk
                  ? 'Playwright + Chromium'
                  : prerequisites?.playwright_msg || 'brak Chromium'}
              </span>
              <button
                type="button"
                className="pp-ready-btn"
                onClick={() => setActiveSetup(activeSetup === 'scrapers' ? null : 'scrapers')}
              >
                {activeSetup === 'scrapers' ? 'Zwiń' : 'Szczegóły'}
              </button>
            </div>
          )}
        </div>

        {/* Rozwijana sekcja konfiguracji wewnątrz modala */}
        {activeSetup === 'cv' && (
          <div className="pp-setup-drawer">
            <div className="pp-setup-head">
              <span>Edycja życiorysu (CV)</span>
            </div>
            <CvEditor cvInfo={cvInfo} onSaved={refreshAll} showToast={showToast} />
          </div>
        )}

        {activeSetup === 'keys' && (
          <div className="pp-setup-drawer">
            <div className="pp-setup-head">
              <span>Konfiguracja kluczy API</span>
            </div>
            <KeysEditor envFields={envFields} full onSaved={refreshAll} showToast={showToast} />
          </div>
        )}

        {activeSetup === 'scrapers' && mode === 'full' && (
          <div className="pp-setup-drawer">
            <div className="pp-setup-head">
              <span>Wymagania scraperów</span>
            </div>
            <div style={{ fontSize: 13, color: 'var(--ink-2)', lineHeight: 1.5 }}>
              {scrapersOk ? (
                <p style={{ margin: 0 }}>
                  Playwright + Chromium są gotowe. Scrapery portali pobiorą aktualne ogłoszenia.
                </p>
              ) : (
                <div>
                  <p style={{ margin: '0 0 6px 0', color: 'var(--ink)' }}>
                    {prerequisites?.playwright_msg || 'Chromium nie jest zainstalowane.'}
                  </p>
                  <p style={{ margin: 0, fontSize: 12, color: 'var(--ink-3)' }}>
                    Zainstaluj Chromium poleceniem <code style={{ fontFamily: 'var(--mono)' }}>playwright install chromium</code> albo przełącz na tryb „Tylko ocena AI”.
                  </p>
                </div>
              )}
            </div>
            <div style={{ marginTop: 6 }}>
              <button
                type="button"
                className="btn btn-secondary"
                style={{ height: 32, fontSize: 12 }}
                onClick={() => {
                  onRefreshPrerequisites();
                  showToast('Sprawdzono wymagania ponownie.');
                }}
              >
                <RefreshCw size={13} /> Sprawdź ponownie
              </button>
            </div>
          </div>
        )}

        {/* Ostrzeżenia, jeśli brakuje wymagań */}
        {!isReady && issues.length > 0 && (
          <div
            style={{
              padding: '12px 14px',
              borderRadius: 14,
              background: 'rgba(247, 183, 49, 0.08)',
              border: '1px solid rgba(247, 183, 49, 0.25)',
              fontSize: 12.5,
              color: 'var(--ink)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontWeight: 600, color: 'var(--ink)', marginBottom: 4 }}>
              <AlertTriangle size={14} /> Najpierw uzupełnij
            </div>
            <ul style={{ margin: 0, paddingLeft: 18, color: 'var(--ink-2)', lineHeight: 1.5 }}>
              {issues.map((issue, idx) => (
                <li key={idx}>{issue}</li>
              ))}
            </ul>
          </div>
        )}

        {/* Zgoda na koszty API */}
        <label className={`pp-consent ${confirmedCosts ? 'is-checked' : ''}`}>
          <input
            type="checkbox"
            checked={confirmedCosts}
            onChange={(e) => setConfirmedCosts(e.target.checked)}
          />
          <span className="pp-checkbox">
            {confirmedCosts && <Check size={12} />}
          </span>
          <span className="pp-consent-text">
            <strong>Rozumiem koszty API</strong>
            <span>
              Ocena wysyła oferty do skonfigurowanego modelu AI. Wywołania mogą być płatne według cennika Twojego konta u dostawcy.
            </span>
          </span>
        </label>

        {/* Stopka okna */}
        <div className="pp-modal-foot">
          <span className="pp-modal-hint">
            {!isReady
              ? 'brakuje wymagań'
              : !confirmedCosts
              ? 'zaznacz zgodę na koszty'
              : ''}
          </span>

          <button type="button" className="btn btn-quiet" onClick={onClose}>
            Anuluj
          </button>

          <button
            type="button"
            className="btn btn-primary"
            disabled={!isReady || !confirmedCosts}
            onClick={() => {
              onStartPipeline(mode);
              onClose();
            }}
          >
            <Play size={14} fill="currentColor" /> Uruchom
          </button>
        </div>
      </div>
    </div>
  );
};
