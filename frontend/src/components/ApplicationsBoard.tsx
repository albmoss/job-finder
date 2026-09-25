import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ArrowDown, Send } from 'lucide-react';
import { api } from '../api';
import type { ApplicationItem, ApplicationsResponse, NextStep } from '../types';
import '../styles/applications.css';

export interface ApplicationsBoardProps {
  refreshKey: number;
  onUpdateDecision: (
    link: string,
    status: string,
    rating: number | null,
    stage?: string,
  ) => void | Promise<void>;
  onOpenOffer: (link: string) => void;
  /** Dane wczytane — podmiana widoku (App.navigate) może wyostrzyć tablicę. */
  onReady: () => void;
}

interface ColumnDef {
  key: string;
  name: string;
  verb: string;
  isArchive?: boolean;
}

const COLUMNS: ColumnDef[] = [
  { key: 'save', name: 'Zapisane', verb: 'zapisane' },
  { key: 'apply', name: 'Wysłane', verb: 'wysłane' },
  { key: 'interview', name: 'Rozmowa', verb: 'rozmowa' },
  { key: 'offer', name: 'Oferta', verb: 'oferta' },
  { key: 'archive', name: 'Archiwum', verb: 'archiwum', isArchive: true },
];

const INITIAL_VISIBLE_COUNT = 4;

function formatStageDate(verb: string, rawDate?: string | null): string {
  if (!rawDate) return verb;
  const datePart = rawDate.split(' ')[0] || '';
  const parts = datePart.split('-');
  if (parts.length === 3) {
    return `${verb} ${parts[2]}.${parts[1]}`;
  }
  return verb;
}

function formatAge(days: number | null): string {
  if (days === null) return '';
  if (days <= 0) return 'dziś';
  if (days === 1) return '1 dzień';
  return `${days} dni`;
}

const WEEKDAYS = ['nd', 'pn', 'wt', 'śr', 'czw', 'pt', 'sb'];

/** "Rozmowa z HR · pt 26.09, 10:00"; `overdue`, gdy termin już minął. */
function formatNextStep(step: NextStep): { text: string; overdue: boolean } {
  if (!step.due) return { text: step.label, overdue: false };
  const [day, time] = step.due.split(' ');
  const [y, m, d] = day.split('-').map(Number);
  const [hh, mm] = (time ?? '23:59').split(':').map(Number);
  const at = new Date(y, m - 1, d, hh, mm);
  const when = `${WEEKDAYS[at.getDay()]} ${day.slice(8, 10)}.${day.slice(5, 7)}${time ? `, ${time}` : ''}`;
  return { text: `${step.label} · ${when}`, overdue: at.getTime() < Date.now() };
}

const NextStepLine: React.FC<{ step: NextStep | null }> = ({ step }) => {
  if (!step) return null;
  const { text, overdue } = formatNextStep(step);
  return (
    <p className={`ab-card-next ${overdue ? 'is-overdue' : ''}`} title={overdue ? 'Termin minął' : undefined}>
      {text}
    </p>
  );
};

interface DragState {
  item: ApplicationItem;
  originStage: string;
  targetStage: string | null;
  x: number;
  y: number;
  offsetX: number;
  offsetY: number;
  width: number;
  height: number;
}

