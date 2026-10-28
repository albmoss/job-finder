import { useEffect, useState } from 'react';
import { Check, RotateCcw, TriangleAlert } from 'lucide-react';
import { countFormat } from '../format';
import { publicApi, type AdminOverview, type AdminUser, type SearchStats } from './public_api';

const dateTime = new Intl.DateTimeFormat('pl-PL', { dateStyle: 'medium', timeStyle: 'short' });

function when(value: string | number | null | undefined): string {
  if (value == null || value === '') return '-';
  const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : dateTime.format(date);
}

export function AdminView() {
  const [data, setData] = useState<AdminOverview | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () =>
    publicApi
      .admin()
      .then((overview) => {
        setData(overview);
        setError(null);
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));

  useEffect(() => {
    load();
  }, []);

  const setPlan = async (user: AdminUser, plan: 'free' | 'pro') => {
    try {
      await publicApi.setPlan(user.id, plan);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  if (!data) {
    return error ? (
      <p className="pb-error" role="alert">
        <TriangleAlert />
        {error}
      </p>
    ) : (
      <div className="empty">
        <span className="spinner" />
      </div>
    );
  }

  const { offers, scrapers, users, searches, limits, connections, waitlist } = data;
  const cost = (stats: Record<'guest' | 'pro', SearchStats>) =>
    ((stats.guest.tokens + stats.pro.tokens) * data.usd_per_mtok) / 1e6;

  return (
    <div className="pb-admin">
      <section className="pb-account-head">
        <div>
          <h1 className="pb-heading">Panel administratora</h1>
          <p className="muted">
            {countFormat.format(users.length)} kont · {countFormat.format(offers.total ?? 0)} ofert w bazie
          </p>
        </div>
        <button type="button" className="btn btn-sm" onClick={load}>
          <RotateCcw />
          Odśwież
        </button>
      </section>
      {error && (
        <p className="pb-error" role="alert">
          <TriangleAlert />
          {error}
        </p>
      )}

      <section className="pb-admin-grid">
        <div className="panel pb-admin-card">
          <h2 className="title-md">Połączenia</h2>
          <ul className="pb-checks">
            {connections.map((c) => (
              <li key={c.name} className={c.ok ? 'is-ok' : 'is-warn'}>
                {c.ok ? <Check /> : <TriangleAlert />}
                <div>
                  <strong>{c.name}</strong>
                  <span>{c.detail}</span>
                </div>
              </li>
            ))}
          </ul>
        </div>

        <div className="panel pb-admin-card">
          <h2 className="title-md">Wyszukiwania</h2>
          <table className="pb-table">
            <thead>
              <tr>
                <th />
                <th>24 h</th>
                <th>7 dni</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>Darmowe (udane / wszystkie)</td>
                <td>
                  {searches.day.guest.ok} / {searches.day.guest.total}
                </td>
                <td>
                  {searches.week.guest.ok} / {searches.week.guest.total}
                </td>
              </tr>
              <tr>
                <td>Pro (udane / wszystkie)</td>
                <td>
                  {searches.day.pro.ok} / {searches.day.pro.total}
                </td>
                <td>
                  {searches.week.pro.ok} / {searches.week.pro.total}
                </td>
              </tr>
              <tr>
                <td>Koszt Jev (szacunek)</td>
                <td>${cost(searches.day).toFixed(3)}</td>
                <td>${cost(searches.week).toFixed(3)}</td>
              </tr>
            </tbody>
          </table>
          <p className="faint pb-admin-note">
            Dzienny limit serwisu: {limits.used_today} / {limits.daily} od startu procesu. Pro: {limits.pro_scored} ocen na
            wyszukiwanie, {limits.pro_daily} wyszukiwania dziennie na konto. Koszt liczony z tokenów wejścia Jev po $
            {data.usd_per_mtok}/Mtok, bez odczytu CV.
          </p>
        </div>
      </section>

      <section className="panel pb-admin-card">
        <h2 className="title-md">Baza ofert</h2>
        <p className="muted">
          {offers.snapshot ?? 'brak kopii'} · opublikowana {when(offers.published_at)}
        </p>
        <table className="pb-table">
          <thead>
            <tr>
              <th>Portal</th>
              <th className="num">Ofert</th>
              <th>Ostatnio widziana oferta</th>
            </tr>
          </thead>
          <tbody>
            {offers.sources.map((s) => (
              <tr key={s.source}>
                <td>{s.source || '(bez źródła)'}</td>
                <td className="num">{countFormat.format(s.offers)}</td>
                <td>{when(s.last_seen)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {offers.sources.length === 0 && <p className="faint">Kopia bez metadanych. Opublikuj bazę ponownie.</p>}
      </section>

      <section className="panel pb-admin-card">
        <h2 className="title-md">Scrapery</h2>
        <table className="pb-table">
          <thead>
            <tr>
              <th>Źródło</th>
              <th>Zakres</th>
              <th>Ostatni przebieg</th>
              <th>Stan</th>
              <th className="num">Ofert</th>
              <th className="num">Mediana</th>
            </tr>
          </thead>
          <tbody>
            {scrapers.map((s) => (
              <tr key={`${s.name}|${s.scope}`}>
                <td>{s.name}</td>
                <td>{s.scope}</td>
                <td>{when(s.last_run)}</td>
                <td className={s.status === 'success' ? '' : 'warn'}>{s.status ?? '-'}</td>
                <td className="num">{s.count == null ? '-' : countFormat.format(s.count)}</td>
                <td className="num">{s.median == null ? '-' : countFormat.format(s.median)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {scrapers.length === 0 && <p className="faint">Brak stanu scraperów w kopii bazy.</p>}
      </section>

      <section className="panel pb-admin-card">
        <h2 className="title-md">Użytkownicy</h2>
        <table className="pb-table">
          <thead>
            <tr>
              <th>E-mail</th>
              <th>Plan</th>
              <th>Założone</th>
              <th>Ostatnie logowanie</th>
              <th>CV</th>
              <th className="num">Ofert</th>
              <th className="num">Wyszukiwań Pro</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id}>
                <td>{u.email}</td>
                <td>{u.admin ? 'admin' : u.plan === 'pro' ? 'Pro' : 'darmowy'}</td>
                <td>{when(u.created_at)}</td>
                <td>{when(u.last_login)}</td>
                <td>{u.has_cv ? 'tak' : 'nie'}</td>
                <td className="num">{u.matches}</td>
                <td className="num">{u.pro_searches}</td>
                <td className="num">
                  {!u.admin && (
                    <button type="button" className="btn btn-sm" onClick={() => setPlan(u, u.plan === 'pro' ? 'free' : 'pro')}>
                      {u.plan === 'pro' ? 'Odbierz Pro' : 'Nadaj Pro'}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {users.length === 0 && <p className="faint">Nikt jeszcze nie założył konta.</p>}
      </section>

      {waitlist.length > 0 && (
        <section className="panel pb-admin-card">
          <h2 className="title-md">Lista oczekujących sprzed kont</h2>
          <table className="pb-table">
            <tbody>
              {waitlist.map((w) => (
                <tr key={w.email}>
                  <td>{w.email}</td>
                  <td>{when(w.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}
