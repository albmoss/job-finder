import { useState, type KeyboardEvent } from 'react';
import { Check, CircleAlert, Link, LoaderCircle } from 'lucide-react';
import { api, errorMessage } from '../../api';
import { useApp } from '../../app_context';
import { Modal } from '../../shell/Modal';
import type { OfferDetail } from '../../types';
import '../../styles/add.css';

interface AddFromLinkModalProps {
  /** 'save' z Zapisanych, 'apply' z Aplikacji („Dodaj aplikację”). */
  status: 'save' | 'apply';
  onClose: () => void;
  onSaved: (offer: OfferDetail) => void;
}

const DESCRIPTION_PLACEHOLDER = 'Brak opisu (Opcja dodana ręcznie z linku)';
const LOCATION_PLACEHOLDER = 'Remote / Nieznana';
const FAILED_TITLE_PREFIX = 'Błąd pobierania';

/** Okno „Dodaj z linku”: odczyt oferty z adresu, edycja pól, zapis z decyzją. */
export function AddFromLinkModal({ status, onClose, onSaved }: AddFromLinkModalProps) {
  const { toast, bumpData } = useApp();
  const [url, setUrl] = useState('');
  const [fetchedUrl, setFetchedUrl] = useState('');
  const [title, setTitle] = useState('');
  const [company, setCompany] = useState('');
  const [location, setLocation] = useState('');
  const [description, setDescription] = useState('');
  const [source, setSource] = useState('');
  const [fetching, setFetching] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const link = url.trim();
  const canSave = !saving && !fetching && /^https?:\/\/\S+$/i.test(link) && !!title.trim() && !!company.trim();

  const fetchLink = async () => {
    if (!link || fetching) return;
    setFetching(true);
    setError('');
    try {
      const { data } = await api.fetchLinkData(link);
      const failed = (data.title ?? '').startsWith(FAILED_TITLE_PREFIX);
      setTitle(failed ? '' : (data.title ?? ''));
      setCompany(data.company ?? '');
      setLocation(data.location && data.location !== LOCATION_PLACEHOLDER ? data.location : '');
      setDescription(data.description && data.description !== DESCRIPTION_PLACEHOLDER ? data.description : '');
      setSource(data.source ?? '');
      setFetchedUrl(link);
      if (failed) setError('Nie udało się odczytać oferty z tego adresu. Uzupełnij dane ręcznie.');
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setFetching(false);
    }
  };

  const save = async () => {
    if (!canSave) return;
    setSaving(true);
    setError('');
    try {
      const res = await api.saveManualJob({
        title: title.trim(),
        company: company.trim(),
        link,
        description: description.trim(),
        source: source || 'Manual',
        location: location.trim(),
        status,
      });
      bumpData();
      toast(status === 'apply' ? 'Dodano aplikację.' : 'Zapisano ofertę.');
      onSaved(res.offer);
      onClose();
    } catch (err) {
      setError(errorMessage(err));
      setSaving(false);
    }
  };

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      save();
    }
  };

  const busy = fetching || saving;

  return (
    <Modal title={status === 'apply' ? 'Dodaj aplikację' : 'Dodaj z linku'} onClose={onClose}>
      <div className="add-body" onKeyDown={onKeyDown}>
        <p className="muted">Wklej adres ogłoszenia. Uzupełnimy dane, a Ty możesz je poprawić przed zapisem.</p>
        <div className="add-url">
          <div className="field">
            <Link />
            <input
              autoFocus
              value={url}
              placeholder="https://…"
              aria-label="Adres ogłoszenia"
              disabled={saving}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.ctrlKey && !e.metaKey) {
                  e.preventDefault();
                  fetchLink();
                }
              }}
            />
          </div>
          <button type="button" className="btn" disabled={!link || busy || link === fetchedUrl} onClick={fetchLink}>
            {fetching ? <LoaderCircle className="spin" /> : <Check />}
            {fetching ? 'Pobieramy…' : 'Pobierz dane'}
          </button>
        </div>
        {error && (
          <p className="add-error warn">
            <CircleAlert />
            {error}
          </p>
        )}
        <div className="add-grid">
          <label className="field-stack">
            <span className="label">Stanowisko</span>
            <input className="field" value={title} disabled={busy} onChange={(e) => setTitle(e.target.value)} />
          </label>
          <label className="field-stack">
            <span className="label">Firma</span>
            <input className="field" value={company} disabled={busy} onChange={(e) => setCompany(e.target.value)} />
          </label>
          <label className="field-stack add-wide">
            <span className="label">Lokalizacja</span>
            <input
              className="field"
              value={location}
              placeholder="Warszawa"
              disabled={busy}
              onChange={(e) => setLocation(e.target.value)}
            />
          </label>
          <label className="field-stack add-wide">
            <span className="label">Opis</span>
            <textarea
              className="field add-description"
              value={description}
              disabled={busy}
              onChange={(e) => setDescription(e.target.value)}
            />
          </label>
        </div>
        <div className="modal-actions">
          <button type="button" className="btn btn-primary" disabled={!canSave} onClick={save}>
            {saving ? <LoaderCircle className="spin" /> : <Check />}
            Zapisz
          </button>
          <button type="button" className="btn" disabled={saving} onClick={onClose}>
            Anuluj
          </button>
          <span className="faint add-hint">Ctrl+Enter zapisuje</span>
        </div>
      </div>
    </Modal>
  );
}
