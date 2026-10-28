import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ArrowLeft, ArrowRight, ArrowUpRight, Bookmark, Check, EyeOff, LogOut, Mail, RotateCcw, TriangleAlert, Upload } from 'lucide-react';
import { CompanyLogo } from '../screens/offer/CompanyLogo';
import { Meter } from '../shell/Meter';
import { OFFERS, plural } from '../plural';
import { formatDayLong } from '../format';
import { publicApi, PublicApiError, type Decision, type Me, type ProOffer, type SearchProfile, type SearchState } from './public_api';

export const ACCOUNT_HASH = '#/konto';
export const LOGIN_HASH = '#/zaloguj';
const ACCEPT = '.pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document';
const MAX_BYTES = 5 * 1024 * 1024;
const POLL_MS = 1200;
const PAGE_SIZE = 20;
const SENIORITY: Record<string, string> = {
  intern: 'staż',
  junior: 'junior',
  mid: 'mid',
  senior: 'senior',
  lead: 'lead',
  manager: 'manager',
};

type Tab = 'matched' | 'saved' | 'hidden';
const TABS: { id: Tab; label: string; empty: string }[] = [
  { id: 'matched', label: 'Dopasowane', empty: 'Brak ofert do przejrzenia.' },
  { id: 'saved', label: 'Zapisane', empty: 'Zapisane oferty pojawią się tutaj.' },
  { id: 'hidden', label: 'Ukryte', empty: 'Ukryte oferty pojawią się tutaj.' },
];
const IN_TAB: Record<Tab, (offer: ProOffer) => boolean> = {
  matched: (o) => o.decision === null,
  saved: (o) => o.decision === 'save',
  hidden: (o) => o.decision === 'reject',
};

export function profileTags(profile: SearchProfile | null): string[] {
  if (!profile) return [];
  return [profile.city ?? 'cała Polska', profile.seniority ? SENIORITY[profile.seniority] : '', ...profile.roles.slice(0, 2)].filter(
    Boolean,
  );
}

export function LoginForm({ cta = 'Wyślij link do logowania' }: { cta?: string }) {
  const [email, setEmail] = useState('');
  const [busy, setBusy] = useState(false);
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await publicApi.requestLogin(email.trim());
      setSentTo(email.trim());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  if (sentTo) {
    return (
      <p className="pb-saved">
        <Check />
        Wysłaliśmy link na {sentTo}. Otwórz go na tym urządzeniu.
      </p>
    );
  }
  return (
    <>
      <form className="pb-email-form" onSubmit={submit}>
        <label className="field">
          <Mail />
          <input
            type="email"
            required
            placeholder="twoj@adres.pl"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            aria-label="Adres e-mail"
          />
        </label>
        <button type="submit" className="btn btn-primary" disabled={busy || !email.trim()}>
          {busy ? <span className="spinner" /> : null}
          {cta}
        </button>
      </form>
      {error && (
        <p className="pb-error" role="alert">
          <TriangleAlert />
          {error}
        </p>
      )}
    </>
  );
}

export function LoginView() {
  return (
    <section className="pb-card panel">
      <span className="pb-round-icon">
        <Mail />
      </span>
      <h1 className="title-lg">Zaloguj się albo załóż konto</h1>
      <p className="muted pb-narrow">Podaj e-mail, a wyślemy Ci link do logowania. Bez hasła.</p>
      <LoginForm />
    </section>
  );
}

const verifying = new Map<string, Promise<Me>>();