export const ApplicationsBoard: React.FC<ApplicationsBoardProps> = ({
  refreshKey,
  onUpdateDecision,
  onOpenOffer,
  onReady,
}) => {
  const [data, setData] = useState<ApplicationsResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [expandedCols, setExpandedCols] = useState<Record<string, boolean>>({});

  // Drag & drop
  const [drag, setDrag] = useState<DragState | null>(null);
  const pointerStartRef = useRef<{
    x: number;
    y: number;
    item: ApplicationItem;
    element: HTMLElement;
  } | null>(null);
  const dragRef = useRef<DragState | null>(null);
  dragRef.current = drag;

  const fetchApplications = useCallback(async () => {
    try {
      setLoading(true);
      const res = await api.getApplications();
      setData(res);
    } catch (err) {
      console.error('Błąd pobierania aplikacji:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchApplications();
  }, [fetchApplications, refreshKey]);

  useEffect(() => {
    if (!loading) onReady();
  }, [loading, onReady]);

  // Escape cancels drag
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && dragRef.current) {
        setDrag(null);
        pointerStartRef.current = null;
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  // Window pointer listeners during drag
  useEffect(() => {
    const handlePointerMove = (e: PointerEvent) => {
      if (!pointerStartRef.current) return;

      const { x: startX, y: startY, item, element } = pointerStartRef.current;
      const dist = Math.hypot(e.clientX - startX, e.clientY - startY);

      if (!dragRef.current) {
        if (dist > 4) {
          const rect = element.getBoundingClientRect();
          const initialDrag: DragState = {
            item,
            originStage: item.stage,
            targetStage: null,
            x: e.clientX,
            y: e.clientY,
            offsetX: startX - rect.left,
            offsetY: startY - rect.top,
            width: rect.width,
            height: rect.height,
          };
          setDrag(initialDrag);
        }
        return;
      }

      // We are already dragging: determine hovered column
      const hitElement = document.elementFromPoint(e.clientX, e.clientY);
      const colEl = hitElement?.closest('[data-stage]');
      const targetStage = colEl ? colEl.getAttribute('data-stage') : null;

      setDrag((prev) =>
        prev
          ? {
              ...prev,
              x: e.clientX,
              y: e.clientY,
              targetStage,
            }
          : null,
      );
    };

    const handlePointerUp = async (_e: PointerEvent) => {
      const activeDrag = dragRef.current;
      const pointerStart = pointerStartRef.current;
      pointerStartRef.current = null;

      if (!activeDrag) {
        // Simple click without dragging
        if (pointerStart) {
          onOpenOffer(pointerStart.item.link);
        }
        return;
      }

      setDrag(null);

      const target = activeDrag.targetStage;
      if (target && target !== activeDrag.originStage) {
        const item = activeDrag.item;
        // Optimistic UI update
        setData((prev) => {
          if (!prev) return prev;
          const updatedItems = prev.items.map((it) =>
            it.link === item.link ? { ...it, stage: target } : it,
          );
          const updatedCounts = { ...prev.counts };
          if (updatedCounts[activeDrag.originStage] !== undefined) {
            updatedCounts[activeDrag.originStage] = Math.max(
              0,
              updatedCounts[activeDrag.originStage] - 1,
            );
          }
          if (updatedCounts[target] !== undefined) {
            updatedCounts[target] = (updatedCounts[target] || 0) + 1;
          }
          return {
            ...prev,
            items: updatedItems,
            counts: updatedCounts,
          };
        });

        try {
          await onUpdateDecision(
            item.link,
            item.status || target,
            item.rating,
            target,
          );
        } catch (err) {
          console.error('Błąd aktualizacji etapu oferty:', err);
        } finally {
          fetchApplications();
        }
      }
    };

    window.addEventListener('pointermove', handlePointerMove);
    window.addEventListener('pointerup', handlePointerUp);
    window.addEventListener('pointercancel', handlePointerUp);

    return () => {
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerup', handlePointerUp);
      window.removeEventListener('pointercancel', handlePointerUp);
    };
  }, [fetchApplications, onOpenOffer, onUpdateDecision]);

  const handleCardPointerDown = (
    e: React.PointerEvent,
    item: ApplicationItem,
  ) => {
    // Only primary button
    if (e.button !== 0) return;
    pointerStartRef.current = {
      x: e.clientX,
      y: e.clientY,
      item,
      element: e.currentTarget as HTMLElement,
    };
  };

  const toggleExpand = (colKey: string) => {
    setExpandedCols((prev) => ({ ...prev, [colKey]: !prev[colKey] }));
  };

  // Group items by stage
  const itemsByStage: Record<string, ApplicationItem[]> = {
    save: [],
    apply: [],
    interview: [],
    offer: [],
    archive: [],
  };

  if (data?.items) {
    for (const it of data.items) {
      if (itemsByStage[it.stage]) {
        itemsByStage[it.stage].push(it);
      }
    }
  }

  // Funnel calculations
  const countSave = data?.counts.save || 0;
  const countApply = data?.counts.apply || 0;
  const countInterview = data?.counts.interview || 0;
  const countOffer = data?.counts.offer || 0;

  const funnelTotal = countSave + countApply + countInterview + countOffer;
  const staleCount = data?.stale_count || 0;

  // Cumulative conversions:
  // save: "początek lejka"
  // apply: (apply + interview + offer) / (save + apply + interview + offer) z zapisanych
  // interview: (interview + offer) / (apply + interview + offer) z wysłanych
  // offer: offer / (interview + offer) z rozmów
  const convSave = 'początek lejka';

  const sumFromSave = countSave + countApply + countInterview + countOffer;
  const sumFromApply = countApply + countInterview + countOffer;
  const sumFromInterview = countInterview + countOffer;
  const sumFromOffer = countOffer;

  const convApply =
    sumFromSave > 0
      ? `${Math.round((sumFromApply / sumFromSave) * 100)}% z zapisanych`
      : '—';

  const convInterview =
    sumFromApply > 0
      ? `${Math.round((sumFromInterview / sumFromApply) * 100)}% z wysłanych`
      : '—';

  const convOffer =
    sumFromInterview > 0
      ? `${Math.round((sumFromOffer / sumFromInterview) * 100)}% z rozmów`
      : '—';

  const conversions: Record<string, string> = {
    save: convSave,
    apply: convApply,
    interview: convInterview,
    offer: convOffer,
    archive: 'zamknięte',
  };

  const isEmpty = !loading && data && data.total === 0;

  return (
    <div className="ab-board panel glass">
      {/* Header */}
      <div className="ab-header">
        <div className="ab-header-left">
          <div className="ab-title-row">
            <h1 className="ab-title">Aplikacje</h1>
            <span className="ab-category-icon">
              <Send size={20} strokeWidth={2} />
            </span>
          </div>
          <p className="ab-count-meta">
            {funnelTotal} w lejku &nbsp;·&nbsp;{' '}
            {staleCount === 1
              ? '1 stoi 14 dni lub dłużej'
              : `${staleCount} stoją 14 dni lub dłużej`}
          </p>
        </div>
        <div className="ab-header-right">
          <span className="ab-hint">przeciągnij kartę, żeby zmienić etap</span>
        </div>
      </div>

      {/* Main Content */}
      {isEmpty ? (
        <div className="ab-empty-state">
          <div className="empty-state well">
            <div className="text">
              <strong>Brak aplikacji w lejku</strong>
              <span>
                Zapisz ofertę klawiszem <span className="kbd">Z</span> lub
                przeciągnij ją z listy, aby trafiła do lejka.
              </span>
            </div>
          </div>
        </div>
      ) : (
        <div className="ab-columns">
          {COLUMNS.map((col) => {
            const items = itemsByStage[col.key] || [];
            const isExpanded = !!expandedCols[col.key];
            const isHoveredTarget =
              drag?.targetStage === col.key && drag.originStage !== col.key;
            const count = data?.counts[col.key] ?? items.length;

            const visibleItems = isExpanded
              ? items
              : items.slice(0, INITIAL_VISIBLE_COUNT);
            const remainingCount = items.length - INITIAL_VISIBLE_COUNT;

            return (
              <div
                key={col.key}
                data-stage={col.key}
                className={col.isArchive ? 'ab-col-archive' : 'ab-col'}
              >
                {/* Column Header */}
                <div className="ab-col-header">
                  <div className="ab-col-header-top">
                    <span className="ab-col-name">{col.name}</span>
                    <span className="ab-col-conversion">
                      {conversions[col.key]}
                    </span>
                  </div>
                  <span className="ab-col-count">{count}</span>
                </div>

                {/* Cards / Rows */}
                <div className="ab-col-cards">
                  {/* Drop Slot when dragging over this column */}
                  {isHoveredTarget && (
                    <div className="ab-drop-slot">
                      <span className="ab-drop-slot-icon">
                        <ArrowDown size={14} strokeWidth={2.2} />
                      </span>
                      <span className="ab-drop-slot-label">
                        Upuść — {col.name}
                      </span>
                    </div>
                  )}

                  {col.isArchive
                    ? visibleItems.map((item) => {
                        const isBeingDragged = drag?.item.link === item.link;
                        const dateLabel = formatStageDate(
                          col.verb,
                          item.applied_at || item.decided_at,
                        );
                        const metaLabel = item.age_days === null ? dateLabel : `${dateLabel} · ${formatAge(item.age_days)}`;

                        return (
                          <button
                            key={item.link}
                            type="button"
                            className={`ab-archive-row ${isBeingDragged ? 'is-dragged' : ''}`}
                            onPointerDown={(e) => handleCardPointerDown(e, item)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') {
                                onOpenOffer(item.link);
                              }
                            }}
                          >
                            <span className="ab-archive-title">{item.title}</span>
                            <span className="ab-archive-meta">{metaLabel}</span>
                          </button>
                        );
                      })
                    : visibleItems.map((item) => {
                        const isBeingDragged = drag?.item.link === item.link;
                        const dateLabel = formatStageDate(
                          col.verb,
                          item.applied_at || item.decided_at,
                        );
                        const isStale = (item.age_days ?? 0) >= 14;

                        return (
                          <div
                            key={item.link}
                            role="button"
                            tabIndex={0}
                            className={`ab-card ${isBeingDragged ? 'is-dragged' : ''}`}
                            onPointerDown={(e) => handleCardPointerDown(e, item)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') {
                                onOpenOffer(item.link);
                              }
                            }}
                          >
                            <div className="ab-card-head">
                              <div className="ab-card-meta">
                                <h3 className="ab-card-title">{item.title}</h3>
                                <p className="ab-card-company">
                                  {[item.company, item.location].filter(Boolean).join(' · ')}
                                </p>
                              </div>
                              <span className="ab-card-score">
                                {item.match_percentage !== null
                                  ? item.match_percentage
                                  : '—'}
                              </span>
                            </div>

                            <NextStepLine step={item.next_step} />

                            <div className="ab-card-footer">
                              <span className="ab-card-since">{dateLabel}</span>
                              <span
                                className={`ab-card-age ${isStale ? 'is-stale' : ''}`}
                              >
                                {formatAge(item.age_days)}
                              </span>
                            </div>
                          </div>
                        );
                      })}

                  {/* "+N więcej" button */}
                  {remainingCount > 0 && (
                    <button
                      type="button"
                      className="ab-more-btn"
                      onClick={() => toggleExpand(col.key)}
                    >
                      {isExpanded ? 'Zwiń' : `+${remainingCount} więcej`}
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Karta pod kursorem idzie do body: backdrop-filter panelu tworzy własny
          układ odniesienia dla position: fixed i przesuwa kartę o położenie panelu. */}
      {drag && createPortal(
        <div
          className="ab-card-lifted"
          style={{
            translate: `${drag.x - drag.offsetX}px ${drag.y - drag.offsetY}px`,
            width: `${drag.width}px`,
          }}
        >
          <div className="ab-card-head">
            <div className="ab-card-meta">
              <h3 className="ab-card-title">{drag.item.title}</h3>
              <p className="ab-card-company">
                {[drag.item.company, drag.item.location].filter(Boolean).join(' · ')}
              </p>
            </div>
            <span className="ab-card-score">
              {drag.item.match_percentage !== null
                ? drag.item.match_percentage
                : '—'}
            </span>
          </div>

          <NextStepLine step={drag.item.next_step} />

          <div className="ab-card-footer">
            <span className="ab-card-since">
              {formatStageDate(
                COLUMNS.find((c) => c.key === drag.item.stage)?.verb || 'etap',
                drag.item.applied_at || drag.item.decided_at,
              )}
            </span>
            <span
              className={`ab-card-age ${(drag.item.age_days ?? 0) >= 14 ? 'is-stale' : ''}`}
            >
              {formatAge(drag.item.age_days)}
            </span>
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
};
