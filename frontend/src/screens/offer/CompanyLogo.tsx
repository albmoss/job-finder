import { useState } from 'react';
import { Link } from 'lucide-react';

const SOURCE_MARKS: Record<string, string> = {
  JustJoinIT: 'JJIT',
  RocketJobs: 'Rocket',
  'Pracuj.pl': 'pracuj',
  NoFluffJobs: 'NFJ',
  'SOLID.Jobs': 'SOLID',
  'OLX Praca': 'OLX',
  'GoWork.pl': 'GoWork',
  'praca.pl': 'praca',
  'aplikuj.pl': 'aplikuj',
  LinkedIn: 'in',
  Indeed: 'indeed',
  'ATS Feeds': 'ATS',
  Adzuna: 'adzuna',
  Jooble: 'jooble',
  Careerjet: 'CJ',
};

interface CompanyLogoProps {
  url: string | null;
  source: string;
  size: 'sm' | 'lg';
}

export function CompanyLogo({ url, source, size }: CompanyLogoProps) {
  const [failedUrl, setFailedUrl] = useState<string | null>(null);

  if (url && url !== failedUrl) {
    return (
      <span className={`logo-tile logo-${size} has-image`} aria-hidden="true">
        <img
          src={url}
          alt=""
          loading="lazy"
          decoding="async"
          referrerPolicy="no-referrer"
          onError={() => setFailedUrl(url)}
        />
      </span>
    );
  }

  const manual = source === 'Dodane ręcznie' || source.startsWith('Manual');
  const mark = SOURCE_MARKS[source] ?? source.split(/[\s.(]/)[0].slice(0, 6);
  return (
    <span className={`logo-tile logo-${size}`} title={source} aria-hidden="true">
      {manual ? <Link /> : mark}
    </span>
  );
}
