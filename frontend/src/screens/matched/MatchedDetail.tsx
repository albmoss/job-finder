import { useEffect, useRef, useState } from 'react';
import {
  ArrowUpRight,
  Bookmark,
  BookmarkCheck,
  Check,
  Download,
  Ellipsis,
  EyeOff,
  FilePenLine,
  FileText,
  Link,
  LoaderCircle,
  RotateCcw,
  Sparkles,
} from 'lucide-react';
import { api } from '../../api';
import { formatRelativeDay } from '../../format';
import { plural } from '../../plural';
import { navigate, paths } from '../../router';
import type { CvVersionSummary, DescriptionBlock, OfferDetail } from '../../types';
import { CompanyLogo } from '../offer/CompanyLogo';
import { capitalize, contractText, locationLine, offerSkills, seniorityText } from '../offer/offer_format';

const NUMBERS = ['liczba', 'liczby', 'liczb'] as const;

interface MatchedDetailProps {
  offer: OfferDetail;
  busy: boolean;
  onSave: () => void;
  onUnsave: () => void;
  onHide: () => void;
  onRestore: () => void;
  onMarkSent: () => void;
  onCopyLink: () => void;
}

export function MatchedDetail({
  offer,
  busy,
  onSave,
  onUnsave,
  onHide,
  onRestore,
  onMarkSent,
  onCopyLink,
}: MatchedDetailProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const hidden = offer.status === 'reject';
  const saved = offer.status === 'save';
  const added = formatRelativeDay(offer.scraped_at);
  const level = seniorityText(offer.fields);
  const contract = contractText(offer.fields);
  const place = locationLine(offer.location, offer.work_mode);
  const skills = offerSkills(offer.fields);
  const facts: [string, string][] = [
    ['Wynagrodzenie', offer.salary_text || 'Brak widełek'],
    ...(level ? ([['Poziom', level]] as [string, string][]) : []),
    ...(offer.work_mode ? ([['Tryb pracy', capitalize(offer.work_mode)]] as [string, string][]) : []),
    ...(contract ? ([['Umowa', contract]] as [string, string][]) : []),
  ];

  useEffect(() => {
    if (!menuOpen) return;
    const onPointer = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setMenuOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMenuOpen(false);
    };
    document.addEventListener('pointerdown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [menuOpen]);

  const runMenu = (action: () => void) => {
    setMenuOpen(false);
    action();
  };

  return (
    <article className="panel mo-detail">
      <header className="mo-detail-source">
        <span className="label">
          {offer.source}
          {added && ` / dodana ${added}`}
          {offer.is_gone && ' / zdjęta z portalu'}
        </span>
        <div className="mo-menu-anchor" ref={menuRef}>
          <button
            type="button"
            className="btn-quiet mo-more"
            aria-label="Więcej działań"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((open) => !open)}
          >
            <Ellipsis />
          </button>
          {menuOpen && (
            <div className="menu panel mo-menu-right" role="menu">
              {hidden ? (
                <button type="button" className="menu-item" role="menuitem" disabled={busy} onClick={() => runMenu(onRestore)}>
                  <RotateCcw />
                  Przywróć
                </button>
              ) : (
                <button type="button" className="menu-item" role="menuitem" disabled={busy} onClick={() => runMenu(onHide)}>
                  <EyeOff />
                  Ukryj tę ofertę
                </button>
              )}
              <button type="button" className="menu-item" role="menuitem" onClick={() => runMenu(onCopyLink)}>
                <Link />
                Skopiuj link
              </button>
            </div>
          )}
        </div>
      </header>

      <div className="mo-identity">
        <CompanyLogo url={offer.logo_url} source={offer.source} size="lg" />
        <div className="mo-identity-text">
          <h1 className="mo-detail-title">{offer.title}</h1>
          <p className="mo-detail-company">
            <span>{offer.company}</span>
            {place && (
              <>
                <span className="mo-dot">·</span>
                <span>{place}</span>
              </>
            )}
          </p>
        </div>
        {offer.match_percentage !== null && (
          <div className="mo-percent">
            <span className="mo-percent-value">{offer.match_percentage}%</span>
            <span className="label">dopasowania do CV</span>
          </div>
        )}
      </div>

      <div className="mo-detail-body scroll">
        <dl className="mo-facts">
          {facts.map(([label, value]) => (
            <div key={label} className="mo-fact">
              <dt className="label">{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
        {skills.length > 0 && (
          <ul className="mo-skills">
            {skills.map((skill) => (
              <li key={skill} className="chip">
                {skill}
              </li>
            ))}
          </ul>
        )}
        <Description blocks={offer.description_blocks} raw={offer.raw_description} />
      </div>

      <CvEntry offer={offer} />

      <footer className="mo-actions">
        {hidden ? (
          <button type="button" className="btn" disabled={busy} onClick={onRestore}>
            <RotateCcw />
            Przywróć
          </button>
        ) : saved ? (
          <button type="button" className="btn" disabled={busy} onClick={onUnsave}>
            <BookmarkCheck />
            Zapisano
          </button>
        ) : (
          <button type="button" className="btn" disabled={busy} onClick={onSave}>
            <Bookmark />
            Zapisz
          </button>
        )}
        <button type="button" className="btn" disabled={busy} onClick={onMarkSent}>
          <Check />
          Oznacz jako wysłane
        </button>
        <span className="mo-actions-spacer" />
        <a className="btn btn-primary" href={offer.link} target="_blank" rel="noreferrer">
          <ArrowUpRight />
          Otwórz ogłoszenie
        </a>
      </footer>
    </article>
  );
}

function Description({ blocks, raw }: { blocks: DescriptionBlock[]; raw: string }) {
  const firstLead = blocks.findIndex((block) => block.type === 'lead');
  if (blocks.length === 0 && !raw.trim()) {
    return (
      <section className="mo-desc-missing">
        <span className="mo-desc-missing-icon">
          <FileText />
        </span>
        <h2 className="mo-desc-missing-title">Brak opisu</h2>
        <p className="mo-desc-missing-text">Spróbujemy go pobrać przy następnym wyszukiwaniu.</p>
      </section>
    );
  }
  if (blocks.length === 0) {
    return (
      <section className="mo-desc">
        <h2 className="mo-desc-title">O tej pracy</h2>
        <p className="mo-desc-text">{raw.trim()}</p>
      </section>
    );
  }
  return (
    <section className="mo-desc">
      <h2 className="mo-desc-title">O tej pracy</h2>
      {blocks.map((block, idx) => {
        const lead = firstLead === -1 || idx < firstLead;
        if (block.type === 'lead') {
          return (
            <h3 key={idx} className="mo-desc-heading">
              {(block.text ?? '').replace(/:\s*$/, '')}
            </h3>
          );
        }
        if (block.type === 'list') {
          return (
            <ul key={idx} className={`mo-desc-list${lead ? ' is-lead' : ''}`}>
              {(block.items ?? []).map((item, itemIdx) => (
                <li key={itemIdx}>{item}</li>
              ))}
            </ul>
          );
        }
        return (
          <p key={idx} className={`mo-desc-text${lead ? ' is-lead' : ''}`}>
            {block.text}
          </p>
        );
      })}
    </section>
  );
}

function CvEntry({ offer }: { offer: OfferDetail }) {
  const [versions, setVersions] = useState<CvVersionSummary[]>(offer.cv_versions);
  const latest = versions[0] ?? null;
  const running = latest?.status === 'running';

  useEffect(() => {
    setVersions(offer.cv_versions);
  }, [offer.cv_versions]);

  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => {
      api
        .listCvVersions(offer.link)
        .then((res) => setVersions(res.items))
        .catch(() => undefined);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [running, offer.link]);

  let subtitle = 'Osobna wersja. Bazowe CV zostaje bez zmian.';
  let action = (
    <button type="button" className="btn" onClick={() => navigate(paths.tailorNew(offer.link))}>
      <Sparkles />
      Dopasuj CV
    </button>
  );
  if (latest?.status === 'running') {
    subtitle = 'Tworzymy wersję…';
    action = (
      <button type="button" className="btn" onClick={() => navigate(paths.cvVersion(latest.id))}>
        <LoaderCircle className="spin" />
        Pokaż
      </button>
    );
  } else if (latest?.status === 'failed') {
    subtitle = latest.error || 'Nie udało się utworzyć wersji.';
    action = (
      <button type="button" className="btn" onClick={() => navigate(paths.cvVersion(latest.id))}>
        Pokaż
      </button>
    );
  } else if (latest?.status === 'draft') {
    const pending = latest.pending_numbers;
    subtitle = pending > 0 ? `Szkic · ${pending} ${plural(pending, NUMBERS)} do potwierdzenia` : 'Szkic gotowy do sprawdzenia';
    action = (
      <button type="button" className="btn" onClick={() => navigate(paths.cvVersion(latest.id))}>
        Sprawdź
      </button>
    );
  } else if (latest?.status === 'ready') {
    subtitle = latest.file_name || `Wersja ${latest.version}`;
    action = (
      <a className="btn" href={api.cvVersionPdfUrl(latest.id)} download={latest.file_name ?? undefined}>
        <Download />
        Pobierz PDF
      </a>
    );
  }

  return (
    <div className="glass mo-cv">
      <FilePenLine className="mo-cv-icon" />
      <div className="mo-cv-text">
        <span className="mo-cv-title">CV pod tę ofertę</span>
        <span className="label">{subtitle}</span>
      </div>
      {action}
    </div>
  );
}
