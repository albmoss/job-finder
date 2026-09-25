import React, { useCallback, useEffect, useState } from 'react';
import type { OfferDetail } from '../types';
import { shouldIgnoreShortcut } from '../keys';
import {
  ArrowUpRight,
  Copy,
  Trash2,
  AlertTriangle,
  RotateCcw,
  X,
  Layers,
  MapPin,
  Laptop,
  GraduationCap,
  Timer,
  CircleDashed,
  Check,
} from 'lucide-react';
import { MatchCard } from './MatchCard';
import { DecisionDock } from './DecisionDock';
import { StageSwitch } from './StageSwitch';
import { NextStepEditor } from './NextStepEditor';
import '../styles/offers.css';

interface OfferDetailPanelProps {
  offer: OfferDetail;
  onClose: () => void;
  onUpdateDecision: (
    link: string,
    status: string,
    rating: number | null,
    stage?: string,
    opts?: { animateExit?: boolean },
  ) => void;
  onRestoreDecision: (link: string) => void;
  onDeleteOffer: (link: string) => void;
  /** Brak w podglądzie szkicu — oferty bez decyzji nie mają etapów. */
  onSaveNextStep?: (link: string, label: string, due: string | null) => Promise<boolean>;
  preview?: boolean;
  /** W trakcie przebiegu odcisk stoi — ruch zostaje dla paska i arkusza pipeline'u. */
  pipelineRunning?: boolean;
}

