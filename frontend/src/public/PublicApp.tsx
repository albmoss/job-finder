import { useEffect, useRef, useState, type DragEvent, type FormEvent } from 'react';
import { ArrowLeft, ArrowUpRight, Check, Lock, Mail, RotateCcw, TriangleAlert, Upload } from 'lucide-react';
import { CompanyLogo } from '../screens/offer/CompanyLogo';
import { LogoMark } from '../shell/LogoMark';
import { OFFERS, plural } from '../plural';
import { publicApi, PublicApiError, type LegalInfo, type PublicPick, type SearchProfile, type SearchState } from './public_api';
import aplikujLogo from './logos/aplikuj.svg';
import goworkLogo from './logos/gowork.svg';
import justjoinLogo from './logos/justjoin.svg';
import nofluffjobsLogo from './logos/nofluffjobs.svg';
import olxLogo from './logos/olx.svg';
import pracaPlLogo from './logos/praca_pl.svg';
import pracujLogo from './logos/pracuj.svg';
import rocketjobsLogo from './logos/rocketjobs.svg';
import solidjobsLogo from './logos/solidjobs.svg';

const POLL_MS = 1200;
const ACCEPT = '.pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document';
const MAX_BYTES = 5 * 1024 * 1024;
const SENIORITY: Record<string, string> = {
  intern: 'staż',
  junior: 'junior',
  mid: 'mid',
  senior: 'senior',
  lead: 'lead',
  manager: 'manager',
};
const PRIVACY_HASH = '#/prywatnosc';
const TERMS_HASH = '#/regulamin';
const DEMO_OFFERS = [
  { logo: 'B', title: 'Specjalistka ds. marketingu', company: 'Brightway, Warszawa', percent: 94 },
  { logo: 'N', title: 'Content Marketing Manager', company: 'Nordlake, zdalnie', percent: 89 },
  { logo: 'K', title: 'Brand Specialist', company: 'Kwadrat Studio, Kraków', percent: 86 },
];

function legalPage(): 'privacy' | 'terms' | null {
  const hash = window.location.hash;
  return hash === PRIVACY_HASH ? 'privacy' : hash === TERMS_HASH ? 'terms' : null;
}

