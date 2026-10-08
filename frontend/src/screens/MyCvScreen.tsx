import { useEffect, useRef, useState } from 'react';
import {
  ArrowRight,
  ArrowUpRight,
  ChevronRight,
  CircleCheck,
  FileText,
  Settings2,
  TriangleAlert,
  Upload,
} from 'lucide-react';
import { api, errorMessage } from '../api';
import { useApp } from '../app_context';
import { navigate, paths } from '../router';
import { CV_REPLACED_TOAST } from '../shell/SettingsModal';
import { Modal } from '../shell/Modal';
import { PAGES, plural, type PluralForms } from '../plural';
import type { CVProfile, CvVersionSummary } from '../types';
import type { ScreenProps } from './types';
import '../styles/my_cv.css';

const NUMBERS: PluralForms = ['liczba', 'liczby', 'liczb'];

const SENIORITY: Record<string, string> = {
  intern: 'Stażysta',
  junior: 'Junior',
  mid: 'Mid',
  senior: 'Senior',
  lead: 'Lead',
  manager: 'Manager',
};

const LANGUAGES: Record<string, string> = {
  english: 'Angielski',
  german: 'Niemiecki',
  french: 'Francuski',
  spanish: 'Hiszpański',
  italian: 'Włoski',
  russian: 'Rosyjski',
  ukrainian: 'Ukraiński',
  czech: 'Czeski',
  slovak: 'Słowacki',
  dutch: 'Niderlandzki',
  portuguese: 'Portugalski',
  swedish: 'Szwedzki',
  norwegian: 'Norweski',
  danish: 'Duński',
  finnish: 'Fiński',
  chinese: 'Chiński',
  japanese: 'Japoński',
  korean: 'Koreański',
  arabic: 'Arabski',
  turkish: 'Turecki',
  hungarian: 'Węgierski',
  romanian: 'Rumuński',
  lithuanian: 'Litewski',
  greek: 'Grecki',
};

const POLISH: Record<string, true> = { polish: true, polski: true, pl: true };

const capitalize = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

function locationLabel(profile: CVProfile): string {
  if (profile.city) return profile.remote ? `${profile.city} + zdalnie` : profile.city;
  return profile.remote ? 'Zdalnie' : '—';
}

function seniorityLabel(profile: CVProfile): string {
  const raw = profile.seniority?.trim();
  if (!raw) return '—';
  return SENIORITY[raw.toLowerCase()] ?? capitalize(raw);
}

function languagesLabel(profile: CVProfile): string {
  const items = profile.languages
    .filter((lang) => lang.name && !POLISH[lang.name.trim().toLowerCase()])
    .map((lang) => {
      const key = lang.name.trim().toLowerCase();
      const name = LANGUAGES[key] ?? capitalize(lang.name.trim());
      return lang.level ? `${name} ${lang.level}` : name;
    });
  return items.length ? items.join(', ') : '—';
}

function versionStatus(version: CvVersionSummary): string {
  switch (version.status) {
    case 'ready':
      return `Wersja ${version.version} · gotowa do pobrania`;
    case 'draft':
      return version.pending_numbers > 0
        ? `Szkic · ${version.pending_numbers} ${plural(version.pending_numbers, NUMBERS)} do potwierdzenia`
        : 'Szkic';
    case 'running':
      return `Wersja ${version.version} · tworzymy…`;
    case 'failed':
      return `Wersja ${version.version} · nie udało się`;
  }
}

