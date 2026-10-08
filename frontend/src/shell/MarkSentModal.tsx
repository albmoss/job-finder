import { useState } from 'react';
import { Check, Undo2 } from 'lucide-react';
import { api, errorMessage } from '../api';
import { useApp } from '../app_context';
import { formatDayLong } from '../format';
import { CompanyLogo } from '../screens/offer/CompanyLogo';
import { STAGES } from '../stages';
import type { DecisionStatus, OfferDetail, Stage } from '../types';
import { Modal } from './Modal';

export interface MarkSentState {
  offer: OfferDetail;
  /** Status sprzed oznaczenia; „Cofnij oznaczenie” go przywraca. */
  previousStatus: DecisionStatus | null;
  cvVersionId: string | undefined;
  onChange?: (offer: OfferDetail) => void;
}

export function MarkSentModal({ state, onClose }: { state: MarkSentState; onClose: () => void }) {
  const { toast, bumpData } = useApp();
  const { offer, previousStatus, cvVersionId, onChange } = state;
  const [stage, setStage] = useState<Stage>(offer.stage ?? 'apply');
  const [label, setLabel] = useState(offer.next_step?.label ?? '');
  const [due, setDue] = useState(offer.next_step?.due?.slice(0, 10) ?? '');
  const [busy, setBusy] = useState(false);

  const finish = (updated: OfferDetail, message: string) => {
    onChange?.(updated);
    bumpData();
    toast(message);
    onClose();
  };

  const save = async () => {
    setBusy(true);
    try {
      let updated = offer;
      if (stage !== (offer.stage ?? 'apply')) {
        updated = (await api.updateDecision(offer.link, 'apply', stage, cvVersionId)).offer;
      }
      const prevLabel = offer.next_step?.label ?? '';
      const prevDue = offer.next_step?.due?.slice(0, 10) ?? '';
      if (label.trim() !== prevLabel || due !== prevDue) {
        updated = (await api.saveNextStep(offer.link, label.trim(), label.trim() && due ? due : null)).offer;
      }
      finish(updated, 'Zapisano aplikację.');
    } catch (err) {
      toast(`Nie udało się zapisać: ${errorMessage(err)}`);
      setBusy(false);
    }
  };

  const undo = async () => {
    setBusy(true);
    try {
      const res =
        previousStatus && previousStatus !== 'apply'
          ? await api.updateDecision(offer.link, previousStatus)
          : await api.restoreDecision(offer.link);
      finish(res.offer, 'Cofnięto oznaczenie.');
    } catch (err) {
      toast(`Nie udało się cofnąć: ${errorMessage(err)}`);
      setBusy(false);
    }
  };

  const day = formatDayLong(offer.applied_at ?? offer.decided_at ?? new Date());
  const hasLabel = label.trim() !== '';

  return (
    <Modal
      title={
        <span className="modal-title">
          <span className="modal-badge" aria-hidden="true">
            <Check />
          </span>
          Aplikacja wysłana
        </span>
      }
      label="Aplikacja oznaczona jako wysłana"
      onClose={onClose}
    >
      <div className="ms-offer">
        <CompanyLogo url={offer.logo_url} source={offer.source} size="sm" />
        <span className="ms-offer-id">
          <span className="ms-offer-title" title={offer.title}>
            {offer.title}
          </span>
          <span className="ms-offer-meta">{[offer.company, day && `wysłano ${day}`].filter(Boolean).join(' · ')}</span>
        </span>
      </div>

      <section className="ms-section">
        <span id="ms-stage-label" className="ms-section-label">
          Etap rekrutacji
        </span>
        <div className="ms-stages" role="radiogroup" aria-labelledby="ms-stage-label">
          {STAGES.map(({ id, label: stageLabel, icon: Icon }) => (
            <button
              key={id}
              type="button"
              role="radio"
              aria-checked={stage === id}
              className="ms-stage"
              disabled={busy}
              onClick={() => setStage(id)}
            >
              <Icon />
              {stageLabel}
            </button>
          ))}
        </div>
      </section>

      <section className="ms-section">
        <label className="ms-section-label" htmlFor="ms-next">
          Następny krok
          <span className="ms-optional">opcjonalnie</span>
        </label>
        <div className="ms-next">
          <input
            id="ms-next"
            className="field"
            value={label}
            placeholder="Np. rozmowa z HR"
            disabled={busy}
            onChange={(e) => setLabel(e.target.value)}
          />
          <input
            type="date"
            className={`field ms-date${due ? '' : ' is-empty'}`}
            value={due}
            aria-label="Termin następnego kroku"
            title={hasLabel ? undefined : 'Najpierw wpisz następny krok'}
            disabled={busy || !hasLabel}
            onChange={(e) => setDue(e.target.value)}
          />
        </div>
      </section>

      <div className="modal-actions ms-actions">
        <button type="button" className="btn-quiet ms-undo" disabled={busy} onClick={undo}>
          <Undo2 />
          Cofnij oznaczenie
        </button>
        <button type="button" className="btn btn-primary" disabled={busy} onClick={save}>
          <Check />
          Zapisz
        </button>
      </div>
    </Modal>
  );
}