function resultIdFromHash(): string | null {
  const match = window.location.hash.match(/^#\/wynik\/([\w-]+)$/);
  return match ? match[1] : null;
}

function profileTags(profile: SearchProfile | null): string[] {
  if (!profile) return [];
  return [profile.city ?? 'cała Polska', profile.seniority ? SENIORITY[profile.seniority] : '', ...profile.roles.slice(0, 2)].filter(
    Boolean,
  );
}

export function PublicApp() {
  const [legal, setLegal] = useState<LegalInfo | null>(null);
  const [searchId, setSearchId] = useState<string | null>(resultIdFromHash);
  const [state, setState] = useState<SearchState | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [page, setPage] = useState(legalPage);

  useEffect(() => {
    publicApi.legal().then(setLegal).catch(() => setLegal(null));
    const onHash = () => {
      setPage(legalPage());
      window.scrollTo(0, 0);
    };
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  useEffect(() => {
    if (!searchId) return;
    let cancelled = false;
    let timer = 0;
    const poll = async () => {
      try {
        const next = await publicApi.state(searchId);
        if (cancelled) return;
        setState(next);
        if (next.status === 'done' || next.status === 'error') return;
      } catch (err) {
        if (cancelled) return;
        if (err instanceof PublicApiError && err.status === 404) {
          setNotice('Ten wynik wygasł. Wgraj CV jeszcze raz.');
          reset();
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

  const begin = (id: string) => {
    setNotice(null);
    setState(null);
    window.history.replaceState(null, '', `#/wynik/${id}`);
    setSearchId(id);
  };

  const reset = () => {
    window.history.replaceState(null, '', window.location.pathname);
    setSearchId(null);
    setState(null);
  };

  let body;
  const backHash = searchId ? `#/wynik/${searchId}` : '#/';
  if (page === 'privacy') body = <PrivacyView legal={legal} backHash={backHash} />;
  else if (page === 'terms') body = <TermsView legal={legal} backHash={backHash} />;
  else if (!searchId) body = <UploadView notice={notice} onStarted={begin} />;
  else if (state?.status === 'done' && state.result) body = <ResultView state={state} />;
  else if (state?.status === 'error') body = <ErrorView message={state.error} onReset={reset} />;
  else body = <ProgressView state={state} />;

  const start = !page && !searchId;
  return (
    <div className={`pb${start ? ' is-wide' : ''}`}>
      <header className="pb-header">
        <a className="pb-brand" href="/" aria-label="traffy.pl">
          <LogoMark />
          <span>
            traffy<span className="pb-brand-tld">.pl</span>
          </span>
        </a>
      </header>
      <main className="pb-main">{body}</main>
      <footer className="pb-footer label">
        {state?.status === 'done' && !page && <span>Ogłoszenie otwiera się na portalu, na którym zostało opublikowane.</span>}
        <nav className="pb-footer-links">
          <a href={TERMS_HASH}>Regulamin</a>
          <a href={PRIVACY_HASH}>Polityka prywatności</a>
        </nav>
      </footer>
    </div>
  );
}

function UploadView({ notice, onStarted }: { notice: string | null; onStarted: (id: string) => void }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(notice);
  const [freeUsed, setFreeUsed] = useState(false);
  const touch = window.matchMedia('(pointer: coarse)').matches;

  const pick = async (file: File | undefined) => {
    if (!file || busy) return;
    if (!/\.(pdf|docx)$/i.test(file.name)) {
      setError('Obsługujemy CV w PDF albo DOCX.');
      return;
    }
    if (file.size > MAX_BYTES) {
      setError('Plik jest za duży (limit 5 MB).');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const { id } = await publicApi.start(file);
      onStarted(id);
    } catch (err) {
      if (err instanceof PublicApiError && err.code === 'free_used') setFreeUsed(true);
      else setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };

  const choose = () => {
    if (!busy) inputRef.current?.click();
  };

  const dropProps = {
    onDragOver: (e: DragEvent<HTMLElement>) => {
      e.preventDefault();
      setDragging(true);
    },
    onDragLeave: () => setDragging(false),
    onDrop: (e: DragEvent<HTMLElement>) => {
      e.preventDefault();
      setDragging(false);
      pick(e.dataTransfer.files[0]);
    },
  };

  if (freeUsed) {
    return (
      <section className="pb-card panel">
        <span className="pb-round-icon">
          <Lock />
        </span>
        <h1 className="title-lg">Bezpłatne wyszukiwanie zostało już wykorzystane</h1>
        <p className="muted pb-narrow">
          Z tego adresu wykonano już bezpłatne wyszukiwanie. Kolejne będą dostępne w płatnej wersji. Zostaw e-mail, a
          powiadomimy Cię o starcie.
        </p>
        <WaitlistForm />
      </section>
    );
  }

  return (
    <div className="pb-home">
      <section className={`pb-start${dragging ? ' is-dragging' : ''}`} aria-label="Dodaj CV" {...dropProps}>
        <div className="pb-start-copy">
          <h1 className="pb-display">
            Wrzuć CV. <span>Dostaniesz oferty, które pasują.</span>
          </h1>
          <div
            className={`pb-drop${dragging ? ' is-dragging' : ''}${busy ? ' is-busy' : ''}`}
            role="button"
            tabIndex={0}
            aria-label="Wybierz plik z CV"
            onClick={choose}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                choose();
              }
            }}
          >
            <span className="pb-round-icon">{busy ? <span className="spinner" /> : <Upload />}</span>
            <span className="pb-drop-text">
              <strong>{busy ? 'Wysyłamy CV' : touch ? 'Wybierz swoje CV' : 'Upuść tutaj swoje CV'}</strong>
              <span>PDF albo DOCX, do 5 MB</span>
            </span>
            <span className="btn btn-primary pb-drop-btn" aria-hidden="true">
              Wybierz plik
            </span>
          </div>
          <p className="pb-terms">
            Dodając CV, akceptujesz <a href={TERMS_HASH}>regulamin</a> i{' '}
            <a href={PRIVACY_HASH}>zasady przetwarzania danych</a>.
          </p>
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPT}
            hidden
            onChange={(e) => {
              pick(e.target.files?.[0]);
              e.target.value = '';
            }}
          />
          {error && (
            <p className="pb-error" role="alert">
              <TriangleAlert />
              {error}
            </p>
          )}
        </div>
        <MatchDemo />
      </section>
      <div className="pb-sources">
        <span className="pb-sources-label">Oferty z portali</span>
        <div className="pb-marquee">
          <ul className="pb-marquee-track">
            {[...SOURCES, ...SOURCES].map(({ name, src, height }, i) => (
              <li key={i} aria-hidden={i >= SOURCES.length || undefined}>
                <img src={src} alt={name} height={height} draggable={false} />
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}

const SOURCES = [
  { name: 'Pracuj.pl', src: pracujLogo, height: 22 },
  { name: 'OLX', src: olxLogo, height: 21 },
  { name: 'JustJoin.it', src: justjoinLogo, height: 21 },
  { name: 'RocketJobs', src: rocketjobsLogo, height: 21 },
  { name: 'aplikuj.pl', src: aplikujLogo, height: 21 },
  { name: 'praca.pl', src: pracaPlLogo, height: 22 },
  { name: 'GoWork.pl', src: goworkLogo, height: 24 },
  { name: 'SOLID.Jobs', src: solidjobsLogo, height: 17 },
  { name: 'No Fluff Jobs', src: nofluffjobsLogo, height: 30 },
];

function MatchDemo() {
  return (
    <div className="pb-demo" aria-hidden="true">
      <div className="pb-demo-cv">
        <div className="pb-demo-person">
          <span className="pb-demo-avatar" />
          <div>
            <strong>Anna Nowak</strong>
            <span>Specjalistka ds. marketingu</span>
          </div>
        </div>
        {[92, 78, 85, 60, 0, 88, 70, 82, 54, 0, 76, 90, 64].map((width, i) =>
          width ? <i key={i} style={{ width: `${width}%` }} /> : <b key={i} />,
        )}
        <span className="pb-demo-scan" />
      </div>
      <div className="pb-demo-offers">
        {DEMO_OFFERS.map((offer) => (
          <div key={offer.title} className="pb-demo-offer glass">
            <span className="pb-demo-logo">{offer.logo}</span>
            <div className="pb-demo-text">
              <strong>{offer.title}</strong>
              <span>{offer.company}</span>
            </div>
            <span className="pb-demo-percent">{offer.percent}%</span>
            <span className="pb-demo-bar">
              <i style={{ ['--fill' as string]: offer.percent / 100 }} />
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

type StepState = 'done' | 'active' | 'todo';

function ProgressView({ state }: { state: SearchState | null }) {
  const status = state?.status ?? 'queued';
  const good = state?.progress.good ?? 0;
  const order = ['reading', 'profile', 'ranking', 'triage', 'scoring'];
  const at = order.indexOf(status);
  const stepState = (from: number, to: number): StepState => (at > to ? 'done' : at >= from ? 'active' : 'todo');
  const tags = profileTags(state?.profile ?? null);

  const steps: { title: string; detail: string | null; state: StepState }[] = [
    { title: 'Odczytujemy doświadczenie i umiejętności z CV', detail: tags.join(' · ') || null, state: stepState(0, 1) },
    { title: 'Wybieramy oferty pasujące do Twojego profilu', detail: null, state: stepState(2, 3) },
    {
      title: 'Analizujemy wymagania i oceniamy dopasowanie',
      detail: good > 0 ? `Mamy już ${good} z 3.` : null,
      state: stepState(4, 4),
    },
  ];

  return (
    <section className="pb-card panel pb-progress" aria-live="polite">
      <h1 className="title-lg">Szukamy ofert dla Twojego doświadczenia</h1>
      {status === 'queued' && state && state.queue > 0 && <p className="label">Za chwilę zaczynamy, jesteś w kolejce.</p>}
      <ol className="pb-steps">
        {steps.map((step) => (
          <li key={step.title} className={`pb-step is-${step.state}`}>
            <span className="pb-step-mark">
              {step.state === 'done' ? <Check /> : step.state === 'active' ? <span className="spinner" /> : null}
            </span>
            <div>
              <p className="pb-step-title">{step.title}</p>
              {step.detail && step.state !== 'todo' && <p className="label">{step.detail}</p>}
            </div>
          </li>
        ))}
      </ol>
      <span className="meter is-indeterminate">
        <span className="meter-fill" />
      </span>
      <p className="label">Kończymy, gdy trzy oferty naprawdę pasują.</p>
    </section>
  );
}

function OfferCard({ pick }: { pick: PublicPick }) {
  const chips = [pick.location, pick.work_mode, pick.salary].filter(Boolean) as string[];
  return (
    <a className="pb-offer glass" href={pick.link} target="_blank" rel="noopener noreferrer">
      <CompanyLogo url={pick.logo_url} source={pick.source} size="lg" endpoint="/api/public/logo" />
      <div className="pb-offer-body">
        <h3 className="pb-offer-title">
          {pick.title}
          <ArrowUpRight className="pb-offer-arrow" aria-label="Otwórz ogłoszenie" />
        </h3>
        <p className="pb-offer-company">{pick.company}</p>
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
        <span className="pb-percent">{pick.percent}%</span>
        <span className="pb-percent-label">dopasowania</span>
      </div>
      <span className="match-track pb-offer-track" style={{ ['--fill' as string]: `${pick.percent}%` }} />
    </a>
  );
}

function ResultView({ state }: { state: SearchState }) {
  const result = state.result!;
  const picks = result.picks;
  const tags = profileTags(state.profile);
  const heading =
    picks.length === 0
      ? 'Tym razem nic nie trafiło w punkt'
      : picks.length === 3
        ? 'Trzy oferty dla Ciebie'
        : `${picks.length} ${plural(picks.length, OFFERS)} dla Ciebie`;

  return (
    <>
      <section className="pb-results-head">
        {tags.length > 0 && (
          <div className="pb-tags">
            {tags.map((tag) => (
              <span key={tag} className="chip">
                {tag}
              </span>
            ))}
          </div>
        )}
        <h1 className="pb-heading">{heading}</h1>
        <p className="muted">
          {picks.length > 0 && result.good < picks.length
            ? 'Najlepsze, co dziś znaleźliśmy dla Twojego doświadczenia, umiejętności i lokalizacji.'
            : 'Wybrane na podstawie Twojego doświadczenia, umiejętności i lokalizacji.'}
        </p>
      </section>
      <div className="pb-offers">
        {picks.map((pick) => (
          <OfferCard key={pick.link} pick={pick} />
        ))}
        <LockedSection teasers={result.teasers} roles={state.profile?.roles ?? []} />
      </div>
    </>
  );
}

function LockedSection({ teasers, roles }: { teasers: number[]; roles: string[] }) {
  return (
    <section className={`pb-locked${teasers.length ? '' : ' is-empty'}`} aria-label="Pełna lista ofert">
      {teasers.length > 0 && (
        <div className="pb-teasers" aria-hidden="true">
          {teasers.map((percent, i) => (
            <div key={i} className="pb-teaser glass">
              <span className="pb-teaser-lock">
                <Lock />
              </span>
              <div className="pb-teaser-content">
                <span className="pb-teaser-logo" />
                <div className="pb-offer-body">
                  <p className="pb-offer-title">{roles[i % Math.max(roles.length, 1)] ?? 'Oferta pracy'}</p>
                  <p className="pb-offer-company">Nazwa firmy</p>
                  <div className="pb-chips">
                    <span className="chip">Lokalizacja</span>
                    <span className="chip">Tryb pracy</span>
                  </div>
                </div>
                <div className="pb-offer-score pb-teaser-score">
                  <span className="pb-percent">{percent}%</span>
                  <span className="pb-percent-label">dopasowania</span>
                </div>
                <span className="match-track pb-offer-track" style={{ ['--fill' as string]: `${percent}%` }} />
              </div>
            </div>
          ))}
        </div>
      )}
      <div className={`pb-unlock${teasers.length ? '' : ' panel'}`}>
        <span className="pb-round-icon">
          <Lock />
        </span>
        <h2 className="title-lg">{teasers.length ? 'Te pasują do Ciebie jeszcze lepiej' : 'Chcesz szukać dalej?'}</h2>
        <p className="muted pb-narrow">
          {teasers.length
            ? 'Pełną listę otworzysz w płatnej wersji. Zostaw e-mail, damy znać, gdy ruszy.'
            : 'Kolejne wyszukiwania i pełne listy ofert będą płatne. Zostaw e-mail, damy znać, gdy ruszy.'}
        </p>
        <WaitlistForm />
      </div>
    </section>
  );
}

function WaitlistForm() {
  const [email, setEmail] = useState('');
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await publicApi.waitlist(email.trim());
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  if (saved) {
    return (
      <p className="pb-saved">
        <Check />
        Gotowe. Powiadomimy Cię, gdy pełny dostęp ruszy.
      </p>
    );
  }
  return (
    <>
      <form className="pb-waitlist" onSubmit={submit}>
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
          Powiadom mnie o starcie
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

function ErrorView({ message, onReset }: { message: string | null; onReset: () => void }) {
  return (
    <section className="pb-card panel pb-progress">
      <h1 className="title-lg">Nie udało się</h1>
      <p className="muted">{message ?? 'Coś poszło nie tak. Spróbuj ponownie.'}</p>
      <p className="label">Nieudane wyszukiwanie nie zużywa bezpłatnej próby.</p>
      <button type="button" className="btn btn-primary" onClick={onReset}>
        <RotateCcw />
        Spróbuj ponownie
      </button>
    </section>
  );
}

function PrivacyView({ legal, backHash }: { legal: LegalInfo | null; backHash: string }) {
  const ttl = legal?.search_ttl_hours ?? 2;
  const windowDays = legal?.free_window_days ?? 30;
  return (
    <article className="pb-legal panel">
      <a className="btn-quiet pb-legal-back" href={backHash}>
        <ArrowLeft />
        Wróć
      </a>
      <h1 className="title-lg">Polityka prywatności</h1>

      <h2 className="title-md">Kto przetwarza dane</h2>
      {legal?.operator ? (
        <p>
          Administratorem danych jest {legal.operator}. W sprawach danych osobowych pisz na{' '}
          <a href={`mailto:${legal.email}`}>{legal.email}</a>.
        </p>
      ) : (
        <p className="warn">Administrator danych nie został jeszcze podany w konfiguracji serwera.</p>
      )}

      <h2 className="title-md">Jakie dane zbieramy i po co</h2>
      <ul>
        <li>
          Treść CV, którą dodajesz. Zwykle zawiera imię i nazwisko, dane kontaktowe, historię pracy i wykształcenie.
          Używamy jej tylko do znalezienia pasujących ofert, czyli do wykonania usługi, którą zlecasz, dodając plik
          (art. 6 ust. 1 lit. b RODO). Nie wpisuj do CV danych, których szukanie pracy nie wymaga, na przykład
          o zdrowiu czy wyznaniu.
        </li>
        <li>
          Jednokierunkowy skrót adresu IP, liczony z tajnym kluczem, żeby jedno bezpłatne wyszukiwanie przypadało na
          jeden adres i żeby chronić serwis przed nadużyciami. Podstawą jest nasz prawnie uzasadniony interes (art. 6
          ust. 1 lit. f RODO).
        </li>
        <li>
          Adres e-mail, jeśli sam go podasz, żeby powiadomić Cię o starcie płatnej wersji. Podstawą jest Twoja zgoda.
        </li>
      </ul>

      <h2 className="title-md">Jak długo</h2>
      <ul>
        <li>Plik CV kasujemy zaraz po wyciągnięciu z niego tekstu.</li>
        <li>Tekst CV istnieje tylko w czasie wyszukiwania i nie zapisujemy go na dysku.</li>
        <li>
          Wynik (znalezione oferty oraz miasto, poziom i stanowiska odczytane z CV) trzymamy w pamięci serwera przez{' '}
          {ttl} {ttl === 1 ? 'godzinę' : 'godziny'}. Potem znika.
        </li>
        <li>Skrót adresu IP przechowujemy przez {windowDays} dni.</li>
        <li>Adres e-mail przechowujemy, dopóki nie wycofasz zgody.</li>
      </ul>

      <h2 className="title-md">Komu przekazujemy dane</h2>
      <ul>
        <li>
          {legal?.cv_reader ?? 'Dostawca modelu językowego'}: model odczytuje z CV miasto, poziom, umiejętności,
          języki i stanowiska. Korzystamy z płatnej wersji usługi, w której dostawca nie używa przesłanych treści do
          trenowania modeli.
        </li>
        <li>
          TypeSafe AI, Inc. (USA): model Jev ocenia, jak bardzo oferty pasują do CV. TypeSafe nie trenuje modeli na
          przesłanych danych, a przekazanie danych do USA odbywa się na podstawie standardowych klauzul umownych
          Komisji Europejskiej.
        </li>
        {legal?.hosting && <li>{legal.hosting}: dostawca serwera, na którym działa strona.</li>}
      </ul>
      <p>Dopasowanie liczy model AI. Wynik to tylko propozycja ofert i nie wywołuje wobec Ciebie skutków prawnych.</p>

      <h2 className="title-md">Twoje prawa</h2>
      <p>
        Masz prawo dostępu do swoich danych, ich sprostowania, usunięcia, ograniczenia przetwarzania i przeniesienia
        oraz prawo sprzeciwu. Zgodę na powiadomienia e-mail możesz wycofać w każdej chwili, co nie wpływa na zgodność
        z prawem wcześniejszego przetwarzania. Możesz też złożyć skargę do Prezesa Urzędu Ochrony Danych Osobowych.
      </p>

      <h2 className="title-md">Pliki cookie</h2>
      <p>Strona nie używa plików cookie ani narzędzi analitycznych.</p>

      <h2 className="title-md">Dla kogo jest strona</h2>
      <p>Z serwisu mogą korzystać osoby, które ukończyły 16 lat.</p>
    </article>
  );
}

function TermsView({ legal, backHash }: { legal: LegalInfo | null; backHash: string }) {
  const ttl = legal?.search_ttl_hours ?? 2;
  const windowDays = legal?.free_window_days ?? 30;
  return (
    <article className="pb-legal panel">
      <a className="btn-quiet pb-legal-back" href={backHash}>
        <ArrowLeft />
        Wróć
      </a>
      <h1 className="title-lg">Regulamin</h1>

      <h2 className="title-md">Kto prowadzi serwis</h2>
      {legal?.operator ? (
        <p>
          Serwis prowadzi {legal.operator}. Kontakt: <a href={`mailto:${legal.email}`}>{legal.email}</a>.
        </p>
      ) : (
        <p className="warn">Dane prowadzącego nie zostały jeszcze podane w konfiguracji serwera.</p>
      )}

      <h2 className="title-md">Co robi serwis</h2>
      <p>
        Po dodaniu CV serwis porównuje je z ofertami pracy z polskich portali i pokazuje te, które najbardziej do niego
        pasują. Dopasowanie ocenia model AI, więc wynik jest propozycją. Nie gwarantujemy, że oferta jest nadal aktualna
        ani że dostaniesz pracę. Ogłoszenie otwiera się na portalu, który je opublikował, a za jego treść odpowiada ten
        portal albo pracodawca.
      </p>

      <h2 className="title-md">Znaki towarowe</h2>
      <p>
        Nazwy i logo portali należą do ich właścicieli. Pokazujemy je tylko po to, żeby wskazać, skąd pochodzą oferty.
        Serwis nie jest powiązany z tymi portalami.
      </p>

      <h2 className="title-md">Jak korzystać</h2>
      <ul>
        <li>
          Dodanie pliku CV (PDF albo DOCX, do 5 MB) oznacza akceptację regulaminu i od razu uruchamia jedno
          wyszukiwanie. Umowa dotyczy tego wyszukiwania i kończy się, gdy zobaczysz wynik.
        </li>
        <li>
          Z jednego adresu IP przysługuje jedno bezpłatne wyszukiwanie w ciągu {windowDays} dni. Wyszukiwanie, które
          się nie udało, nie liczy się do limitu.
        </li>
        <li>
          Wynik jest dostępny pod swoim linkiem przez {ttl} {ttl === 1 ? 'godzinę' : 'godziny'}.
        </li>
        <li>
          Na listę powiadomień zapisujesz się, podając e-mail. Wypisać się możesz w każdej chwili, pisząc na adres
          kontaktowy.
        </li>
      </ul>

      <h2 className="title-md">Twoje obowiązki</h2>
      <ul>
        <li>Dodawaj tylko własne CV albo CV osoby, która się na to zgodziła.</li>
        <li>Nie dodawaj plików z treściami bezprawnymi ani dokumentów, które nie są CV.</li>
      </ul>

      <h2 className="title-md">Wymagania</h2>
      <p>
        Wystarczy aktualna przeglądarka z włączonym JavaScriptem. Z serwisu mogą korzystać osoby, które ukończyły 16
        lat.
      </p>

      <h2 className="title-md">Reklamacje</h2>
      <p>Uwagi i reklamacje wysyłaj na adres kontaktowy. Odpowiadamy w ciągu 14 dni.</p>

      <h2 className="title-md">Dane osobowe</h2>
      <p>
        Zasady opisuje <a href={PRIVACY_HASH}>polityka prywatności</a>.
      </p>
    </article>
  );
}
