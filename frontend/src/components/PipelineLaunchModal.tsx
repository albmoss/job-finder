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
import type { CVInfo, EnvKeysResponse, PipelinePrerequisites } from '../types';
import { CvEditor, JevKeyEditor, KeysEditor } from './PipelineSetup';
import '../styles/pipeline.css';

export type PipelineMode = 'full' | 'skip_scraping';

export interface PipelineLaunchModalProps {
  isOpen: boolean;
  onClose: () => void;
  prerequisites: PipelinePrerequisites | null;
  cvInfo: CVInfo | null;
  envKeys: EnvKeysResponse | null;
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
  envKeys,
  onStartPipeline,
  onRefreshPrerequisites,
  onRefreshCV,
  onRefreshEnvKeys,
  showToast,
}) => {
  const [mode, setMode] = useState<PipelineMode>('full');
  const [confirmedCosts, setConfirmedCosts] = useState(false);
  const [activeSetup, setActiveSetup] = useState<'cv' | 'jev' | 'llm' | 'scrapers' | null>(null);

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

  const llm = envKeys?.api_info;
  const llmLabel = llm?.providers.find((p) => p.id === llm.provider)?.label ?? 'model';
  const dbCount = prerequisites?.db_count ?? 0;

  // Wiersze gotowości
  const cvOk = Boolean(cvInfo?.ready);
  const profile = cvInfo?.profile;
  // Profil z CV wyznacza zakres: miasto + oferty zdalne, poziom z CV ± sąsiedni.
  const cvVal = !cvOk
    ? 'brak pliku CV'
    : profile?.current
      ? `${cvInfo?.filename} · ${[profile.seniority, profile.city ? `${profile.city} i zdalnie` : null].filter(Boolean).join(', ')}`
      : `${cvInfo?.filename} · profil przeliczy się przy starcie`;
  const jevOk = Boolean(prerequisites?.jev_ready);
  const llmOk = Boolean(prerequisites?.api_ready);
  // Model czyta CV tylko wtedy, gdy profil trzeba policzyć od nowa.
  const llmNeeded = prerequisites?.llm_needed ?? true;
  const llmVal = llmOk && llm
    ? `${llmLabel} · ${llm.models[0]}`
    : llmNeeded ? llm?.error || 'brak klucza API' : 'nieużywany, profil jest aktualny';
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
              Pobiera oferty z portali w zakresie z CV, porządkuje bazę i ocenia nowe oferty.
            </div>
            <div className="pp-mode-meta">
              <Globe size={12} /> scraping + przesiew + Jev
            </div>
          </div>

          {/* Tylko ocena */}
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
              <span className="pp-mode-title">Tylko ocena</span>
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
            <span className="pp-ready-val" title={cvVal}>{cvVal}</span>
            <button
              type="button"
              className="pp-ready-btn"
              onClick={() => setActiveSetup(activeSetup === 'cv' ? null : 'cv')}
            >
              {activeSetup === 'cv' ? 'Zwiń' : cvOk ? 'Zmień' : 'Dodaj'}
            </button>
          </div>

          {/* Jev: ocena ofert */}
          <div className="pp-ready-row">
            <span className={`pp-ready-icon ${jevOk ? 'is-ok' : 'is-warn'}`}>
              {jevOk ? <Check size={15} /> : <AlertTriangle size={15} />}
            </span>
            <span className="pp-ready-label">Ocena ofert</span>
            <span className="pp-ready-val">
              {jevOk ? 'Jev (TypeSafe) · klucz ustawiony' : 'brak klucza TYPESAFE_API_KEY'}
            </span>
            <button
              type="button"
              className="pp-ready-btn"
              onClick={() => setActiveSetup(activeSetup === 'jev' ? null : 'jev')}
            >
              {activeSetup === 'jev' ? 'Zwiń' : jevOk ? 'Zmień' : 'Dodaj'}
            </button>
          </div>

          {/* Model czytający CV */}
          <div className="pp-ready-row">
            <span className={`pp-ready-icon ${llmOk || !llmNeeded ? 'is-ok' : 'is-warn'}`}>
              {llmOk || !llmNeeded ? <Check size={15} /> : <AlertTriangle size={15} />}
            </span>
            <span className="pp-ready-label">Czytanie CV</span>
            <span className="pp-ready-val" title={llmVal}>{llmVal}</span>
            <button
              type="button"
              className="pp-ready-btn"
              onClick={() => setActiveSetup(activeSetup === 'llm' ? null : 'llm')}
            >
              {activeSetup === 'llm' ? 'Zwiń' : llmOk ? 'Zmień' : 'Skonfiguruj'}
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

        {activeSetup === 'jev' && (
          <div className="pp-setup-drawer">
            <div className="pp-setup-head">
              <span>Jev: ocena ofert</span>
            </div>
            <JevKeyEditor envKeys={envKeys} onSaved={refreshAll} showToast={showToast} />
          </div>
        )}

        {activeSetup === 'llm' && (
          <div className="pp-setup-drawer">
            <div className="pp-setup-head">
              <span>Model czytający CV i pozostałe klucze</span>
            </div>
            <KeysEditor envKeys={envKeys} full onSaved={refreshAll} showToast={showToast} />
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
                    Zainstaluj Chromium poleceniem <code style={{ fontFamily: 'var(--mono)' }}>playwright install chromium</code> albo przełącz na tryb „Tylko ocena”.
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
          <div className="pp-issues">
            <div className="pp-issues-title">
              <AlertTriangle size={14} /> Najpierw uzupełnij
            </div>
            <ul>
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
              Jev ocenia każdą nową ofertę. Model z wiersza „Czytanie CV” dostaje CV tylko wtedy, gdy
              trzeba policzyć profil. Oba wywołania mogą być płatne według cennika Twoich kont.
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
