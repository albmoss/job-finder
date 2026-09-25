import React, { useEffect, useState } from 'react';
import type { NextStep } from '../types';

interface NextStepEditorProps {
  link: string;
  value: NextStep | null;
  /** Pusty `label` usuwa krok; `due` w formacie backendu "YYYY-MM-DD HH:MM". */
  onSave: (link: string, label: string, due: string | null) => Promise<boolean>;
}

/** Pole `datetime-local` daje "YYYY-MM-DDTHH:MM", backend trzyma "YYYY-MM-DD HH:MM"
 *  (sam dzień zapisany wcześniej dostaje 09:00, żeby pole go pokazało). */
function toInput(due: string | null): string {
  if (!due) return '';
  return due.length === 10 ? `${due}T09:00` : due.replace(' ', 'T');
}

export const NextStepEditor: React.FC<NextStepEditorProps> = ({ link, value, onSave }) => {
  const savedLabel = value?.label ?? '';
  const savedDue = toInput(value?.due ?? null);
  const [label, setLabel] = useState(savedLabel);
  const [due, setDue] = useState(savedDue);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setLabel(savedLabel);
    setDue(savedDue);
  }, [link, savedLabel, savedDue]);

  const trimmed = label.trim();
  const dirty = trimmed !== savedLabel || (trimmed !== '' && due !== savedDue);

  const save = async (nextLabel: string, nextDue: string) => {
    setSaving(true);
    const ok = await onSave(link, nextLabel, nextLabel && nextDue ? nextDue.replace('T', ' ') : null);
    setSaving(false);
    if (!ok) {
      setLabel(savedLabel);
      setDue(savedDue);
    }
  };

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (dirty && !saving) save(trimmed, due);
  };

  return (
    <form className="od-next" onSubmit={submit} aria-label="Następny krok">
      <span className="od-stage-label">Następny krok</span>
      <div className="od-next-row">
        <input
          className="input od-next-label"
          value={label}
          maxLength={80}
          placeholder="np. rozmowa z HR"
          aria-label="Co trzeba zrobić"
          onChange={(event) => setLabel(event.target.value)}
        />
        <input
          type="datetime-local"
          className="input od-next-due"
          value={due}
          aria-label="Termin"
          disabled={!trimmed}
          onChange={(event) => setDue(event.target.value)}
        />
        <button type="submit" className="btn btn-secondary press" disabled={!dirty || saving}>
          Zapisz
        </button>
        {value && (
          <button
            type="button"
            className="btn btn-quiet press"
            disabled={saving}
            onClick={() => save('', '')}
          >
            Usuń
          </button>
        )}
      </div>
    </form>
  );
};