export const OfferDetailPanel: React.FC<OfferDetailPanelProps> = ({
  offer,
  onClose,
  onUpdateDecision,
  onRestoreDecision,
  onDeleteOffer,
  onSaveNextStep,
  preview = false,
  pipelineRunning = false,
}) => {
  // 0 = oferta bez oceny: dok nie zapala żadnego poziomu, a decyzja idzie z rating = null
  // (generate_preference_profile.py sam traktuje wtedy save/apply jak 9, reject jak 1).
  const [rating, setRating] = useState<number>(offer.rating ?? 0);
  const [savedRating, setSavedRating] = useState<number>(offer.rating ?? 0);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [copiedLink, setCopiedLink] = useState(false);

  useEffect(() => {
    const r = offer.rating ?? 0;
    setRating(r);
    setSavedRating(r);
    setConfirmDelete(false);
    setCopiedLink(false);
  }, [offer.link, offer.rating]);

  // Decyzja z doku (myszą) animuje wyjazd wiersza z listy; ze skrótu — bez animacji:
  // Z/W/A/X to czynność powtarzana dziesiątki razy pod rząd, ruch tylko by ją spowalniał.
  const decide = useCallback(
    (status: string, viaPointer = false) => {
      if (preview) return;
      const finalRating = !rating ? null : status === 'reject' ? Math.min(rating, 3) : rating;
      if (status === 'reject' && rating > 3) {
        setRating(3);
        setSavedRating(3);
      }
      onUpdateDecision(offer.link, status, finalRating, undefined, { animateExit: viaPointer });
    },
    [offer.link, rating, preview, onUpdateDecision],
  );

  const confirmRating = useCallback(() => {
    if (preview) return;
    setSavedRating(rating);
    onUpdateDecision(offer.link, offer.status || 'rated', rating);
  }, [offer.link, offer.status, rating, preview, onUpdateDecision]);

  // Skróty klawiszowe w panelu oferty
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (shouldIgnoreShortcut(event)) return;
      const key = event.key.toLowerCase();

      if (key === 'z') {
        event.preventDefault();
        decide('save');
      } else if (key === 'w') {
        event.preventDefault();
        decide('apply');
      } else if (key === 'a') {
        event.preventDefault();
        decide('aspirational');
      } else if (key === 'x') {
        event.preventDefault();
        decide('reject');
      } else if (/^[0-9]$/.test(key)) {
        event.preventDefault();
        const num = key === '0' ? 10 : Number(key);
        setRating(num);
      } else if (event.key === 'Enter') {
        if (rating !== savedRating) {
          event.preventDefault();
          confirmRating();
        }
      } else if (key === 'o' && offer.link) {
        event.preventDefault();
        window.open(offer.link, '_blank', 'noopener,noreferrer');
      }
    };

    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [offer.link, decide, confirmRating, rating, savedRating]);

  const handleCopyLink = () => {
    if (!offer.link) return;
    navigator.clipboard?.writeText(offer.link);
    setCopiedLink(true);
    setTimeout(() => setCopiedLink(false), 1800);
  };

  // Zbuduj listę faktów z niepustych danych
  const facts: Array<{ icon: React.ReactNode; label: string; value: string }> = [];
  if (offer.industry) {
    facts.push({ icon: <Layers />, label: 'Branża', value: offer.industry });
  }
  if (offer.location) {
    facts.push({ icon: <MapPin />, label: 'Lokalizacja', value: offer.location });
  }
  if (offer.work_mode) {
    facts.push({ icon: <Laptop />, label: 'Tryb', value: offer.work_mode });
  }
  if (offer.is_entry_level) {
    facts.push({ icon: <GraduationCap />, label: 'Poziom', value: 'junior / staż' });
  }
  if (offer.learnable_in_month) {
    facts.push({ icon: <Timer />, label: 'Wdrożenie', value: 'nauka ≤ 1 mc' });
  }

  const blocks = offer.description_blocks ?? [];
  const hasGaps = Boolean(offer.missing_skills && offer.missing_skills.length > 0);
  const highlights = offer.highlights ?? [];
  const title = offer.title || 'Bez tytułu';
  const titleCut = title.lastIndexOf(' ') + 1;
  const titleHead = title.slice(0, titleCut);
  const titleTail = title.slice(titleCut);

  return (
    <article className="panel glass od-pane" aria-label="Szczegóły oferty">
      <div className="od-wash" aria-hidden="true" />

      <div className="od-scroll" key={offer.link}>
        {/* Wiersz źródła */}
        <div className="od-src">
          <div className="od-src-left">
            <span className="od-chip-portal">
              {preview ? 'Ręcznie' : offer.source || 'Portal'}
            </span>
            <span className="od-src-note">
              {preview
                ? 'podgląd — tak oferta wejdzie do bazy'
                : offer.is_gone
                ? 'zdjęta z portalu'
                : ''}
            </span>
          </div>

          <div className="od-src-actions">
            {!preview && offer.link && (
              <button
                type="button"
                className="iconbtn press"
                data-tip={copiedLink ? 'Skopiowano!' : 'Kopiuj link'}
                aria-label="Kopiuj link do schowka"
                onClick={handleCopyLink}
              >
                {copiedLink ? <Check size={16} color="var(--accent)" /> : <Copy size={16} />}
              </button>
            )}

            {!preview && offer.status && (
              <button
                type="button"
                className="iconbtn press"
                data-tip="Cofnij decyzję — oferta wraca do nieocenionych"
                aria-label="Cofnij decyzję"
                onClick={() => onRestoreDecision(offer.link)}
              >
                <RotateCcw size={16} />
              </button>
            )}

            {!preview && (
              <>
                {!confirmDelete ? (
                  <button
                    type="button"
                    className="iconbtn press"
                    data-tip="Usuń z bazy"
                    aria-label="Usuń z bazy"
                    onClick={() => setConfirmDelete(true)}
                  >
                    <Trash2 size={16} />
                  </button>
                ) : (
                  <>
                    <button
                      type="button"
                      className="iconbtn is-on press"
                      data-tip="Na pewno usunąć?"
                      aria-label="Potwierdź usunięcie z bazy"
                      onClick={() => {
                        setConfirmDelete(false);
                        onDeleteOffer(offer.link);
                      }}
                    >
                      <AlertTriangle size={16} />
                    </button>
                    <button
                      type="button"
                      className="iconbtn press"
                      data-tip="Anuluj"
                      aria-label="Anuluj usuwanie"
                      onClick={() => setConfirmDelete(false)}
                    >
                      <X size={16} />
                    </button>
                  </>
                )}
              </>
            )}

            <button
              type="button"
              className="iconbtn press"
              data-tip="Zamknij"
              data-kbd="Esc"
              aria-label="Zamknij podgląd (Esc)"
              onClick={onClose}
            >
              <X size={16} />
            </button>
          </div>
        </div>

        {/* Tytuł i firma */}
        <h2 className="od-title">
          {!preview && offer.link ? (
            <>
              {titleHead}
              {/* Ostatnie słowo i przycisk w jednym nowrap — ikona nie zostaje sama w nowej linii. */}
              <span className="od-title-tail">
                {titleTail}
                <button
                  type="button"
                  className="iconbtn press od-title-link"
                  data-tip="Otwórz ogłoszenie"
                  data-kbd="O"
                  aria-label="Otwórz oryginalne ogłoszenie (O)"
                  onClick={() => window.open(offer.link, '_blank', 'noopener,noreferrer')}
                >
                  <ArrowUpRight size={18} />
                </button>
              </span>
            </>
          ) : (
            title
          )}
        </h2>
        <div className="od-company-line">
          {[offer.company || '—', offer.location].filter(Boolean).join('  ·  ')}
        </div>

        {/* Karta dopasowania */}
        <MatchCard
          link={offer.link}
          matchPercentage={offer.match_percentage}
          preview={preview}
          still={pipelineRunning}
        />

        {/* Wiersz faktów */}
        {facts.length > 0 && (
          <div className="od-facts" role="group" aria-label="Fakty o ofercie">
            {facts.map((fact, idx) => (
              <div key={idx} className="od-fact">
                <span className="od-fact-well">{fact.icon}</span>
                <span className="od-fact-text">
                  <span className="od-fact-label">{fact.label}</span>
                  <b className="od-fact-val">{fact.value}</b>
                </span>
              </div>
            ))}
          </div>
        )}

        {/* Uzasadnienie dopasowania */}
        {offer.reason && <div className="od-why">{offer.reason}</div>}

        {/* W skrócie: hasła z oceny AI i luki względem CV */}
        {(highlights.length > 0 || hasGaps) && (
          <div className="od-kw">
            <div className="od-kw-head">
              <span>W skrócie</span>
              {hasGaps && (
                <span className="od-kw-legend">
                  <CircleDashed aria-hidden="true" />
                  luka względem CV
                </span>
              )}
            </div>
            <div className="chips">
              {highlights.map((text, idx) => (
                <span key={`h${idx}`} className="chip">
                  {text}
                </span>
              ))}
              {(offer.missing_skills ?? []).map((skill, idx) => (
                <span key={`g${idx}`} className="chip gap">
                  <CircleDashed aria-hidden="true" />
                  {skill}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Przełącznik etapu rekrutacji */}
        {Boolean(offer.status) && !preview && (
          <div className="od-stage-section">
            <span className="od-stage-label">Etap rekrutacji</span>
            <StageSwitch
              value={offer.stage || 'save'}
              onChange={(nextStage) =>
                onUpdateDecision(offer.link, offer.status!, rating || null, nextStage)
              }
            />
            {onSaveNextStep && offer.status !== 'reject' && (
              <NextStepEditor link={offer.link} value={offer.next_step} onSave={onSaveNextStep} />
            )}
          </div>
        )}

        {/* Treść ogłoszenia */}
        {(blocks.length > 0 || offer.raw_description) && (
          <section className="od-desc" aria-label="Treść ogłoszenia">
            <h3>Opis stanowiska</h3>
            {blocks.length > 0 ? (
              blocks.map((block, idx) =>
                block.type === 'list' ? (
                  <ul key={idx}>
                    {(block.items ?? []).map((item, i) => (
                      <li key={i}>{item}</li>
                    ))}
                  </ul>
                ) : (
                  <p key={idx}>{block.text}</p>
                ),
              )
            ) : (
              <p className="od-desc-raw">{offer.raw_description}</p>
            )}
          </section>
        )}
      </div>

      <div className="od-fade" aria-hidden="true" />

      {/* Pływający dok decyzji */}
      {!preview && (
        <DecisionDock
          status={offer.status}
          currentRating={rating}
          initialRating={savedRating}
          onDecide={(status) => decide(status, true)}
          onRatingChange={(newVal) => setRating(newVal)}
          onConfirmRating={confirmRating}
        />
      )}
    </article>
  );
};
