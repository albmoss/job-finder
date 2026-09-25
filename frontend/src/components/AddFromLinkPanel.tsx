import React, { useState, useMemo, useEffect, useCallback } from 'react';
import type { OfferDetail } from '../types';
import { api } from '../api';
import { OfferDetailPanel } from './OfferDetailPanel';
import { Link2, ArrowDownToLine, CircleCheck, CircleAlert, Database } from 'lucide-react';
import { CHARS, plural } from '../plural';
import '../styles/tools.css';

interface AddFromLinkPanelProps {
  offer: OfferDetail | null;
  onOfferSaved: (offer: OfferDetail) => void;
  onCloseOffer: () => void;
  showToast: (msg: string) => void;
  onUpdateDecision: (link: string, status: string, rating: number | null, stage?: string) => void;
  onRestoreDecision: (link: string) => void;
  onDeleteOffer: (link: string) => void;
  onSaveNextStep: (link: string, label: string, due: string | null) => Promise<boolean>;
  pipelineRunning: boolean;
}

interface Draft {
  title: string;
  company: string;
  location: string;
  source: string;
  description: string;
  link: string;
}

export const AddFromLinkPanel: React.FC<AddFromLinkPanelProps> = ({
  offer,
  onOfferSaved,
  onCloseOffer,
  showToast,
  onUpdateDecision,
  onRestoreDecision,
  onDeleteOffer,
  onSaveNextStep,
  pipelineRunning,
}) => {
  const [url, setUrl] = useState('');
  const [fetching, setFetching] = useState(false);
  const [saving, setSaving] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null);

  const handleFetch = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmedUrl = url.trim();
    if (!trimmedUrl) return;

    setFetching(true);
    try {
      const res = await api.fetchLinkData(trimmedUrl);
      if (res.ok && res.data) {
        setDraft({
          title: res.data.title || '',
          company: res.data.company || '',
          location: res.data.location || 'Warszawa',
          source: res.data.source || 'Manual',
          description: res.data.description || '',
          link: trimmedUrl,
        });
        onCloseOffer();
      }
    } catch (err: unknown) {
      showToast(`Błąd pobierania danych: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setFetching(false);
    }
  };

  const handleClear = useCallback(() => {
    setUrl('');
    setDraft(null);
    onCloseOffer();
  }, [onCloseOffer]);

  const missingRequired = draft !== null && (!draft.title.trim() || !draft.company.trim());

  const handleSave = useCallback(async () => {
    if (!draft || !draft.title.trim() || !draft.company.trim() || saving) return;
    setSaving(true);
    try {
      const res = await api.saveManualJob(draft);
      if (res.ok) {
        showToast(res.message);
        setDraft(null);
        setUrl('');
        onOfferSaved(res.offer);
      }
    } catch (err: unknown) {
      showToast(`Błąd zapisu: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setSaving(false);
    }
  }, [draft, saving, showToast, onOfferSaved]);

  // Skrót Ctrl+Enter do zapisu formularza
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        if (draft && !missingRequired && !saving) {
          e.preventDefault();
          handleSave();
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [draft, missingRequired, saving, handleSave]);

  // Liczba uzupełnionych pól z 6: title, company, location, source, description, link
  const filledCount = useMemo(() => {
    if (!draft) return 0;
    let count = 0;
    if (draft.title.trim()) count++;
    if (draft.company.trim()) count++;
    if (draft.location.trim() && !draft.location.includes('Nieznana')) count++;
    if (draft.source.trim()) count++;
    if (draft.description.trim() && !draft.description.startsWith('Brak opisu')) count++;
    if (draft.link.trim()) count++;
    return count;
  }, [draft]);

  // Obiekt OfferDetail na potrzeby podglądu (preview) szkicu
  const draftAsOfferDetail: OfferDetail | null = useMemo(() => {
    if (!draft) return null;
    const blocks: OfferDetail['description_blocks'] = draft.description
      ? draft.description
          .split(/\n\s*\n/)
          .map((p) => p.trim())
          .filter(Boolean)
          .map((text) => ({ type: 'p' as const, text }))
      : [];

    return {
      link: draft.link,
      title: draft.title || 'Bez tytułu',
      company: draft.company || '—',
      location: draft.location,
      source: draft.source || 'Manual',
      source_color: '',
      is_gone: false,
      work_mode: '',
      match_percentage: null,
      reason: null,
      industry: null,
      is_entry_level: false,
      learnable_in_month: false,
      missing_skills: [],
      description_blocks: blocks,
      raw_description: draft.description,
      status: null,
      rating: null,
      stage: 'save',
      decided_at: null,
      applied_at: null,
      dot_color: null,
      dot_label: null,
      next_step: null,
      highlights: [],
    };
  }, [draft]);

  return (
    <>
      {/* Lewy panel formularza */}
      <section className="panel glass afl-left" aria-label="Dodaj ofertę z linku">
        <div className="panel-scroll afl-left-scroll">
          {/* Nagłówek */}
          <div className="panel-head">
            <div>
              <h1>
                Dodaj z linku
                <Link2 aria-hidden="true" />
              </h1>
              <p>oferta spoza skraperów — ze strony firmy, z polecenia</p>
            </div>
          </div>

          {/* Pole URL */}
          <form className="afl-fetch-box" onSubmit={handleFetch}>
            <div className="afl-url-field">
              <Link2 size={16} className="afl-url-icon" aria-hidden="true" />
              <input
                id="add-url"
                className="afl-url-input mono"
                type="url"
                placeholder="https://…"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                disabled={fetching}
                aria-label="Adres ogłoszenia"
              />
              <button
                type="submit"
                className="btn btn-primary afl-fetch-btn press"
                disabled={fetching || !url.trim()}
              >
                <ArrowDownToLine size={16} aria-hidden="true" />
                {fetching ? 'Czytam…' : 'Pobierz'}
              </button>
            </div>

            {draft && (
              <div className="afl-fetch-result">
                <CircleCheck size={13} className="afl-check-icon" aria-hidden="true" />
                <span className="mono">
                  przeczytano stronę  ·  {filledCount} z 6 pól uzupełnionych
                </span>
              </div>
            )}
          </form>

          {/* Formularz lub tekst pomocniczy */}
          {draft ? (
            <div className="afl-fields">
              <div className="section-label">
                <span>Sprawdź przed zapisem</span>
                <span className="aside">* wymagane</span>
              </div>

              {/* Stanowisko */}
              <div className={`field ${!draft.title.trim() ? 'is-missing' : ''}`}>
                <label className="field-label" htmlFor="draft-title">
                  Stanowisko *
                </label>
                <input
                  id="draft-title"
                  className="input"
                  value={draft.title}
                  onChange={(e) => setDraft({ ...draft, title: e.target.value })}
                  placeholder="np. Junior UX/UI Designer"
                />
                {!draft.title.trim() && (
                  <div className="field-help">
                    <CircleAlert size={13} aria-hidden="true" />
                    <span>Bez stanowiska oferty nie znajdziesz na liście</span>
                  </div>
                )}
              </div>

              {/* Firma */}
              <div className={`field ${!draft.company.trim() ? 'is-missing' : ''}`}>
                <label className="field-label" htmlFor="draft-company">
                  Firma *
                </label>
                <input
                  id="draft-company"
                  className="input"
                  value={draft.company}
                  onChange={(e) => setDraft({ ...draft, company: e.target.value })}
                  placeholder="np. Acme Corp"
                />
                {!draft.company.trim() && (
                  <div className="field-help">
                    <CircleAlert size={13} aria-hidden="true" />
                    <span>Bez firmy oferty nie znajdziesz na liście</span>
                  </div>
                )}
              </div>

              {/* Lokalizacja i Portal w jednym wierszu */}
              <div className="afl-row">
                <div className="field">
                  <label className="field-label" htmlFor="draft-location">
                    Lokalizacja
                  </label>
                  <input
                    id="draft-location"
                    className="input"
                    value={draft.location}
                    onChange={(e) => setDraft({ ...draft, location: e.target.value })}
                    placeholder="np. Warszawa"
                  />
                </div>
                <div className="field">
                  <label className="field-label" htmlFor="draft-source">
                    Portal
                  </label>
                  <input
                    id="draft-source"
                    className="input"
                    value={draft.source}
                    onChange={(e) => setDraft({ ...draft, source: e.target.value })}
                    placeholder="np. Pracuj.pl"
                  />
                </div>
              </div>

              {/* Opis */}
              <div className="field">
                <div className="afl-field-head">
                  <label className="field-label" htmlFor="draft-description">
                    Opis
                  </label>
                  <span className="afl-char-count mono">
                    {draft.description.length.toLocaleString('pl-PL')}{' '}
                    {plural(draft.description.length, CHARS)}
                  </span>
                </div>
                <textarea
                  id="draft-description"
                  className="textarea afl-textarea"
                  rows={7}
                  value={draft.description}
                  onChange={(e) => setDraft({ ...draft, description: e.target.value })}
                  placeholder="Treść ogłoszenia…"
                />
              </div>

              <div className="afl-spacer" />

              {/* Akcje zapisu */}
              <div className="afl-actions">
                <button
                  type="button"
                  className="btn btn-primary press"
                  disabled={saving || missingRequired}
                  onClick={handleSave}
                >
                  <Database size={16} aria-hidden="true" />
                  {saving ? 'Zapisuję…' : 'Zapisz w bazie'}
                </button>
                <button
                  type="button"
                  className="btn btn-quiet press"
                  onClick={handleClear}
                >
                  Wyczyść
                </button>
                <div className="afl-shortcut" title="Skrót zapisu: Ctrl + Enter">
                  <span className="kbd">Ctrl</span>
                  <span className="kbd">↵</span>
                </div>
              </div>
            </div>
          ) : (
            <div className="afl-hint-box">
              <p className="afl-hint-text">
                Skrapery nie sięgają wszędzie. Ofertę spoza nich — ze strony firmy, z polecenia — wklej tutaj.
                Aplikacja przeczyta stronę i wypełni pola, a Ty poprawisz je przed zapisem. Dodana oferta zachowuje
                się jak każda inna: można ją ocenić, zapisać i odrzucić.
              </p>
            </div>
          )}
        </div>
      </section>

      {/* Prawy panel: podgląd oferty / szkicu / pusty stan */}
      {offer ? (
        <OfferDetailPanel
          offer={offer}
          onClose={onCloseOffer}
          onUpdateDecision={onUpdateDecision}
          onRestoreDecision={onRestoreDecision}
          onDeleteOffer={onDeleteOffer}
          onSaveNextStep={onSaveNextStep}
          pipelineRunning={pipelineRunning}
        />
      ) : draftAsOfferDetail ? (
        <OfferDetailPanel
          offer={draftAsOfferDetail}
          preview
          onClose={handleClear}
          onUpdateDecision={onUpdateDecision}
          onRestoreDecision={onRestoreDecision}
          onDeleteOffer={onDeleteOffer}
          pipelineRunning={pipelineRunning}
        />
      ) : (
        <section className="panel glass afl-empty-panel" aria-label="Podgląd">
          <div className="empty-state">
            <div className="well">
              <Link2 size={18} aria-hidden="true" />
            </div>
            <div className="text">
              <strong>Wklej adres po lewej</strong>
              <span>Tu pojawi się podgląd, a po zapisie — oferta gotowa do oceny.</span>
            </div>
          </div>
        </section>
      )}
    </>
  );
};
