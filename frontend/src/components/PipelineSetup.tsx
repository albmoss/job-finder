import React, { useState } from 'react';
import { FileText, Eye, EyeOff } from 'lucide-react';
import type { CVInfo, EnvKeysResponse } from '../types';
import { api } from '../api';
import { BACKUPS, plural } from '../plural';
import '../styles/pipeline.css';
import '../styles/applications.css';

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
  envKeys: EnvKeysResponse | null;
  full?: boolean;
  disabled?: boolean;
  onSaved: () => void;
  showToast: (msg: string, action?: { label: string; run: () => void }, done?: boolean) => void;
}

export const KeysEditor: React.FC<KeysEditorProps> = ({ envKeys, full, disabled, onSaved, showToast }) => {
  const info = envKeys?.api_info;
  const envFields = envKeys?.fields ?? [];
  const [picked, setPicked] = useState<string | null>(null);
  const [key, setKey] = useState('');
  const [showKey, setShowKey] = useState(false);
  // null = pole nietknięte: zapis nie rusza wartości w .env
  const [models, setModels] = useState<string | null>(null);
  const [baseUrl, setBaseUrl] = useState<string | null>(null);
  const [updates, setUpdates] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  const providerId = picked ?? info?.provider ?? 'gemini';
  const provider = info?.providers.find((p) => p.id === providerId);
  const isCurrent = providerId === info?.provider;
  const dirty = (picked !== null && !isCurrent) || Boolean(key.trim()) || models !== null || baseUrl !== null;

  const pick = (id: string) => {
    setPicked(id);
    setKey('');
    setModels(null);
    setBaseUrl(null);
  };

  const saveLlm = async () => {
    setSaving(true);
    try {
      const res = await api.saveLlmSettings({
        provider: providerId,
        key: key.trim() || undefined,
        models: models ?? undefined,
        base_url: baseUrl ?? undefined,
      });
      showToast(res.message);
      setPicked(null);
      setKey('');
      setModels(null);
      setBaseUrl(null);
      onSaved();
    } catch (err) {
      showToast(`Błąd zapisu ustawień modelu: ${err instanceof Error ? err.message : String(err)}`);
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

  const busy = disabled || saving;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {info && provider && (
        <>
          <div className="field">
            <span className="field-label" id="llm-provider-label">Dostawca modelu</span>
            <div
              className={`ab-stage-switch ${busy ? 'is-disabled' : ''}`}
              role="radiogroup"
              aria-labelledby="llm-provider-label"
              style={{ alignSelf: 'flex-start' }}
            >
              {info.providers.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  role="radio"
                  aria-checked={p.id === providerId}
                  disabled={busy}
                  className={`ab-stage-segment press ${p.id === providerId ? 'is-active' : ''}`}
                  onClick={() => p.id !== providerId && pick(p.id)}
                >
                  <span className="ab-stage-label">{p.label}</span>
                </button>
              ))}
            </div>
            <span className="field-help">
              {isCurrent
                ? info.ready
                  ? `W użyciu · klucz ${info.primary_masked}` +
                    (info.count > 1 ? ` + ${info.count - 1} ${plural(info.count - 1, BACKUPS)}` : '')
                  : info.error
                : 'Zapis przełączy ocenę ofert na tego dostawcę.'}
            </span>
          </div>

          <div className="field">
            <label className="field-label" htmlFor="llm-key">
              Klucz główny ({provider.key_env})
            </label>
            <div style={{ position: 'relative' }}>
              <input
                id="llm-key"
                className="input"
                type={showKey ? 'text' : 'password'}
                placeholder={isCurrent && info.count > 0 ? '•••••••• (bez zmian)' : provider.key_hint}
                value={key}
                disabled={busy}
                onChange={(e) => setKey(e.target.value)}
                style={{ fontFamily: 'var(--mono)', paddingRight: 36 }}
              />
              <button
                type="button"
                className="iconbtn"
                style={{ position: 'absolute', right: 4, top: 4, width: 36, height: 36, background: 'none' }}
                onClick={() => setShowKey(!showKey)}
                aria-label={showKey ? 'Ukryj klucz' : 'Pokaż klucz'}
              >
                {showKey ? <EyeOff size={15} /> : <Eye size={15} />}
              </button>
            </div>
          </div>

          <div className="field">
            <label className="field-label" htmlFor="llm-models">
              Modele po przecinku ({provider.models_env})
            </label>
            <input
              id="llm-models"
              className="input"
              type="text"
              placeholder={provider.default_models.join(', ')}
              value={models ?? (isCurrent && info.models_custom ? info.models.join(', ') : '')}
              disabled={busy}
              onChange={(e) => setModels(e.target.value)}
              style={{ fontFamily: 'var(--mono)', height: 38, fontSize: 12.5 }}
            />
          </div>
          {provider.base_url_env && (
            <div className="field">
              <label className="field-label" htmlFor="llm-base-url">
                Adres API ({provider.base_url_env})
              </label>
              <input
                id="llm-base-url"
                className="input"
                type="text"
                placeholder={provider.default_base_url}
                value={baseUrl ?? (isCurrent && info.base_url !== provider.default_base_url ? info.base_url : '')}
                disabled={busy}
                onChange={(e) => setBaseUrl(e.target.value)}
                style={{ fontFamily: 'var(--mono)', height: 38, fontSize: 12.5 }}
              />
            </div>
          )}

          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <button type="button" className="btn btn-primary" disabled={busy || !dirty} onClick={saveLlm}>
              Zapisz ustawienia modelu
            </button>
            <span style={{ fontSize: 11, color: 'var(--ink-3)' }}>
              Puste pole modeli = lista domyślna; pierwszy model to pierwszy wybór.
            </span>
          </div>
        </>
      )}

      {/* Wszystkie pola .env */}
      {full && envFields.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ height: 1, background: 'var(--line)', margin: '4px 0' }} />
          <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink)' }}>
            Klucze zapasowe i źródła ofert (.env)
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
                  disabled={busy}
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
              disabled={busy || Object.keys(updates).length === 0}
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

