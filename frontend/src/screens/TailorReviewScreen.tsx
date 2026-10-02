import { useEffect, useState } from 'react';
import { CircleAlert } from 'lucide-react';
import { api, errorMessage } from '../api';
import { useApp } from '../app_context';
import { navigate, paths } from '../router';
import type { CvVersionDetail } from '../types';
import { TailorHeader, stepsFor, useOfferPath, type Step } from './tailor/common';
import { ReviewView } from './tailor/ReviewView';
import { FailedCard, ReadyCard, RunningCard } from './tailor/StateCards';
import type { ScreenProps } from './types';
import '../styles/tailor.css';

const POLL_MS = 2000;

export function TailorReviewScreen({ route }: ScreenProps) {
  const id = route.params.id;
  const { bumpData } = useApp();
  const [version, setVersion] = useState<CvVersionDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [preview, setPreview] = useState(false);
  const offerPath = useOfferPath(version?.link);
  const status = version?.status;

  useEffect(() => {
    let alive = true;
    api
      .getCvVersion(id)
      .then((v) => alive && setVersion(v))
      .catch((err) => alive && setLoadError(errorMessage(err)));
    return () => {
      alive = false;
    };
  }, [id]);

  useEffect(() => {
    if (status !== 'running') return;
    let alive = true;
    const timer = window.setInterval(async () => {
      try {
        const next = await api.getCvVersion(id);
        if (!alive) return;
        setVersion(next);
        if (next.status !== 'running') bumpData();
      } catch {
        return;
      }
    }, POLL_MS);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [id, status, bumpData]);

  if (version && (version.status === 'draft' || (version.status === 'ready' && preview))) {
    return (
      <ReviewView
        version={version}
        offerPath={offerPath}
        onUpdate={setVersion}
        readOnly={version.status === 'ready'}
        onClosePreview={() => setPreview(false)}
      />
    );
  }

  const step: Step = version?.status === 'ready' ? 'ready' : 'settings';

  return (
    <div className="tl-screen">
      <TailorHeader
        backLabel="Wróć do oferty"
        onBack={() => navigate(version ? offerPath : paths.cv)}
        title="CV pod tę ofertę"
        subtitle={version ? `${version.title} w ${version.company}` : undefined}
        steps={stepsFor(step)}
      />
      <div className="tl-stage">
        {loadError ? (
          <section className="panel tl-card">
            <CircleAlert className="tl-card-icon warn" />
            <h2 className="title-lg">Nie udało się wczytać wersji CV</h2>
            <p className="tl-card-line">{loadError}</p>
          </section>
        ) : !version ? (
          <p className="muted">Wczytywanie…</p>
        ) : version.status === 'running' ? (
          <RunningCard version={version} onUpdate={setVersion} />
        ) : version.status === 'failed' ? (
          <FailedCard version={version} onUpdate={setVersion} />
        ) : (
          <ReadyCard version={version} offerPath={offerPath} previewOpen={preview} onTogglePreview={() => setPreview((p) => !p)} />
        )}
      </div>
    </div>
  );
}