export function MyCvScreen(_props: ScreenProps) {
  const { cv, refreshCv, pipelineBusy, toast, dataVersion } = useApp();
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [fileRev, setFileRev] = useState(0);
  const [showText, setShowText] = useState(false);
  const [versions, setVersions] = useState<CvVersionSummary[] | null>(null);
  const [versionsError, setVersionsError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    const load = () =>
      api
        .listCvVersions()
        .then((res) => {
          if (!alive) return;
          setVersions(res.items);
          setVersionsError(null);
          if (res.items.some((v) => v.status === 'running')) timer = window.setTimeout(load, 3000);
        })
        .catch((err: unknown) => {
          if (alive) setVersionsError(errorMessage(err));
        });
    load();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [dataVersion]);

  const upload = async (file: File) => {
    const hadCv = Boolean(cv?.ready);
    setUploading(true);
    try {
      const res = await api.uploadCVFile(file);
      await refreshCv();
      setFileRev((n) => n + 1);
      toast(hadCv ? CV_REPLACED_TOAST : `Zapisano CV: ${res.cv_info.filename}.`);
    } catch (err) {
      toast(`Nie udało się zastąpić CV: ${errorMessage(err)}`);
    } finally {
      setUploading(false);
    }
  };

  const profile = cv?.profile ?? null;
  const pdfSrc = cv?.pdf_url ? `${cv.pdf_url}?v=${fileRev}#toolbar=0&navpanes=0&view=FitH` : null;

  return (
    <div className="cv-screen">
      <header className="cv-head">
        <div className="cv-head-text">
          <h1 className="title-xl">Twoje CV, punkt wyjścia</h1>
          <p className="cv-subtitle">Na jego podstawie szukamy ofert i tworzymy wersje pod konkretne stanowiska.</p>
        </div>
        <span title={pipelineBusy ? 'Nie można zastąpić CV w trakcie wyszukiwania ofert.' : undefined}>
          <button
            type="button"
            className="btn"
            disabled={pipelineBusy || uploading}
            onClick={() => fileRef.current?.click()}
          >
            <Upload />
            {uploading ? 'Wgrywanie…' : 'Zastąp CV'}
          </button>
        </span>
        <input
          ref={fileRef}
          type="file"
          accept=".pdf,.docx,.doc,.txt,.md"
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = '';
            if (file) upload(file);
          }}
        />
      </header>

      <div className="cv-workspace">
        <section className="panel cv-preview">
          <div className="cv-preview-bar">
            <span className="cv-file-name" title={cv?.filename}>
              {cv?.filename ?? ''}
            </span>
            {cv && (cv.pdf_pages != null || cv.pdf_url) && (
              <span className="cv-preview-meta">
                {cv.pdf_pages != null && (
                  <span className="label">
                    {cv.pdf_pages} {plural(cv.pdf_pages, PAGES)}
                  </span>
                )}
                {cv.pdf_url && (
                  <a
                    className="btn-quiet cv-open"
                    href={`${cv.pdf_url}?v=${fileRev}`}
                    target="_blank"
                    rel="noreferrer"
                    aria-label="Otwórz PDF"
                    title="Otwórz PDF"
                  >
                    <ArrowUpRight />
                  </a>
                )}
              </span>
            )}
          </div>
          <div className="cv-stage">
            <div className="cv-paper">
              {pdfSrc ? (
                <iframe key={pdfSrc} className="cv-pdf" src={pdfSrc} title="Podgląd CV" />
              ) : (
                <pre className="cv-paper-text scroll">{cv?.text ?? ''}</pre>
              )}
            </div>
          </div>
        </section>

        <div className="cv-side">
          <section className="panel cv-card cv-profile">
            <div className="cv-card-head">
              <h2 className="cv-card-title">Profil odczytany z CV</h2>
              {profile &&
                (profile.current ? (
                  <span className="cv-state">
                    <CircleCheck className="cv-head-icon" aria-hidden="true" />
                    Profil aktualny
                  </span>
                ) : (
                  <span className="cv-state">
                    <TriangleAlert className="cv-head-icon warn" aria-hidden="true" />
                    Profil zostanie przeliczony przy następnym wyszukiwaniu
                  </span>
                ))}
            </div>
            {profile ? (
              <>
                <div className="cv-facts">
                  <div className="cv-fact">
                    <span className="label">Lokalizacja</span>
                    <span className="cv-fact-value">{locationLabel(profile)}</span>
                  </div>
                  <div className="cv-fact">
                    <span className="label">Poziom</span>
                    <span className="cv-fact-value">{seniorityLabel(profile)}</span>
                  </div>
                  <div className="cv-fact">
                    <span className="label">Język</span>
                    <span className="cv-fact-value">{languagesLabel(profile)}</span>
                  </div>
                </div>
                {profile.roles.length > 0 && (
                  <div className="cv-fact">
                    <span className="label">Stanowiska</span>
                    <p className="cv-fact-text" title={profile.roles.join(', ')}>
                      {profile.roles.join(', ')}
                    </p>
                  </div>
                )}
                {profile.skills.length > 0 && (
                  <div className="cv-fact">
                    <span className="label">Umiejętności</span>
                    <p className="cv-fact-text muted" title={profile.skills.join(', ')}>
                      {profile.skills.join(', ')}
                    </p>
                  </div>
                )}
                <p className="cv-note">
                  Zakres wyszukiwania dobieramy automatycznie. Jeśli profil się nie zgadza, popraw treść bazowego CV.
                </p>
              </>
            ) : (
              <p className="cv-note">Profil powstanie przy pierwszym wyszukiwaniu.</p>
            )}
            <button type="button" className="btn cv-fit" onClick={() => setShowText(true)} disabled={!cv?.text}>
              <FileText />
              Sprawdź odczytaną treść
            </button>
          </section>

          <section className="panel cv-card cv-versions">
            <h2 className="cv-card-title">Wersje do ofert</h2>
            {versions && versions.length > 0 ? (
              <ul className="cv-version-list scroll">
                {versions.map((version) => (
                  <li key={version.id}>
                    <a
                      className="cv-version"
                      href={`#${paths.cvVersion(version.id)}`}
                      title={version.title}
                      onClick={(e) => {
                        e.preventDefault();
                        navigate(paths.cvVersion(version.id));
                      }}
                    >
                      <FileText className={`cv-version-icon${version.status === 'failed' ? ' warn' : ''}`} />
                      <span className="cv-version-copy">
                        <span className="cv-version-company">{version.company || version.title}</span>
                        <span className="label">{versionStatus(version)}</span>
                      </span>
                      <ChevronRight className="cv-version-chevron" />
                    </a>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="cv-note">
                {versionsError
                  ? `Nie udało się wczytać wersji: ${versionsError}`
                  : versions
                    ? 'Nie masz jeszcze wersji pod oferty. Dopasuj CV z karty oferty.'
                    : 'Wczytywanie…'}
              </p>
            )}
            <div className="cv-actions">
              {versions && versions.length === 0 && !versionsError && (
                <button type="button" className="btn" onClick={() => navigate(paths.matched())}>
                  Przejdź do ofert
                  <ArrowRight />
                </button>
              )}
              <button
                type="button"
                className="btn-quiet"
                title="Stały prompt dopasowania CV. Nie musisz wklejać go przy każdej ofercie."
                onClick={() => navigate(paths.instructions)}
              >
                <Settings2 />
                Instrukcje agenta
              </button>
            </div>
          </section>
        </div>
      </div>

      {showText && cv && (
        <Modal title="Odczytana treść CV" onClose={() => setShowText(false)} wide>
          <p className="label">{cv.filename}</p>
          <pre className="cv-read-text">{cv.text}</pre>
        </Modal>
      )}
    </div>
  );
}
