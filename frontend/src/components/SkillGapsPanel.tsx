import React, { useState, useEffect, useMemo } from 'react';
import type { SkillGapsResponse, SkillGapRow } from '../types';
import { api } from '../api';
import { SWAP_CLASS, useBlurSwap } from '../swap';
import { Puzzle, Info, CircleDashed, ChevronLeft, ChevronRight, ArrowRight } from 'lucide-react';
import { OFFERS, plural } from '../plural';
import '../styles/tools.css';

const PAGE_SIZE = 10;
const TICKS = [30, 40, 50, 60, 70, 80];

function offersCountLabel(n: number): string {
  if (n === 1) return 'w 1 ofercie';
  return `w ${n} ofertach`;
}

interface SkillGapsPanelProps {
  /** Dane wczytane — podmiana widoku (App.navigate) może wyostrzyć panel. */
  onReady: () => void;
  /** Lista ofert z tym brakiem przy tym samym progu (App → Wszystkie z filtrem). */
  onShowOffers: (skill: string, threshold: number) => void;
}

export const SkillGapsPanel: React.FC<SkillGapsPanelProps> = ({ onReady, onShowOffers }) => {
  const [threshold, setThreshold] = useState<number>(50);
  const [data, setData] = useState<SkillGapsResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [currentPage, setCurrentPage] = useState<number>(1);
  // Strona tabeli zmienia się pod rozmyciem, jak lista ofert (dane są już w pamięci).
  const pageSwap = useBlurSwap(false);

  useEffect(() => {
    if (!loading) onReady();
  }, [loading, onReady]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setCurrentPage(1);

    api
      .getSkillGaps(threshold)
      .then((res) => {
        if (active) {
          setData(res);
          setLoading(false);
        }
      })
      .catch((err) => {
        console.error('Błąd pobierania brakujących umiejętności:', err);
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [threshold]);

  const rows: SkillGapRow[] = data?.rows ?? [];
  const considered = data?.considered ?? 0;
  const skipped = data?.skipped ?? 0;
  const totalGaps = data?.total_gaps ?? 0;

  // Najbliżej celu: 4 braki o najwyższym średnim dopasowaniu
  const closestRows = useMemo(() => {
    return [...rows]
      .sort((a, b) => b.mean_match - a.mean_match)
      .slice(0, 4);
  }, [rows]);

  // Paginacja
  const totalPages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const pageSafe = Math.min(currentPage, totalPages);
  const pageRows = rows.slice((pageSafe - 1) * PAGE_SIZE, pageSafe * PAGE_SIZE);
  const startItem = rows.length === 0 ? 0 : (pageSafe - 1) * PAGE_SIZE + 1;
  const endItem = Math.min(pageSafe * PAGE_SIZE, rows.length);
  const totalForRange = totalGaps > 0 ? totalGaps.toLocaleString('pl-PL') : rows.length.toString();

  // Najczęstszy brak nad progiem (hero)
  const topGap = rows[0];

  // Procent wypełnienia suwaka
  const fillPct = `${((threshold - 30) / (80 - 30)) * 100}%`;

  return (
    <>
      {/* Lewy panel sterowania */}
      <section className="panel glass sg-left" aria-label="Parametry braków">
        <div className="panel-scroll sg-left-scroll">
          {/* Nagłówek */}
          <div className="panel-head">
            <div>
              <h1>
                Czego brakuje
                <Puzzle aria-hidden="true" />
              </h1>
              <p>braki w ofertach, które poza tym pasowały</p>
            </div>
          </div>

          {/* Pytanie w stylu Reason */}
          <div className="sg-question">
            Ranking mówi, w co aplikować dziś. Tu jest inne pytanie:{' '}
            <em>czego się nauczyć, żeby ranking miał z czego wybierać</em>.
          </div>

          {/* Suwak progu */}
          <div className="sg-range-wrap">
            <div className="sg-range-head">
              <label htmlFor="gap-threshold" className="sg-range-label">
                Próg dopasowania
              </label>
              <span className="sg-range-val mono tnum">{threshold}%</span>
            </div>
            <input
              id="gap-threshold"
              type="range"
              min={30}
              max={80}
              step={5}
              value={threshold}
              onChange={(e) => setThreshold(parseInt(e.target.value, 10))}
              className="range-slider"
              style={{ '--fill-pct': fillPct } as React.CSSProperties}
              aria-label="Próg dopasowania"
            />
            <div className="range-ticks" aria-hidden="true">
              {TICKS.map((t) => (
                <span key={t} className={`range-tick ${t === threshold ? 'is-active' : ''}`}>
                  {t}
                </span>
              ))}
            </div>
          </div>

          {/* Statystyki */}
          <div className="sg-stats">
            <div className={`stat ${considered === 0 ? 'is-zero' : ''}`}>
              <b>{considered.toLocaleString('pl-PL')}</b>
              <span>ofert nad progiem</span>
            </div>
            <div className={`stat ${skipped === 0 ? 'is-zero' : ''}`}>
              <b>{skipped.toLocaleString('pl-PL')}</b>
              <span>pominiętych jako odrzucone</span>
            </div>
            <div className={`stat ${totalGaps === 0 ? 'is-zero' : ''}`}>
              <b>{totalGaps.toLocaleString('pl-PL')}</b>
              <span>różnych braków</span>
            </div>
          </div>

          {/* Najbliżej celu */}
          <div className="sg-closest">
            <div className="section-label">
              <span>Najbliżej celu</span>
              <span className="aside">najwyższe śr. dop.</span>
            </div>
            {closestRows.length > 0 ? (
              <div className="chips">
                {closestRows.map((r) => (
                  <span key={r.skill} className="chip gap" title={`Średnie dopasowanie: ${Math.round(r.mean_match)}%`}>
                    <CircleDashed size={13} aria-hidden="true" />
                    <span>{r.skill}</span>
                    <span className="sg-chip-pct mono tnum">
                      {Math.round(r.mean_match)}%
                    </span>
                  </span>
                ))}
              </div>
            ) : (
              <span style={{ fontSize: '12.5px', color: 'var(--ink-3)' }}>
                Brak danych dla tego progu
              </span>
            )}
          </div>

          {/* Notatka na dole */}
          <div className="sg-note">
            <Info size={15} className="sg-note-icon" aria-hidden="true" />
            <p className="sg-note-text">
              Bez progu na czoło wychodzą „wykształcenie medyczne” i „uprawnienia SEP” — braki prawdziwe, tylko względem
              ofert, których i tak nie tkniesz. Odrzucone przez Ciebie oferty nie wchodzą do rachunku: tę drogę już
              świadomie odrzuciłeś.
            </p>
          </div>
        </div>
      </section>

      {/* Prawy panel z listą braków */}
      <section className="panel glass sg-right" aria-label="Brakujące umiejętności">
        {loading && rows.length === 0 ? (
          <div className="sg-empty-state empty-state">
            <div className="text">
              <strong>Przeliczam braki…</strong>
            </div>
          </div>
        ) : rows.length === 0 ? (
          <div className="sg-empty-state empty-state">
            <div className="text">
              <strong>Nic nad tym progiem</strong>
              <span>Obniż próg albo poczekaj, aż pipeline oceni świeże oferty.</span>
            </div>
          </div>
        ) : (
          <>
            {/* Hero z najczęstszym brakiem */}
            {topGap && (
              <header className="sg-hero">
                <span className="sg-hero-eyebrow mono">
                  01  ·  najczęstszy brak nad progiem {threshold}%
                </span>
                <h2 className="sg-hero-title">{topGap.skill}</h2>
                <p className="sg-hero-line">
                  brakuje {offersCountLabel(topGap.offers)}  ·  średnio pasowały w {Math.round(topGap.mean_match)}%
                </p>
                <button
                  type="button"
                  className="btn btn-secondary press sg-hero-action"
                  onClick={() => onShowOffers(topGap.skill, threshold)}
                >
                  Pokaż {topGap.offers.toLocaleString('pl-PL')} {plural(topGap.offers, OFFERS)}
                  <ArrowRight size={15} aria-hidden="true" />
                </button>
              </header>
            )}

            {/* Tabela braków z przygaszaniem podczas ładowania */}
            <div
              className="sg-table-wrap"
              style={{
                opacity: loading ? 0.55 : 1,
                transition: 'opacity 200ms ease',
              }}
            >
              <div className="sg-table-head">
                <span className="sg-col-rank">#</span>
                <span className="sg-col-skill">umiejętność</span>
                <span className="sg-col-track">udział</span>
                <span className="sg-col-count">ofert</span>
                <span className="sg-col-avg">śr. dop.</span>
              </div>

              <div className={`sg-table-body panel-scroll${SWAP_CLASS[pageSwap.phase]}`}>
                {pageRows.map((row, idx) => {
                  const globalRank = (pageSafe - 1) * PAGE_SIZE + idx + 1;
                  const isTopRank = globalRank === 1;
                  return (
                    <div
                      key={row.skill}
                      className={`sg-row ${isTopRank ? 'is-first' : ''}`}
                    >
                      <span className="sg-col-rank mono">
                        {String(globalRank).padStart(2, '0')}
                      </span>
                      <span className="sg-col-skill" title={row.skill}>
                        {row.skill}
                      </span>
                      <span className="sg-col-track">
                        <span className="sg-track-bg">
                          <i
                            className={`sg-bar ${isTopRank ? 'is-ink' : ''}`}
                            style={{ width: `${Math.max(4, Math.min(100, row.width_pct))}%` }}
                          />
                        </span>
                      </span>
                      <span className="sg-col-count mono tnum">{row.offers}</span>
                      <span className="sg-col-avg mono tnum">{Math.round(row.mean_match)}%</span>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Pager listy */}
            <footer className="sg-pager">
              <span className={`sg-pager-range mono${SWAP_CLASS[pageSwap.phase]}`}>
                {startItem}–{endItem} z {totalForRange}
              </span>
              <div className="sg-pager-actions">
                <button
                  type="button"
                  className="iconbtn press"
                  disabled={pageSafe <= 1}
                  onClick={() => pageSwap.run(() => setCurrentPage((p) => Math.max(1, p - 1)))}
                  data-tip="Poprzednia strona"
                  aria-label="Poprzednia strona"
                >
                  <ChevronLeft size={16} />
                </button>
                <button
                  type="button"
                  className="iconbtn press"
                  disabled={pageSafe >= totalPages}
                  onClick={() => pageSwap.run(() => setCurrentPage((p) => Math.min(totalPages, p + 1)))}
                  data-tip="Następna strona"
                  aria-label="Następna strona"
                >
                  <ChevronRight size={16} />
                </button>
              </div>
            </footer>
          </>
        )}
      </section>
    </>
  );
};
