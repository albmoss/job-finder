import { useState } from 'react';
import { Check, Undo2 } from 'lucide-react';
import { api, errorMessage } from '../api';
import { useApp } from '../app_context';
import { formatDayLong } from '../format';
import { CompanyLogo } from '../screens/offer/CompanyLogo';
import type { DecisionStatus, OfferDetail } from '../types';
import { Modal } from './Modal';

export interface MarkSentState {
  offer: OfferDetail;
  /** Status sprzed oznaczenia; „Cofnij oznaczenie” go przywraca. */
  previousStatus: DecisionStatus | null;
  onChange?: (offer: OfferDetail) => void;
}

export function MarkSentModal({ state, onClose }: { state: MarkSentState; onClose: () => void }) {
  const { toast, bumpData } = useApp();
  const { offer, previousStatus, onChange } = state;
  const [note, setNote] = useState(offer.note);
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
      if (note.trim() !== offer.note) {
        updated = (await api.saveNote(offer.link, note.trim())).offer;
      }
      const prevLabel = offer.next_step?.label ?? '';
      const prevDue = offer.next_step?.due?.slice(0, 10) ?? '';
      if (label.trim() !== prevLabel || due !== prevDue) {
        const nextDue = due === prevDue ? (offer.next_step?.due ?? '') : due;
        updated = (await api.saveNextStep(offer.link, label.trim(), label.trim() && nextDue ? nextDue : null)).offer;
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
        <label className="ms-section-label" htmlFor="ms-note">
          Notatka
          <span className="ms-optional">opcjonalnie</span>
        </label>
        <textarea
          id="ms-note"
          className="field ms-note"
          value={note}
          placeholder="Np. wysłane przez formularz, odpowiedź do dwóch tygodni"
          disabled={busy}
          onChange={(e) => setNote(e.target.value)}
        />
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
