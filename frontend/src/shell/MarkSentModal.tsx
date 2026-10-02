import { useState } from 'react';
import { Check, Undo2 } from 'lucide-react';
import { api, errorMessage } from '../api';
import { useApp } from '../app_context';
import { formatDayLong } from '../format';
import type { DecisionStatus, OfferDetail, Stage } from '../types';
import { Modal } from './Modal';

const STAGES: { id: Stage; label: string }[] = [
  { id: 'apply', label: 'Wysłane' },
  { id: 'interview', label: 'Rozmowy' },
  { id: 'archive', label: 'Zakończone' },
];

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

  return (
    <Modal title="Aplikacja oznaczona jako wysłana" onClose={onClose}>
      <p className="muted">
        {offer.company}
        {day && ` / ${day}`}
      </p>
      <div className="segmented" role="radiogroup" aria-label="Etap aplikacji">
        {STAGES.map((s) => (
          <button
            key={s.id}
            type="button"
            role="radio"
            aria-checked={stage === s.id}
            className="btn"
            disabled={busy}
            onClick={() => setStage(s.id)}
          >
            {stage === s.id && <Check />}
            {s.label}
          </button>
        ))}
      </div>
      <label className="ms-label" htmlFor="ms-next">
        Następny krok (opcjonalnie)
      </label>
      <div className="field">
        <input
          id="ms-next"
          value={label}
          placeholder="Np. rozmowa z HR"
          disabled={busy}
          onChange={(e) => setLabel(e.target.value)}
        />
        <input
          type="date"
          className="ms-date"
          value={due}
          aria-label="Dodaj termin"
          disabled={busy}
          onChange={(e) => setDue(e.target.value)}
        />
      </div>
      <div className="modal-actions">
        <button type="button" className="btn btn-primary" disabled={busy} onClick={save}>
          <Check />
          Zapisz
        </button>
        <button type="button" className="btn" disabled={busy} onClick={undo}>
          <Undo2 />
          Cofnij oznaczenie
        </button>
      </div>
    </Modal>
  );
}
