import React, { useMemo, useState } from 'react';
import ReactDOM from 'react-dom/client';
import { Stream, type FingerprintShape } from '../components/ui/Stream';
import { MATCH_STREAM_MOTION } from '../components/MatchCard';
import SAVED from '../components/ui/fingerprint_shape.json';
import '../theme_tokens.css';
import '../styles/base.css';
import './fingerprint_lab.css';

/* Laboratorium kształtu odcisku oferty — tylko `npm run dev`, nie trafia do builda.
   Podgląd używa tego samego komponentu Stream co karta oferty. */

type Key = keyof FingerprintShape;

const CONTROLS: { key: Key; label: string; hint: string; min: number; max: number }[] = [
  { key: 'peaksFrom', label: 'Szczyty — słabe', hint: 'wysokość górek i głębokość dołków przy niskim %', min: 0, max: 2.5 },
  { key: 'peaksTo', label: 'Szczyty — mocne', hint: 'to samo przy wysokim %', min: 0, max: 2.5 },
  { key: 'hillsFrom', label: 'Gęstość górek — słabe', hint: 'ile górek na długości przy niskim %', min: 0.3, max: 3 },
  { key: 'hillsTo', label: 'Gęstość górek — mocne', hint: 'to samo przy wysokim %', min: 0.3, max: 3 },
  { key: 'bodyFrom', label: 'Grubość — słabe', hint: 'ogólna grubość płomienia przy niskim %', min: 0.1, max: 1 },
  { key: 'bodyTo', label: 'Grubość — mocne', hint: 'to samo przy wysokim %', min: 0.1, max: 1 },
  { key: 'swell', label: 'Falowanie całości', hint: 'wolne pogrubienia i przewężenia wzdłuż płomienia', min: 0, max: 1.2 },
  { key: 'minThick', label: 'Najcieńsze miejsce', hint: 'dolna granica — płomień nie zejdzie niżej', min: 0, max: 0.8 },
  { key: 'maxThick', label: 'Najwyższy szczyt', hint: '1 = pełna wysokość karty', min: 0.3, max: 1 },
  { key: 'matchFrom', label: 'Próg „słabe”', hint: 'poniżej tego % kształt ma wartości „słabe”', min: 0, max: 1 },
  { key: 'matchTo', label: 'Próg „mocne”', hint: 'powyżej tego % kształt ma wartości „mocne”', min: 0, max: 1 },
];

const MATCHES = [45, 60, 72, 80, 85, 90, 95];

const newSeeds = () => MATCHES.map(() => 1 + Math.floor(Math.random() * 996));

const Lab: React.FC = () => {
  const [shape, setShape] = useState<FingerprintShape>(SAVED);
  const [seeds, setSeeds] = useState(newSeeds);
  const [status, setStatus] = useState('');
  const dirty = useMemo(() => CONTROLS.some(({ key }) => shape[key] !== SAVED[key]), [shape]);

  const set = (key: Key, v: number) => {
    setShape((s) => ({ ...s, [key]: v }));
    setStatus('');
  };

  const save = async () => {
    setStatus('zapisuję…');
    const res = await fetch('/__lab/fingerprint', { method: 'POST', body: JSON.stringify(shape) });
    setStatus(res.ok ? 'zapisane w fingerprint_shape.json — teraz npm run build' : `błąd: ${await res.text()}`);
  };

  return (
    <div className="lab">
      <aside className="lab-controls">
        <h1>Odcisk oferty</h1>
        <p className="lab-sub">Suwaki zmieniają podgląd od razu. „Zapisz” wpisuje wartości do pliku.</p>
        {CONTROLS.map(({ key, label, hint, min, max }) => (
          <label key={key} className="lab-row">
            <span className="lab-row-head">
              <span>{label}</span>
              <span className="mono tnum">{shape[key].toFixed(2)}</span>
            </span>
            <input
              type="range"
              min={min}
              max={max}
              step={0.01}
              value={shape[key]}
              onChange={(e) => set(key, Number(e.target.value))}
            />
            <span className="lab-hint">{hint}</span>
          </label>
        ))}
        <div className="lab-actions">
          <button className="btn btn-primary" onClick={save} disabled={!dirty}>Zapisz</button>
          <button className="btn btn-secondary" onClick={() => setShape(SAVED)} disabled={!dirty}>Cofnij do zapisanych</button>
        </div>
        <div className="lab-actions">
          <button className="btn btn-quiet" onClick={() => setSeeds(newSeeds())}>Inne kształty</button>
        </div>
        {status && <p className="lab-status">{status}</p>}
      </aside>
      <main className="lab-previews">
        {MATCHES.map((m, i) => (
          <div key={m} className="lab-card">
            <div className="lab-score">
              <span className="lab-num tnum">{m}</span>
              <span className="lab-pct">%</span>
            </div>
            <Stream
              seed={seeds[i]}
              progress={m / 100}
              {...MATCH_STREAM_MOTION}
              shape={shape}
              className="lab-stream"
            />
          </div>
        ))}
      </main>
    </div>
  );
};

const root = document.getElementById('root');
if (root) ReactDOM.createRoot(root).render(<Lab />);