export function VerifyView({ token, onLogin }: { token: string; onLogin: (me: Me) => void }) {
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let pending = verifying.get(token);
    if (!pending) {
      pending = publicApi.verifyLogin(token).then((r) => r.user);
      verifying.set(token, pending);
    }
    let cancelled = false;
    pending.then(
      (me) => {
        if (cancelled) return;
        onLogin(me);
        window.location.replace(ACCOUNT_HASH);
      },
      (err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [token, onLogin]);

  if (!error) {
    return (
      <section className="pb-card panel">
        <span className="pb-round-icon">
          <span className="spinner" />
        </span>
        <h1 className="title-lg">Logujemy Cię</h1>
      </section>
    );
  }
  return (
    <section className="pb-card panel">
      <span className="pb-round-icon">
        <TriangleAlert />
      </span>
      <h1 className="title-lg">Ten link już nie działa</h1>
      <p className="muted pb-narrow">{error}</p>
      <LoginForm cta="Wyślij nowy link" />
    </section>
  );
}

export function AccountView({ me, onMe }: { me: Me; onMe: (me: Me | null) => void }) {
  const logout = async () => {
    await publicApi.logout().catch(() => null);
    onMe(null);
    window.location.hash = '#/';
  };

  const remove = async () => {
    if (!window.confirm('Usunąć konto razem z CV, wynikami i zapisanymi ofertami? Tego nie da się cofnąć.')) return;
    await publicApi.deleteAccount();
    onMe(null);
    window.location.hash = '#/';
  };

  return (
    <>
      <section className="pb-account-head">
        <div>
          <h1 className="pb-heading">Twoje konto</h1>
          <p className="muted">
            {me.email} · {me.admin ? 'administrator' : me.pro ? 'plan Pro' : 'plan darmowy'}
          </p>
        </div>
        <button type="button" className="btn btn-sm" onClick={logout}>
          <LogOut />
          Wyloguj
        </button>
      </section>
      {me.pro ? <ProPanel me={me} onMe={onMe} /> : <FreePlan />}
      <button type="button" className="link-btn pb-delete" onClick={remove}>
        Usuń konto i wszystkie dane
      </button>
    </>
  );
}

function FreePlan() {
  return (
    <section className="pb-card panel">
      <span className="pb-round-icon">
        <Bookmark />
      </span>
      <h2 className="title-lg">Pełna lista ofert jest w planie Pro</h2>
      <p className="muted pb-narrow">
        W planie Pro porównujemy Twoje CV z całą bazą i oceniamy setki najlepiej pasujących ofert. Możesz je zapisywać
        i ukrywać. Płatności uruchomimy wkrótce.
      </p>
    </section>
  );
}

function ProPanel({ me, onMe }: { me: Me; onMe: (me: Me | null) => void }) {
  const [offers, setOffers] = useState<ProOffer[] | null>(null);
  const [searchId, setSearchId] = useState<string | null>(me.running);
  const [search, setSearch] = useState<SearchState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>('matched');
  const [page, setPage] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const load = async () => {
    const data = await publicApi.proOffers();
    setOffers(data.offers);
    onMe(data.user);
  };

  useEffect(() => {
    load().catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  useEffect(() => {
    if (!searchId) return;
    let cancelled = false;
    let timer = 0;
    const poll = async () => {
      try {
        const next = await publicApi.state(searchId);
        if (cancelled) return;
        setSearch(next);
        if (next.status === 'done' || next.status === 'error') {
          if (next.status === 'error') setError(next.error);
          setSearchId(null);
          await load();
          return;
        }
      } catch (err) {
        if (cancelled) return;
        if (err instanceof PublicApiError && err.status === 404) {
          setSearchId(null);
          return;
        }
      }
      timer = window.setTimeout(poll, POLL_MS);
    };
    poll();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [searchId]);

  const start = async (file: File | null) => {
    if (file && !/\.(pdf|docx)$/i.test(file.name)) {
      setError('Obsługujemy CV w PDF albo DOCX.');
      return;
    }
    if (file && file.size > MAX_BYTES) {
      setError('Plik jest za duży (limit 5 MB).');
      return;
    }
    setError(null);
    setSearch(null);
    try {
      const { id } = await publicApi.proSearch(file);
      setSearchId(id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const decide = async (offer: ProOffer, decision: Decision | null) => {
    const previous = offer.decision;
    const apply = (value: Decision | null) =>
      setOffers((list) => list?.map((o) => (o.link === offer.link ? { ...o, decision: value } : o)) ?? null);
    apply(decision);
    try {
      await publicApi.decide(offer.link, decision);
    } catch (err) {
      apply(previous);
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const busy = !!searchId;
  const visible = (offers ?? []).filter(IN_TAB[tab]);
  const pages = Math.max(1, Math.ceil(visible.length / PAGE_SIZE));
  const current = Math.min(page, pages - 1);
  const tags = profileTags(me.cv);
  const limitReached = me.searches_left === 0;

  return (
    <>
      <section className="pb-cv-bar panel">
        <div className="pb-cv-info">
          <p className="label">Twoje CV</p>
          {me.cv ? (
            <div className="pb-tags">
              {tags.map((tag) => (
                <span key={tag} className="chip">
                  {tag}
                </span>
              ))}
              <span className="faint pb-cv-date">dodane {formatDayLong(new Date(me.cv.at * 1000))}</span>
            </div>
          ) : (
            <p className="muted">Dodaj CV, a porównamy je z całą bazą ofert.</p>
          )}
        </div>
        <div className="pb-cv-actions">
          <button type="button" className="btn btn-sm" disabled={busy || limitReached} onClick={() => inputRef.current?.click()}>
            <Upload />
            {me.cv ? 'Zmień CV' : 'Dodaj CV'}
          </button>
          {me.cv && (
            <button type="button" className="btn btn-sm btn-primary" disabled={busy || limitReached} onClick={() => start(null)}>
              <RotateCcw />
              Szukaj ponownie
            </button>
          )}
        </div>
        {me.searches_left !== null && (
          <p className="faint pb-cv-limit">
            {limitReached ? 'Dzisiejsze wyszukiwania wykorzystane. Wróć jutro.' : `Dziś zostało: ${me.searches_left}`}
          </p>
        )}
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          hidden
          onChange={(e) => {
            start(e.target.files?.[0] ?? null);
            e.target.value = '';
          }}
        />
      </section>

      {error && (
        <p className="pb-error" role="alert">
          <TriangleAlert />
          {error}
        </p>
      )}

      {busy && <ProProgress state={search} />}

      {offers === null ? (
        <div className="empty">
          <span className="spinner" />
        </div>
      ) : offers.length === 0 && !busy ? (
        <div className="empty panel pb-empty">
          <p className="empty-title">Jeszcze nic tu nie ma</p>
          <p>{me.cv ? 'Kliknij „Szukaj ponownie”, żeby ocenić oferty.' : 'Dodaj CV, żeby zacząć.'}</p>
        </div>
      ) : (
        offers.length > 0 && (
          <>
            <div className="pb-tabs segmented" role="tablist">
              {TABS.map(({ id, label }) => (
                <button
                  key={id}
                  type="button"
                  role="tab"
                  className="btn btn-sm"
                  aria-pressed={tab === id}
                  onClick={() => {
                    setTab(id);
                    setPage(0);
                  }}
                >
                  {label}
                  <span className="faint">{offers.filter(IN_TAB[id]).length}</span>
                </button>
              ))}
            </div>
            {visible.length === 0 ? (
              <p className="muted pb-tab-empty">{TABS.find((t) => t.id === tab)!.empty}</p>
            ) : (
              <div className="pb-offers">
                {visible.slice(current * PAGE_SIZE, (current + 1) * PAGE_SIZE).map((offer) => (
                  <ProOfferCard key={offer.link} offer={offer} onDecide={decide} />
                ))}
              </div>
            )}
            {pages > 1 && (
              <nav className="pb-pager" aria-label="Strony">
                <button
                  type="button"
                  className="btn btn-sm btn-icon"
                  disabled={current === 0}
                  aria-label="Poprzednia strona"
                  onClick={() => {
                    setPage(current - 1);
                    window.scrollTo(0, 0);
                  }}
                >
                  <ArrowLeft />
                </button>
                <span className="muted">
                  {current + 1} / {pages}
                </span>
                <button
                  type="button"
                  className="btn btn-sm btn-icon"
                  disabled={current >= pages - 1}
                  aria-label="Następna strona"
                  onClick={() => {
                    setPage(current + 1);
                    window.scrollTo(0, 0);
                  }}
                >
                  <ArrowRight />
                </button>
              </nav>
            )}
          </>
        )
      )}
    </>
  );
}

function ProProgress({ state }: { state: SearchState | null }) {
  const status = state?.status ?? 'queued';
  const scored = state?.progress.scored ?? 0;
  const total = state?.progress.total ?? 0;
  const label =
    status === 'queued'
      ? 'Za chwilę zaczynamy.'
      : status === 'reading' || status === 'profile'
        ? 'Odczytujemy CV.'
        : status === 'ranking'
          ? 'Przeszukujemy całą bazę ofert.'
          : `Oceniamy dopasowanie: ${scored} z ${total} ${plural(total, OFFERS)}.`;
  return (
    <section className="pb-pro-progress panel" aria-live="polite">
      <p>{label}</p>
      <Meter value={status === 'scoring' && total ? scored / total : null} />
    </section>
  );
}

function ProOfferCard({ offer, onDecide }: { offer: ProOffer; onDecide: (offer: ProOffer, d: Decision | null) => void }) {
  const chips = [offer.location, offer.work_mode, offer.salary].filter(Boolean) as string[];
  return (
    <article className="pb-offer pb-pro-offer glass">
      <CompanyLogo url={offer.logo_url} source={offer.source} size="lg" endpoint="/api/public/logo" />
      <div className="pb-offer-body">
        <h3 className="pb-offer-title">
          <a href={offer.link} target="_blank" rel="noopener noreferrer">
            {offer.title}
            <ArrowUpRight className="pb-offer-arrow" aria-label="Otwórz ogłoszenie" />
          </a>
        </h3>
        <p className="pb-offer-company">{offer.company}</p>
        {chips.length > 0 && (
          <div className="pb-chips">
            {chips.map((chip) => (
              <span key={chip} className="chip">
                {chip}
              </span>
            ))}
          </div>
        )}
      </div>
      <div className="pb-offer-score">
        <span className="pb-percent">{offer.percent}%</span>
        <span className="pb-percent-label">dopasowania</span>
      </div>
      <span className="match-track pb-offer-track" style={{ ['--fill' as string]: `${offer.percent}%` }} />
      <div className="pb-pro-actions">
        {offer.decision === null ? (
          <>
            <button type="button" className="btn-quiet" onClick={() => onDecide(offer, 'save')}>
              <Bookmark />
              Zapisz
            </button>
            <button type="button" className="btn-quiet" onClick={() => onDecide(offer, 'reject')}>
              <EyeOff />
              Ukryj
            </button>
          </>
        ) : (
          <button type="button" className="btn-quiet" onClick={() => onDecide(offer, null)}>
            <RotateCcw />
            {offer.decision === 'save' ? 'Usuń z zapisanych' : 'Przywróć'}
          </button>
        )}
      </div>
    </article>
  );
}
