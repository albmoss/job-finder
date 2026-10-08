import { useCallback, useEffect, useRef, useState } from 'react';
import { Check, Eye, EyeOff, Upload } from 'lucide-react';
import { api, errorMessage } from '../api';
import { useApp } from '../app_context';
import { BACKUPS, plural } from '../plural';
import type { EnvKeysResponse } from '../types';
import { Modal } from './Modal';

const JEV_KEY = 'TYPESAFE_API_KEY';

export const CV_REPLACED_TOAST = 'Zapisano nowe CV. Dotychczasowe oceny zostają, nowe oferty Jev oceni według tego CV.';

function SecretInput({
  id,
  value,
  placeholder,
  disabled,
  onChange,
}: {
  id: string;
  value: string;
  placeholder: string;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  const [visible, setVisible] = useState(false);
  return (
    <div className="field">
      <input
        id={id}
        type={visible ? 'text' : 'password'}
        autoComplete="off"
        value={value}
        placeholder={placeholder}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
      />
      <button
        type="button"
        className="btn-quiet st-eye"
        onClick={() => setVisible(!visible)}
        aria-label={visible ? 'Ukryj klucz' : 'Pokaż klucz'}
      >
        {visible ? <EyeOff /> : <Eye />}
      </button>
    </div>
  );
}

export function SettingsModal({ onClose }: { onClose: () => void }) {
  const { cv, refreshCv, toast } = useApp();
  const [env, setEnv] = useState<EnvKeysResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [jevKey, setJevKey] = useState('');
  const [picked, setPicked] = useState<string | null>(null);
  const [llmKey, setLlmKey] = useState('');
  const [models, setModels] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    api
      .getEnvKeys()
      .then((res) => {
        setEnv(res);
        setLoadError(null);
      })
      .catch((err: unknown) => setLoadError(errorMessage(err)));
  }, []);

  useEffect(load, [load]);

  const info = env?.api_info;
  const jevConfigured = Boolean(env?.fields.find((f) => f.key === JEV_KEY)?.configured);
  const providerId = picked ?? info?.provider ?? '';
  const provider = info?.providers.find((p) => p.id === providerId);
  const isCurrent = providerId === info?.provider;
  const llmDirty = (picked !== null && !isCurrent) || Boolean(llmKey.trim()) || models !== null;

  const run = async (work: () => Promise<string>) => {
    setSaving(true);
    try {
      toast(await work());
      load();
    } catch (err) {
      toast(`Nie udało się zapisać: ${errorMessage(err)}`);
    } finally {
      setSaving(false);
    }
  };

  const saveJev = () =>
    run(async () => {
      const res = await api.saveEnvKeys({ [JEV_KEY]: jevKey.trim() });
      setJevKey('');
      return res.message;
    });

  const saveLlm = () =>
    run(async () => {
      const res = await api.saveLlmSettings({
        provider: providerId,
        key: llmKey.trim() || undefined,
        models: models ?? undefined,
      });
      setPicked(null);
      setLlmKey('');
      setModels(null);
      return res.message;
    });

  const uploadCv = (file: File) => {
    const hadCv = Boolean(cv?.ready);
    return run(async () => {
      const res = await api.uploadCVFile(file);
      await refreshCv();
      return hadCv ? CV_REPLACED_TOAST : `Zapisano CV: ${res.cv_info.filename}.`;
    });
  };

  return (
    <Modal title="Ustawienia" onClose={onClose} wide>
      {loadError && <p className="warn">{loadError}</p>}

      <section className="st-section">
        <div className="st-head">
          <h3 className="title-md">Dopasowanie ofert: Jev</h3>
          <span className="label">{jevConfigured ? 'klucz ustawiony' : 'brak klucza'}</span>
        </div>
        <label className="st-label" htmlFor="st-jev">
          Klucz TypeSafe
          <span className="st-env">{JEV_KEY}</span>
        </label>
        <SecretInput
          id="st-jev"
          value={jevKey}
          placeholder={jevConfigured ? '•••••••• (bez zmian)' : 'klucz z panelu TypeSafe'}
          disabled={saving}
          onChange={setJevKey}
        />
        <div>
          <button type="button" className="btn" disabled={saving || !jevKey.trim()} onClick={saveJev}>
            Zapisz klucz
          </button>
        </div>
      </section>

      <div className="divider" />

      {info && provider && (
        <section className="st-section">
          <div className="st-head">
            <h3 className="title-md">Model odczytu CV</h3>
            <span className="label">
              {isCurrent
                ? info.ready
                  ? `klucz ${info.primary_masked}` +
                    (info.count > 1 ? ` + ${info.count - 1} ${plural(info.count - 1, BACKUPS)}` : '')
                  : info.error
                : 'zapis przełączy dostawcę'}
            </span>
          </div>
          <div className="segmented st-providers" role="radiogroup" aria-label="Dostawca modelu">
            {info.providers.map((p) => (
              <button
                key={p.id}
                type="button"
                role="radio"
                aria-checked={p.id === providerId}
                className="btn btn-sm"
                disabled={saving}
                onClick={() => {
                  setPicked(p.id);
                  setLlmKey('');
                  setModels(null);
                }}
              >
                {p.id === providerId && <Check aria-hidden="true" />}
                {p.label}
              </button>
            ))}
          </div>
          <label className="st-label" htmlFor="st-llm-key">
            Klucz {provider.label}
            <span className="st-env">{provider.key_env}</span>
          </label>
          <SecretInput
            id="st-llm-key"
            value={llmKey}
            placeholder={isCurrent && info.count > 0 ? '•••••••• (bez zmian)' : provider.key_hint}
            disabled={saving}
            onChange={setLlmKey}
          />
          <label className="st-label" htmlFor="st-llm-models">
            Modele (po przecinku, puste = domyślne)
            <span className="st-env">{provider.models_env}</span>
          </label>
          <input
            id="st-llm-models"
            className="field"
            value={models ?? (isCurrent && info.models_custom ? info.models.join(', ') : '')}
            placeholder={provider.default_models.join(', ')}
            disabled={saving}
            onChange={(e) => setModels(e.target.value)}
          />
          <div>
            <button type="button" className="btn" disabled={saving || !llmDirty} onClick={saveLlm}>
              {isCurrent ? 'Zapisz klucz i modele' : 'Zapisz dostawcę, klucz i modele'}
            </button>
          </div>
        </section>
      )}

      <div className="divider" />

      <section className="st-section">
        <div className="st-head">
          <h3 className="title-md">CV</h3>
          <span className="label">{cv?.ready ? cv.filename : 'brak CV'}</span>
        </div>
        <div>
          <button type="button" className="btn" disabled={saving} onClick={() => fileRef.current?.click()}>
            <Upload />
            {cv?.ready ? 'Zastąp CV' : 'Wybierz plik CV'}
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".pdf,.docx,.doc,.txt,.md"
            hidden
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = '';
              if (file) uploadCv(file);
            }}
          />
        </div>
      </section>
    </Modal>
  );
}
