import type { CSSProperties } from 'react';
import { ArrowRight, Check, TriangleAlert } from 'lucide-react';
import { countFormat } from '../format';
import { navigate, paths, useRoute } from '../router';
import { GROUPS, formatClock, foundTotal, sourcesWithProblems } from '../run_progress';
import { CompanyLogo } from '../screens/offer/CompanyLogo';
import { locationLine } from '../screens/offer/offer_format';
import type { PipelineState, RunSummary } from '../types';
import { Modal } from './Modal';

export interface RunDoneState {
  pipeline: PipelineState;
  summary: RunSummary | null;
}

export function RunDoneModal({ state, onClose }: { state: RunDoneState; onClose: () => void }) {
  const route = useRoute();
  const { pipeline, summary } = state;
  const failed = pipeline.status === 'failed';
  const duration =
    pipeline.started_at != null && pipeline.finished_at != null ? pipeline.finished_at - pipeline.started_at : null;
  const found = foundTotal(pipeline);
  const failedStage = pipeline.stages.find((s) => s.status === 'failed')?.id;
  const failedGroup = GROUPS.find((g) => (g.ids as readonly string[]).includes(failedStage ?? ''));
  const recent = summary?.recent.slice(0, 3) ?? [];
  const problems = sourcesWithProblems(pipeline);
  const heading = failed ? 'Wyszukiwanie przerwane' : 'Wyszukiwanie zakończone';

  const stats: { label: string; value: string }[] = [];
  if (duration != null) stats.push({ label: 'Czas', value: formatClock(duration) });
  if (found != null) stats.push({ label: 'Pobrane z portali', value: countFormat.format(found) });
  if (summary?.downloaded != null) stats.push({ label: 'Nowe w bazie', value: countFormat.format(summary.downloaded) });
  if (summary && (!failed || summary.checked > 0)) {
    stats.push({ label: 'Porównane z CV', value: countFormat.format(summary.checked) });
    stats.push({ label: 'Dopasowane', value: countFormat.format(summary.matched_count) });
  }

  const go = (path: string) => {
    onClose();
    navigate(path);
  };

  return (
    <Modal
      label={heading}
      title={
        <span className="modal-title">
          <span className={`modal-badge${failed ? ' is-warn' : ''}`} aria-hidden="true">
            {failed ? <TriangleAlert /> : <Check />}
          </span>
          {heading}
        </span>
      }
      onClose={onClose}
      wide
    >
      {failed && (
        <p className="rd-error">
          <TriangleAlert />
          <span>
            {failedGroup ? `Błąd w kroku „${failedGroup.label}”.` : 'Przebieg zakończył się błędem.'}
            {pipeline.error_message ? ` ${pipeline.error_message}` : ''}
          </span>
        </p>
      )}
      {stats.length > 0 && (
        <dl className="rd-stats">
          {stats.map((stat) => (
            <div key={stat.label} className="rd-stat">
              <dt>{stat.label}</dt>
              <dd>{stat.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {problems.length > 0 && (
        <section className="rd-problems">
          <p className="rd-problems-lead">
            <TriangleAlert />
            {failed
              ? 'Portale z problemami:'
              : 'Nie wszystkie portale zadziałały. Reszta ofert jest już porównana; te portale spróbujemy przy następnym wyszukiwaniu.'}
          </p>
          <ul className="rd-problems-list">
            {problems.map((problem) => (
              <li key={problem.name}>
                <span className="rd-problem-name">{problem.name}</span>
                <span className="rd-problem-text">{problem.text}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
      {!failed && (
        <section className="rd-best">
          <h3 className="rd-best-title">Najlepsze nowe dopasowania</h3>
          {recent.length > 0 ? (
            recent.map((offer, index) => {
              const percent = offer.match_percentage != null ? Math.round(offer.match_percentage) : null;
              return (
                <button
                  key={offer.link}
                  type="button"
                  className="rd-row"
                  style={{ '--i': index } as CSSProperties}
                  onClick={() => go(paths.matched(offer.link))}
                >
                  <CompanyLogo url={offer.logo_url} source={offer.source} size="sm" />
                  <span className="rd-row-id">
                    <span className="rd-row-top">
                      <span className="rd-row-title">{offer.title}</span>
                      {percent != null && <span className="rd-row-pct">{percent}%</span>}
                    </span>
                    <span className="rd-row-meta">
                      {[offer.company, locationLine(offer.location, offer.work_mode)].filter(Boolean).join(' · ')}
                    </span>
                    {percent != null && (
                      <span
                        className="match-track"
                        aria-hidden="true"
                        style={{ '--fill': `${Math.max(0, Math.min(100, percent))}%` } as CSSProperties}
                      />
                    )}
                  </span>
                  <ArrowRight className="rd-row-arrow" />
                </button>
              );
            })
          ) : (
            <p className="muted">Tym razem żadna nowa oferta nie pasuje do CV.</p>
          )}
        </section>
      )}
      <div className="modal-actions modal-actions-end">
        <button type="button" className="btn" onClick={onClose}>
          Zamknij
        </button>
        {!failed && (
          <button type="button" className="btn btn-primary" onClick={() => go(paths.matched())}>
            Pokaż oferty
          </button>
        )}
        {failed && route.name !== 'postep' && (
          <button type="button" className="btn btn-primary" onClick={() => go(paths.progress)}>
            Pokaż szczegóły
          </button>
        )}
      </div>
    </Modal>
  );
}
