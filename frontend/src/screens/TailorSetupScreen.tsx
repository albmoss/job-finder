import { useEffect, useState } from 'react';
import { Check, CircleAlert, FilePenLine, FileText, ListRestart, LockKeyhole, Settings2, Sparkles, type LucideIcon } from 'lucide-react';
import { api, errorMessage } from '../api';
import { useApp } from '../app_context';
import { navigate, paths } from '../router';
import { Modal } from '../shell/Modal';
import type { OfferDetail } from '../types';
import { LANGUAGE_LABEL, TailorHeader, offerPath, stepsFor } from './tailor/common';
import type { ScreenProps } from './types';
import '../styles/tailor.css';

const FACTS_PLACEHOLDER = 'Np. w projekcie portfolio samodzielnie wdrożyłam formularz w React i obsługę błędów walidacji.';

export function TailorSetupScreen({ route }: ScreenProps) {
  const link = route.query.get('oferta') ?? '';
  const { cv, openSettings, bumpData } = useApp();
  const [offer, setOffer] = useState<OfferDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [language, setLanguage] = useState<'pl' | 'en'>('pl');
  const [facts, setFacts] = useState('');
  const [lockContact, setLockContact] = useState(true);
  const [keepOrder, setKeepOrder] = useState(true);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState(false);

  useEffect(() => {
    if (!link) {
      setLoadError('Nie wybrano oferty.');
      return;
    }
    let alive = true;
    api
      .getOfferDetail(link)
      .then(async (detail) => {
        if (!alive) return;
        setOffer(detail);
        const previous = detail.cv_versions[0];
        if (!previous) return;
        const settings = await api.getCvVersion(previous.id);
        if (!alive) return;
        setLanguage(settings.language);
        setFacts(settings.facts);
        setLockContact(settings.lock_contact);
        setKeepOrder(settings.keep_order);
      })
      .catch((err) => alive && setLoadError(errorMessage(err)));
    return () => {
      alive = false;
    };
  }, [link]);

  const back = () => navigate(offer ? offerPath(offer.status, offer.link) : paths.matched(link || undefined));

  const create = async () => {
    if (!offer || !consent || busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.createCvVersion({
        link: offer.link,
        language,
        facts,
        lock_contact: lockContact,
        keep_order: keepOrder,
        consent: true,
      });
      bumpData();
      navigate(paths.cvVersion(res.version.id));
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  };

  if (loadError || !offer) {
    return (
      <div className="tl-screen">
        <TailorHeader backLabel="Wróć do oferty" onBack={back} title="CV pod tę ofertę" steps={stepsFor('settings')} />
        <section className="panel tl-fill">
          {loadError ? (
            <div className="empty">
              <CircleAlert />
              <p className="empty-title">Nie udało się wczytać oferty</p>
              <p>{loadError}</p>
            </div>
          ) : (
            <div className="empty">
              <p>Wczytywanie…</p>
            </div>
          )}
        </section>
      </div>
    );
  }

  const requirements = (offer.fields.skills_required ?? []).filter(Boolean).join(', ');
  const company = offer.company || 'tej firmy';
  const errorNeedsSettings = error !== null && /klucz|ustawieni|model/i.test(error);
  const errorNeedsCv = error !== null && /Brak CV/i.test(error);

  return (
    <div className="tl-screen">
      <TailorHeader
        backLabel="Wróć do oferty"
        onBack={back}
        title="CV pod tę ofertę"
        subtitle={`${offer.title} w ${offer.company}`}
        steps={stepsFor('settings')}
      />
      <div className="tl-setup">
        <aside className="panel tl-context scroll">
          <FilePenLine className="tl-context-icon" />
          <h2 className="tl-offer-title">{offer.title}</h2>
          <p className="tl-company">{offer.company}</p>
          {requirements && (
            <div className="tl-group">
              <p className="tl-small">Wymagania z ogłoszenia</p>
              <p className="tl-skills">{requirements}</p>
            </div>
          )}
          <p className="tl-small">Bazowe CV</p>
          <p className="tl-file">{cv?.filename || 'cv.pdf'}</p>
          <p className="tl-copy">Utworzymy osobną wersję dla {company}. Bazowe CV nie zostanie zmienione.</p>
          <button type="button" className="btn" onClick={() => setPreview(true)}>
            <FileText />
            Podgląd bazowego CV
          </button>
          <div className="tl-spacer" />
          <p className="tl-small">Stałe zasady dopasowania są zapisane w instrukcjach agenta.</p>
          <button type="button" className="btn" onClick={() => navigate(paths.instructions)}>
            <Settings2 />
            Instrukcje agenta
          </button>
        </aside>

        <section className="panel tl-options scroll">
          <h2 className="tl-options-title">Co uwzględnić w tej wersji?</h2>
          <div className="tl-language">
            <span id="tl-language-label">Język CV</span>
            <div className="segmented" role="radiogroup" aria-labelledby="tl-language-label">
              {(['pl', 'en'] as const).map((code) => (
                <button
                  key={code}
                  type="button"
                  className="btn"
                  role="radio"
                  aria-checked={language === code}
                  onClick={() => setLanguage(code)}
                >
                  {language === code && <Check />}
                  {LANGUAGE_LABEL[code]}
                </button>
              ))}
            </div>
          </div>
          <label className="tl-facts-label" htmlFor="tl-facts">
            Dodatkowe fakty do tej oferty
          </label>
          <p className="tl-small">Uzupełnij doświadczenie, którego nie ma w bazowym CV. To pole jest opcjonalne.</p>
          <textarea
            id="tl-facts"
            className="tl-facts"
            value={facts}
            placeholder={FACTS_PLACEHOLDER}
            onChange={(e) => setFacts(e.target.value)}
          />
          <div className="tl-protections">
            <Toggle icon={LockKeyhole} label="Dane kontaktowe i daty chronione" on={lockContact} onChange={setLockContact} />
            <Toggle icon={ListRestart} label="Zachowaj kolejność sekcji" on={keepOrder} onChange={setKeepOrder} />
          </div>
          <p className="tl-copy">
            Dostaniesz podgląd zmian i listę informacji do sprawdzenia. Niepotwierdzone liczby trzeba poprawić lub usunąć przed
            pobraniem CV.
          </p>
          <div className="tl-spacer" />
          {error && (
            <p className="tl-error" role="alert">
              <CircleAlert />
              <span>
                {error}
                {errorNeedsSettings && (
                  <>
                    {' '}
                    <button type="button" className="link-btn" onClick={openSettings}>
                      Ustawienia
                    </button>
                  </>
                )}
                {errorNeedsCv && (
                  <>
                    {' '}
                    <a className="link-btn" href={`#${paths.cv}`}>
                      Moje CV
                    </a>
                  </>
                )}
              </span>
            </p>
          )}
          <label className="check tl-consent">
            <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
            Zgadzam się na wysłanie CV i oferty do modelu oraz koszt API.
          </label>
          <div className="tl-submit">
            <p className="tl-tiny">Nie wysyłamy aplikacji za Ciebie.</p>
            <button type="button" className="btn btn-primary" disabled={!consent || busy} onClick={create}>
              <Sparkles className={busy ? 'spin' : undefined} />
              Utwórz wersję CV
            </button>
          </div>
        </section>
      </div>

      {preview && (
        <Modal title="Bazowe CV" wide onClose={() => setPreview(false)}>
          {cv?.pdf_url ? (
            <iframe className="tl-base-pdf" src={cv.pdf_url} title="Bazowe CV" />
          ) : cv?.text ? (
            <pre className="tl-base-text scroll">{cv.text}</pre>
          ) : (
            <p className="muted">Brak bazowego CV.</p>
          )}
        </Modal>
      )}
    </div>
  );
}

interface ToggleProps {
  icon: LucideIcon;
  label: string;
  on: boolean;
  onChange: (on: boolean) => void;
}

function Toggle({ icon: Icon, label, on, onChange }: ToggleProps) {
  return (
    <button type="button" className="tl-toggle" role="switch" aria-checked={on} onClick={() => onChange(!on)}>
      <Icon />
      <span>{label}</span>
      <i className="tl-switch" aria-hidden="true" />
    </button>
  );
}
