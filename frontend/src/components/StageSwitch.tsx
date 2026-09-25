import React from 'react';
import '../styles/applications.css';

export interface StageSwitchProps {
  value: string;
  onChange: (stage: string) => void;
  disabled?: boolean;
}

export const STAGES: Array<{ key: string; label: string }> = [
  { key: 'save', label: 'Zapisane' },
  { key: 'apply', label: 'Wysłane' },
  { key: 'interview', label: 'Rozmowa' },
  { key: 'offer', label: 'Oferta' },
  { key: 'archive', label: 'Archiwum' },
];

export const StageSwitch: React.FC<StageSwitchProps> = ({
  value,
  onChange,
  disabled = false,
}) => {
  return (
    <div
      className={`ab-stage-switch ${disabled ? 'is-disabled' : ''}`}
      role="radiogroup"
      aria-label="Etap rekrutacji"
    >
      {STAGES.map((s) => {
        const isActive = value === s.key;
        return (
          <button
            key={s.key}
            type="button"
            role="radio"
            aria-checked={isActive}
            disabled={disabled}
            className={`ab-stage-segment press ${isActive ? 'is-active' : ''}`}
            onClick={() => {
              if (!disabled && value !== s.key) {
                onChange(s.key);
              }
            }}
          >
            <span className="ab-stage-label">{s.label}</span>
          </button>
        );
      })}
    </div>
  );
};
