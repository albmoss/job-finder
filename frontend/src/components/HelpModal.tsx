import React, { useEffect, useState } from 'react';
import { X } from 'lucide-react';
import { ADD_LINK_VIEW } from '../views';
import '../styles/topbar.css';

interface HelpModalProps {
  isOpen: boolean;
  onClose: () => void;
}

/** Wiersz skrótu: `keys` stoją obok siebie, `range` wstawia między nimi „–” (np. 1 – 0). */
interface HelpKey {
  keys: string[];
  range?: boolean;
  text: string;
}

const SECTIONS: { title: string; rows: HelpKey[] }[] = [
  {
    title: 'Lista',
    rows: [
      { keys: ['J', '↓'], text: 'Następna oferta, także na kolejnej stronie' },
      { keys: ['K', '↑'], text: 'Poprzednia oferta' },
      { keys: ['/'], text: 'Szukaj po stanowisku lub firmie' },
      { keys: ['Esc'], text: 'Zamknij ofertę, wyczyść lub zwiń szukanie' },
    ],
  },
  {
    title: 'Oferta',
    rows: [
      { keys: ['Z'], text: 'Zapisz' },
      { keys: ['W'], text: 'Wysłane' },
      { keys: ['A'], text: 'Aspiruję' },
      { keys: ['X'], text: 'Odrzuć' },
      { keys: ['1', '0'], range: true, text: 'Ocena (0 = 10)' },
      { keys: ['Enter'], text: 'Zapisz ocenę' },
      { keys: ['O'], text: 'Otwórz ogłoszenie w nowej karcie' },
    ],
  },
  {
    title: 'Ogólne',
    rows: [
      { keys: ['P'], text: 'Otwórz lub zwiń arkusz pipeline’u' },
      { keys: ['Ctrl', 'Enter'], text: `Zapisz ofertę w „${ADD_LINK_VIEW}”` },
      { keys: ['?'], text: 'Ta pomoc' },
    ],
  },
];

export const HelpModal: React.FC<HelpModalProps> = ({ isOpen, onClose }) => {
  useEffect(() => {
    if (!isOpen) return;
    // Otwierające ? (App, preventDefault) dochodzi tu jeszcze po zamontowaniu — pomiń je.
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented) return;
      if (e.key === 'Escape' || e.key === '?') {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isOpen, onClose]);

  // Wyjście tą samą drogą co wejście: po `isOpen=false` okno zostaje na 180 ms animacji.
  const [present, setPresent] = useState(isOpen);
  const [closing, setClosing] = useState(false);
  useEffect(() => {
    if (isOpen) {
      setPresent(true);
      setClosing(false);
      return;
    }
    setClosing(true);
    const timer = window.setTimeout(() => {
      setPresent(false);
      setClosing(false);
    }, 180);
    return () => window.clearTimeout(timer);
  }, [isOpen]);

  if (!present) return null;

  return (
    <div
      className={`modal-backdrop${closing ? ' is-closing' : ''}`}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="help-title"
    >
      <div className={`modal help-modal${closing ? ' is-closing' : ''}`}>
        <div className="modal-head">
          <div>
            <h2 id="help-title">Pomoc</h2>
            <p>Skróty działają, gdy kursor nie stoi w polu tekstowym.</p>
          </div>
          <button type="button" className="iconbtn press" onClick={onClose} aria-label="Zamknij (Esc)">
            <X />
          </button>
        </div>

        <div className="help-about">
          <p>
            <b>Dopasowane</b> są ułożone od najlepszego dopasowania do CV. Każda decyzja zdejmuje
            ofertę z tej kolejki i uczy profil, czego szukasz. W pozostałych zakładkach wybierz ofertę
            z listy, żeby zobaczyć opis i zmienić decyzję.
          </p>
          <p>
            Klik w zakres stron w stopce listy („1–25 z …”) rozwija skok o wiele stron: suwak i progi
            dopasowania.
          </p>
        </div>

        <div className="help-grid">
          {SECTIONS.map((section) => (
            <section key={section.title} className="help-section" aria-label={section.title}>
              <span className="section-label">{section.title}</span>
              <dl className="help-keys">
                {section.rows.map((row) => (
                  <div key={row.text} className="help-row">
                    <dt>
                      {row.keys.map((key, i) => (
                        <React.Fragment key={key}>
                          {row.range && i > 0 && <span className="help-dash">–</span>}
                          <kbd className="kbd">{key}</kbd>
                        </React.Fragment>
                      ))}
                    </dt>
                    <dd>{row.text}</dd>
                  </div>
                ))}
              </dl>
            </section>
          ))}
        </div>
      </div>
    </div>
  );
};
