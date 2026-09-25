import React, { useState } from 'react';
import { Check, AlertTriangle, FileText, Key, Globe, Eye, EyeOff } from 'lucide-react';
import type { CVInfo, EnvField, PipelinePrerequisites } from '../types';
import { api } from '../api';
import '../styles/pipeline.css';

interface CvEditorProps {
  cvInfo: CVInfo | null;
  disabled?: boolean;
  onSaved: () => void;
  showToast: (msg: string, action?: { label: string; run: () => void }, done?: boolean) => void;
}

export const CvEditor: React.FC<CvEditorProps> = ({ cvInfo, disabled, onSaved, showToast }) => {
  const [pasted, setPasted] = useState('');
  const [saving, setSaving] = useState(false);

  const upload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    setSaving(true);
    try {
      const res = await api.uploadCVFile(file);
      showToast(`Zapisano CV: ${res.cv_info.filename} (${res.cv_info.words} słów)`);
      onSaved();
    } catch (err) {
      showToast(`Błąd zapisu CV: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setSaving(false);
    }
  };

  const savePasted = async () => {
    setSaving(true);
    try {
      const res = await api.pasteCVText(pasted);
      showToast(`Zapisano treść CV (${res.cv_info.words} słów).`);
      setPasted('');
      onSaved();
    } catch (err) {
      showToast(`Błąd zapisu CV: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setSaving(false);
    }
  };

  const openLocal = async () => {
    try {
      const res = await api.openLocalCV();
      showToast(res.message);
    } catch (err) {
      showToast(`Błąd otwierania CV: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <label
          className={`btn btn-secondary ${disabled || saving ? 'is-disabled' : ''}`}
          style={{ cursor: disabled || saving ? 'default' : 'pointer' }}
        >
          <FileText size={14} /> {saving ? 'Zapisuję…' : 'Wgraj plik CV'}
          <input
            type="file"
            accept=".pdf,.docx,.doc,.txt,.md"
            disabled={disabled || saving}
            onChange={upload}
            style={{ display: 'none' }}
          />
        </label>

        {cvInfo?.pdf_exists && (
          <button type="button" className="btn btn-quiet" onClick={openLocal} disabled={disabled || saving}>
            Otwórz cv.pdf
          </button>
        )}

        <span style={{ fontSize: 11, color: 'var(--ink-3)' }}>PDF, DOCX albo TXT</span>
      </div>

      <div className="field">
        <label className="field-label" htmlFor="cv-paste">
          Albo wklej treść życiorysu
        </label>
        <textarea
          id="cv-paste"
          className="textarea"
          rows={4}
          value={pasted}
          disabled={disabled || saving}
          onChange={(e) => setPasted(e.target.value)}
          placeholder="Doświadczenie, technologie, rola, wykształcenie…"
        />
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 4 }}>
          <button
            type="button"
            className="btn btn-secondary"
            style={{ height: 32, fontSize: 12, padding: '0 12px' }}
            disabled={disabled || saving || pasted.trim().length < 20}
            onClick={savePasted}
          >
            Zapisz wklejony tekst
          </button>
          {pasted && pasted.trim().length < 20 && (
            <span style={{ fontSize: 11, color: 'var(--ink-3)' }}>minimum 20 znaków</span>
          )}
        </div>
      </div>

      {cvInfo?.text && (
        <div className="field">
          <span className="field-label">Początek aktywnego CV</span>
          <div className="pp-cv-preview">{cvInfo.text.slice(0, 500)}…</div>
        </div>
      )}
    </div>
  );
};

interface KeysEditorProps {
  envFields: EnvField[];
  full?: boolean;
  disabled?: boolean;
  onSaved: () => void;
  showToast: (msg: string, action?: { label: string; run: () => void }, done?: boolean) => void;
}

export const KeysEditor: React.FC<KeysEditorProps> = ({ envFields, full, disabled, onSaved, showToast }) => {
  const [quickKey, setQuickKey] = useState('');
  const [showQuickKey, setShowQuickKey] = useState(false);
  const [updates, setUpdates] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  const saveQuick = async () => {
    setSaving(true);
    try {
      await api.saveQuickGeminiKey(quickKey.trim());
      showToast('Zapisano GEMINI_API_KEY_PRIMARY w .env');
      setQuickKey('');
      onSaved();
    } catch (err) {
      showToast(`Błąd zapisu klucza: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setSaving(false);
    }
  };

  const saveAll = async () => {
    setSaving(true);
    try {
      const res = await api.saveEnvKeys(updates);
      showToast(res.message);
      setUpdates({});
      onSaved();
    } catch (err) {
      showToast(`Błąd zapisu .env: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* Szybki klucz Gemini */}
      <div className="field">
        <label className="field-label" htmlFor="quick-gemini-key">
          Główny klucz Gemini (GEMINI_API_KEY_PRIMARY)
        </label>
        <div style={{ display: 'flex', gap: 8 }}>
          <div style={{ position: 'relative', flex: 1 }}>
            <input
              id="quick-gemini-key"
              className="input"
              type={showQuickKey ? 'text' : 'password'}
              placeholder="AIzaSy…"
              value={quickKey}
              disabled={disabled || saving}
              onChange={(e) => setQuickKey(e.target.value)}
              style={{ fontFamily: 'var(--mono)', paddingRight: 36 }}
            />
            <button
              type="button"
              className="iconbtn"
              style={{
                position: 'absolute',
                right: 4,
                top: 4,
                width: 36,
                height: 36,
                background: 'none',
              }}
              onClick={() => setShowQuickKey(!showQuickKey)}
              aria-label={showQuickKey ? 'Ukryj klucz' : 'Pokaż klucz'}
            >
              {showQuickKey ? <EyeOff size={15} /> : <Eye size={15} />}
            </button>
          </div>

          <button
            type="button"
            className="btn btn-primary"
            disabled={disabled || saving || !quickKey.trim()}
            onClick={saveQuick}
          >
            Zapisz klucz
          </button>
        </div>
      </div>

      {/* Wszystkie pola .env */}
      {full && envFields.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ height: 1, background: 'var(--line)', margin: '4px 0' }} />
          <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink)' }}>
            Pozostałe wpisy środowiska (.env)
          </div>

          <div className="pp-env-grid">
            {envFields.map((f) => (
              <div className="field" key={f.key}>
                <label
                  className="field-label"
                  htmlFor={`env-${f.key}`}
                  style={{ display: 'flex', justifyContent: 'space-between' }}
                >
                  <span>{f.label}</span>
                  <span style={{ fontSize: 11, color: f.configured ? 'var(--ink-2)' : 'var(--ink-3)' }}>
                    {f.configured ? 'ustawiony' : 'brak'}
                  </span>
                </label>
                <input
                  id={`env-${f.key}`}
                  className="input"
                  type={f.secret ? 'password' : 'text'}
                  placeholder={f.configured ? '•••••••• (bez zmian)' : 'wartość'}
                  value={updates[f.key] ?? ''}
                  disabled={disabled || saving}
                  onChange={(e) => setUpdates({ ...updates, [f.key]: e.target.value })}
                  style={{ fontFamily: 'var(--mono)', height: 38, fontSize: 12.5 }}
                />
              </div>
            ))}
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 4 }}>
            <button
              type="button"
              className="btn btn-secondary"
              style={{ height: 34, fontSize: 12 }}
              disabled={disabled || saving || Object.keys(updates).length === 0}
              onClick={saveAll}
            >
              Zapisz zmiany w .env
            </button>
            <span style={{ fontSize: 11, color: 'var(--ink-3)' }}>Puste pola zostawiają dotychczasową wartość.</span>
          </div>
        </div>
      )}
    </div>
  );
};

export type SetupSectionKey = 'cv' | 'keys' | 'scrapers';

export interface PipelineSetupProps {
  prerequisites: PipelinePrerequisites | null;
  cvInfo: CVInfo | null;
  envFields: EnvField[];
  disabled?: boolean;
  onRefreshCV: () => void;
  onRefreshEnvKeys: () => void;
  onRefreshPrerequisites: () => void;
  showToast: (msg: string, action?: { label: string; run: () => void }, done?: boolean) => void;
}

export const PipelineSetup: React.FC<PipelineSetupProps> = ({
  prerequisites,
  cvInfo,
  envFields,
  disabled,
  onRefreshCV,
  onRefreshEnvKeys,
  onRefreshPrerequisites,
  showToast,
}) => {
  const [openSection, setOpenSection] = useState<SetupSectionKey | null>(null);
  const configuredKeysCount = envFields.filter((f) => f.configured).length;

  const tiles = [
    {
      key: 'cv' as SetupSectionKey,
      name: 'CV',
      ok: Boolean(cvInfo?.ready),
      desc: cvInfo?.ready ? `${cvInfo.filename} · ${cvInfo.words} słów` : 'brak — model nie ma wzorca',
      Icon: FileText,
    },
    {
      key: 'keys' as SetupSectionKey,
      name: 'Klucze API',
      ok: Boolean(prerequisites?.api_ready),
      desc: prerequisites?.api_ready
        ? `Gemini gotowy · ${configuredKeysCount} wpisów w .env`
        : 'brak klucza Gemini',
      Icon: Key,
    },
    {
      key: 'scrapers' as SetupSectionKey,
      name: 'Scrapery',
      ok: Boolean(prerequisites?.playwright_ready),
      desc: prerequisites?.playwright_ready
        ? 'Playwright + Chromium'
        : prerequisites?.playwright_msg || 'nie sprawdzono',
      Icon: Globe,
    },
  ];

  const handleSaved = () => {
    onRefreshCV();
    onRefreshEnvKeys();
    onRefreshPrerequisites();
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10 }}>
        {tiles.map(({ key, name, ok, desc, Icon }) => (
          <div
            key={key}
            style={{
              padding: '12px 14px',
              borderRadius: 16,
              background: 'rgba(255, 255, 255, 0.025)',
              border: `1px solid ${openSection === key ? 'var(--stroke-strong)' : 'var(--stroke)'}`,
              display: 'flex',
              flexDirection: 'column',
              gap: 8,
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontWeight: 600, fontSize: 13 }}>
                <Icon size={15} style={{ color: 'var(--ink-2)' }} />
                <span>{name}</span>
                {ok ? (
                  <Check size={14} style={{ color: 'var(--ink)' }} />
                ) : (
                  <AlertTriangle size={14} style={{ color: 'var(--ink)' }} />
                )}
              </div>

              <button
                type="button"
                className="btn btn-quiet"
                style={{ height: 26, padding: '0 8px', fontSize: 11 }}
                onClick={() => setOpenSection(openSection === key ? null : key)}
              >
                {openSection === key ? 'Zwiń' : ok ? 'Zmień' : 'Uzupełnij'}
              </button>
            </div>

            <div style={{ fontSize: 11.5, color: 'var(--ink-3)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {desc}
            </div>
          </div>
        ))}
      </div>

      {openSection === 'cv' && (
        <div className="pp-setup-drawer">
          <div className="pp-setup-head">
            <span>Ustawienia życiorysu (CV)</span>
          </div>
          <CvEditor cvInfo={cvInfo} disabled={disabled} onSaved={handleSaved} showToast={showToast} />
        </div>
      )}

      {openSection === 'keys' && (
        <div className="pp-setup-drawer">
          <div className="pp-setup-head">
            <span>Klucze API i środowisko</span>
          </div>
          <KeysEditor envFields={envFields} full disabled={disabled} onSaved={handleSaved} showToast={showToast} />
        </div>
      )}

      {openSection === 'scrapers' && (
        <div className="pp-setup-drawer">
          <div className="pp-setup-head">
            <span>Wymagania scraperów</span>
          </div>
          <div style={{ fontSize: 13, color: 'var(--ink-2)', lineHeight: 1.5 }}>
            {prerequisites?.playwright_ready ? (
              <p style={{ margin: 0 }}>
                Przeglądarka Chromium (Playwright) jest poprawnie skonfigurowana. Scraping ze wszystkich portali jest dostępny.
              </p>
            ) : (
              <div>
                <p style={{ margin: '0 0 8px 0', color: 'var(--ink)' }}>
                  {prerequisites?.playwright_msg || 'Chromium nie jest zainstalowane.'}
                </p>
                <p style={{ margin: 0, fontSize: 12, color: 'var(--ink-3)' }}>
                  Możesz zainstalować przeglądarkę poleceniem <code style={{ fontFamily: 'var(--mono)' }}>playwright install chromium</code> albo uruchomić pipeline w trybie „Tylko ocena AI”.
                </p>
              </div>
            )}
          </div>
          <div style={{ marginTop: 8 }}>
            <button
              type="button"
              className="btn btn-secondary"
              style={{ height: 32, fontSize: 12 }}
              onClick={() => {
                onRefreshPrerequisites();
                showToast('Sprawdzono wymagania ponownie.');
              }}
            >
              Sprawdź ponownie
            </button>
          </div>
        </div>
      )}
    </div>
  );
};
