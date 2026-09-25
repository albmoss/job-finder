import React from 'react';
import type { ActivityRow, RecentDecision, Stats } from '../types';
import { formatStamp } from '../pipeline';
import {
  ArrowLeft,
  Bookmark,
  Send,
  MountainSnow,
  CircleX,
  Star,
  CircleCheck,
  Ban,
} from 'lucide-react';
import '../styles/offers.css';

interface OverviewPanelProps {
  activeTab: string;
  /** Lista zawężona z panelu braków — wtedy zdanie mówi o braku, nie o całej zakładce. */
  gapSkill: string | null;
  total: number;
  stats: Stats | null;
  activityRows: ActivityRow[];
  recentDecisions: RecentDecision[];
  onSelectOffer: (link: string) => void;
  onStartReview: (() => void) | null;
}

function getDecisionIcon(status: string) {
  switch (status) {
    case 'save':
    case 'saved':
      return <Bookmark aria-hidden="true" />;
    case 'apply':
    case 'applied':
      return <Send aria-hidden="true" />;
    case 'aspirational':
    case 'aspire':
      return <MountainSnow aria-hidden="true" />;
    case 'reject':
    case 'rejected':
      return <CircleX aria-hidden="true" />;
    case 'rated':
    default:
      return <Star aria-hidden="true" />;
  }
}

export const OverviewPanel: React.FC<OverviewPanelProps> = ({
  activeTab,
  gapSkill,
  total,
  stats,
  activityRows,
  recentDecisions,
  onSelectOffer,
  onStartReview,
}) => {
  const numStr = total.toLocaleString('pl-PL');

  const sentence = gapSkill
    ? `ofert, którym brakuje „${gapSkill}”`
    : activeTab === 'Dopasowane'
      ? total > 0
        ? 'ofert czeka na Twoją decyzję'
        : 'Wszystko przejrzane'
      : `ofert w zakładce „${activeTab}”`;

  return (
    <section className="panel glass ov-pane" aria-label="Przegląd">
      <div className="ov-wash" aria-hidden="true" />

      {/* Bez przewijania całości: hero i statystyki stoją, a listy w kolumnach mają własny scroll. */}
      <div className="ov-body">
        {/* Sekcja Hero */}
        <div className="ov-hero">
          <div className="ov-headline">
            <span className="ov-eyebrow">{activeTab}  ·  przegląd</span>
            <div className="ov-number">{numStr}</div>
            <div className="ov-sentence">{sentence}</div>
          </div>

          {onStartReview && (
            <div className="ov-actions">
              <button
                type="button"
                className="btn btn-primary press"
                onClick={onStartReview}
                aria-label="Otwórz pierwszą ofertę z listy"
              >
                <ArrowLeft size={16} aria-hidden="true" /> Otwórz pierwszą
              </button>
            </div>
          )}
        </div>

        {/* Pasek statystyk */}
        <div className="ov-stats" role="group" aria-label="Statystyki bazy">
          <div className="ov-stat">
            <b>{(stats?.raw_count ?? 0).toLocaleString('pl-PL')}</b>
            <span>ofert w bazie</span>
          </div>
          <div className="ov-stat">
            <b>{(stats?.analyzed_count ?? 0).toLocaleString('pl-PL')}</b>
            <span>ocenionych przez AI</span>
          </div>
          <div className="ov-stat">
            <b>{(stats?.pending_scoring_count ?? 0).toLocaleString('pl-PL')}</b>
            <span>czeka na ocenę AI</span>
          </div>
          <div className="ov-stat">
            <b>{(stats?.decisions_count ?? 0).toLocaleString('pl-PL')}</b>
            <span>Twoich decyzji</span>
          </div>
        </div>

        {/* 2 kolumny: Ostatnie decyzje + Co się ostatnio działo */}
        <div className="ov-feeds">
          <div className="ov-feed-col">
            <div className="ov-feed-head">
              <span className="ov-feed-title">Ostatnie decyzje</span>
            </div>

            {recentDecisions.length === 0 ? (
              <p className="ov-feed-hint">Jeszcze nic nie oceniłeś.</p>
            ) : (
              <div className="ov-feed-list" role="list">
                {recentDecisions.map((dec) => (
                  <button
                    key={dec.link}
                    type="button"
                    className="ov-dec-row press"
                    onClick={() => onSelectOffer(dec.link)}
                    aria-label={`${dec.title}, ${dec.label || dec.status}, ${dec.company}`}
                  >
                    <span className="ov-dec-well">{getDecisionIcon(dec.status)}</span>
                    <span className="ov-dec-text">
                      <span className="ov-dec-title">{dec.title}</span>
                      <span className="ov-dec-company">
                        {dec.label || dec.status}  ·  {dec.company}
                      </span>
                    </span>
                    {dec.stamp && (
                      <span className="ov-dec-time">{formatStamp(dec.stamp)}</span>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="ov-feed-col">
            <div className="ov-feed-head">
              <span className="ov-feed-title">Co się ostatnio działo</span>
            </div>

            {activityRows.length === 0 ? (
              <p className="ov-feed-hint">Pipeline jeszcze nie chodził.</p>
            ) : (
              <div className="ov-feed-list">
                {activityRows.map((row, idx) => (
                  <div key={idx} className="ov-act-row">
                    <span className="ov-act-time">{formatStamp(row.time_str)}</span>
                    {row.bad ? (
                      <Ban className="ov-act-icon is-bad" aria-hidden="true" />
                    ) : (
                      <CircleCheck className="ov-act-icon" aria-hidden="true" />
                    )}
                    <span className="ov-act-what">
                      <span className={`ov-act-op ${row.bad ? 'is-bad' : ''}`}>{row.op}</span>
                      <span className="ov-act-text">{row.what}</span>
                    </span>
                    <span className="ov-act-detail">{row.detail}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </section>
  );
};
