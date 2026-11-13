import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

type Status = 'queued' | 'submitting' | 'waiting_snapshot' | 'ranking' |
  'downloading' | 'complete' | 'partial' | 'failed' | 'cancelled' |
  'interrupted' | 'trigger_unknown' | 'running';
interface Post {
  shortcode: string;
  url: string;
  views: number;
  published_at: string;
  status: string;
  error: string | null;
}
interface Account {
  username: string;
  profile_url: string;
  status: Status;
  snapshot_id: string | null;
  observed: number;
  eligible: number;
  selected: number;
  downloaded: number;
  cached: number;
  failed: number;
  error: string | null;
  posts: Post[];
}
interface ZipBatch {
  number: number;
  name: string;
  count: number;
  bytes: number;
  status: string;
}
interface Run {
  id: string;
  created_at: string;
  status: Status;
  since: string;
  until: string;
  scan_limit_per_account: number;
  accounts: Account[];
  archives?: ZipBatch[];
  archive_status?: 'pending' | 'building' | 'ready' | 'failed';
  archive_error?: string | null;
}
interface HistoryItem {
  id: string;
  created_at: string;
  status: Status;
  total_accounts: number;
}
interface Provider {
  ready: boolean;
  configured: boolean;
  enabled: boolean;
  rights_verified: boolean;
}
interface Health {
  provider: Provider;
  output_directory: string;
}
interface Diagnostic {
  ok: boolean;
  code: string;
  message: string;
  http_status: number | null;
  paid: boolean;
  key_verified: boolean;
}
interface ApiResponse<T> {
  ok: boolean;
  error?: string;
  run?: T;
  runs?: HistoryItem[];
  provider?: Provider;
  output_directory?: string;
  diagnostic?: Diagnostic;
  id?: string;
}
const base = '/api/instagram-top100';

async function api<T>(path: string, payload?: Record<string, unknown>): Promise<T> {
  const response = await fetch(base + path, {
    method: payload ? 'POST' : 'GET',
    cache: 'no-store',
    credentials: 'same-origin',
    headers: payload ? { 'Content-Type': 'application/json', 'X-ContentMachine': '1' } : undefined,
    body: payload ? JSON.stringify(payload) : undefined,
  });
  const data = (await response.json()) as ApiResponse<T>;
  if (!response.ok || !data.ok) throw new Error(data.error || 'Сервер не ответил.');
  return data as T;
}

const labels: Record<Status, string> = {
  queued: 'В очереди', submitting: 'Запрос Bright Data',
  waiting_snapshot: 'Ожидание Bright Data', ranking: 'Отбор топа',
  downloading: 'Скачивание MP4', complete: 'Готово',
  partial: 'Частично', failed: 'Ошибка', cancelled: 'Остановлено',
  interrupted: 'Приостановлено', trigger_unknown: 'Проверить платный запрос',
  running: 'Выполняется',
};
const num = (n: number) => n.toLocaleString('ru-RU');
const currentYear = new Date().getFullYear();

function App(): React.JSX.Element {
  const [input, setInput] = useState('');
  const [scanLimit, setScanLimit] = useState(500);
  const [confirmPaid, setConfirmPaid] = useState(false);
  const [confirmRights, setConfirmRights] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const [brightdataKey, setBrightdataKey] = useState('');
  const [keySaving, setKeySaving] = useState(false);
  const [keyNotice, setKeyNotice] = useState('');
  const [diagnosing, setDiagnosing] = useState(false);
  const [diagnostic, setDiagnostic] = useState<Diagnostic | null>(null);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const candidates = useMemo(
    () => Array.from(new Set(input.split(/[\s,;]+/).map(x => x.trim()).filter(Boolean))),
    [input],
  );
  const metrics = useMemo(() => {
    const a = run?.accounts || [];
    return {
      accounts: a.length,
      finished: a.filter(x => x.status === 'complete').length,
      found: a.reduce((s, x) => s + x.eligible, 0),
      selected: a.reduce((s, x) => s + x.selected, 0),
      downloads: a.reduce((s, x) => s + x.downloaded, 0),
      cached: a.reduce((s, x) => s + x.cached, 0),
      failed: a.reduce((s, x) => s + x.failed, 0),
    };
  }, [run]);
  const running = run?.status === 'running';

  async function refresh(chosenId?: string | null): Promise<void> {
    try {
      const [h, list] = await Promise.all([
        api<ApiResponse<Health>>('/health'),
        api<ApiResponse<Run>>('/runs'),
      ]);
      setHealth({ provider: h.provider as Provider, output_directory: h.output_directory as string });
      const items = list.runs || [];
      setHistory(items);
      const id = chosenId === undefined ? (runId || items[0]?.id) : chosenId;
      if (id) {
        const answer = await api<ApiResponse<Run>>('/status?id=' + encodeURIComponent(id));
        setRun(answer.run || null);
        setRunId(id);
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Ошибка соединения.');
    }
  }

  useEffect(() => { void refresh(); }, []);
  useEffect(() => {
    const timer = window.setInterval(() => { void refresh(); }, 2500);
    return () => window.clearInterval(timer);
  }, [runId]);

  async function saveKey() {
    if (!brightdataKey.trim() || keySaving) return;
    setKeySaving(true);
    setKeyNotice('');
    setError('');
    try {
      await api<ApiResponse<Health>>('/key', { key: brightdataKey });
      setBrightdataKey(''); // Never keep the secret in the visible input after saving.
      setDiagnostic(null); // Saved token has not been checked.
      setKeyNotice('Ключ сохранён в защищённом хранилище Windows.');
      await refresh();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Не удалось сохранить API-ключ.');
    } finally {
      setKeySaving(false);
    }
  }

  async function checkConnection() {
    setDiagnosing(true);
    setDiagnostic(null);
    setError('');
    try {
      const answer = await api<ApiResponse<Diagnostic>>('/diagnostics', {});
      setDiagnostic(answer.diagnostic || null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Диагностика соединения недоступна.');
    } finally {
      setDiagnosing(false);
    }
  }

  async function action(path: string, payload: Record<string, unknown>) {
    setError('');
    setBusy(true);
    try {
      const response = await api<ApiResponse<Run>>(path, payload);
      await refresh(response.id || runId);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Ошибка запуска.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand"><div className="brand-icon">CM</div>
          <span>Content Machine <span className="separator">/</span> Research Tools</span>
        </div>
        <div className="header-right">
          <span className={'live-pill ' + (health?.provider.configured ? 'online' : 'offline')}>
            <span className="dot" />Bright Data {health?.provider.configured ? 'ключ сохранён' : 'нет ключа'}
          </span>
          <span className="version">LOCAL TOOL · 01</span>
        </div>
      </header>

      <main className="content">
        <div className="eyebrow">INSTAGRAM INTELLIGENCE</div>
        <div className="hero">
          <div><h1>Reels <span>Top 100</span></h1>
            <p className="lead">Сбор и загрузка самых просматриваемых Reels по аккаунтам.
              Отбор по фактическим просмотрам за период с 1 ноября {currentYear} года.</p>
          </div>
          <div className="hero-graphic"><div className="disc">100<span>TOP</span></div></div>
        </div>

        <div className="layout">
          <section className="panel setup">
            <div className="panel-heading"><div className="step">01</div>
              <div><h2>Аккаунты для анализа</h2><p>Вставь ники или ссылки через пробел, запятую или с новой строки</p></div>
            </div>
            <div className="api-key-panel">
              <div className="key-heading">
                <label htmlFor="brightdata-key" className="field-label">BRIGHT DATA API KEY</label>
                <span className={'key-state ' + (health?.provider.configured ? 'saved' : '')}>
                  {health?.provider.configured ? 'Ключ сохранён' : 'Требуется ключ'}
                </span>
              </div>
              <div className="key-controls">
                <input id="brightdata-key" type="password" value={brightdataKey}
                  onChange={e => { setBrightdataKey(e.target.value); setKeyNotice(''); }}
                  autoComplete="off" spellCheck={false}
                  placeholder={health?.provider.configured ? 'Вставь новый ключ для замены' : 'Вставь API-ключ Bright Data'}
                  onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); void saveKey(); } }} />
                <button type="button" className="save-key" disabled={keySaving || !brightdataKey.trim()}
                  onClick={() => void saveKey()}>{keySaving ? 'Сохраняю…' : 'Сохранить'}</button>
              </div>
              {keyNotice && <p className="key-success" role="status">{keyNotice}</p>}
              <div className="diagnostic-controls">
                <button className="outline diagnostic-button" type="button"
                  disabled={diagnosing || !health?.provider.configured}
                  onClick={() => void checkConnection()}>
                  {diagnosing ? 'Проверяю соединение…' : 'Проверить подключение (без оплаты)'}
                </button>
                {diagnostic && <div role="status" className={'diagnostic-result ' + (diagnostic.ok ? 'diagnostic-ok' : 'diagnostic-failed')}>
                  {diagnostic.message}
                  {diagnostic.http_status !== null && <span> HTTP {diagnostic.http_status}</span>}
                </div>}
              </div>
              <p className="small-muted">Ключ хранится зашифрованным на ноутбуке.
                Проверка соединения не запускает платный сбор и не гарантирует права на dataset.</p>
            </div>
            <label htmlFor="accounts" className="field-label">INSTAGRAM USERNAMES</label>
            <textarea id="accounts" value={input} onChange={e => setInput(e.target.value)}
              placeholder={'@account_one\n@account_two\nhttps://instagram.com/account_three/'} rows={8} />
            <div className="inline-stats"><span>Указано аккаунтов: <strong>{num(candidates.length)}</strong></span>
              <span>Максимум: <strong>100 Reels / аккаунт</strong></span></div>
            <div className="form-grid">
              <div><label className="field-label" htmlFor="scan">ГЛУБИНА ПОИСКА / АККАУНТ</label>
                <input id="scan" type="number" min={100} max={5000} value={scanLimit}
                  onChange={e => setScanLimit(Number(e.target.value))} />
                <p className="small-muted">Bright Data может вернуть меньше указанного числа.</p>
              </div>
              <div className="period"><span className="field-label">ДИАПАЗОН ПУБЛИКАЦИЙ</span>
                <strong>01.11.{currentYear} — сегодня</strong>
                <p className="small-muted">Дата и просмотры должны быть подтверждены.</p></div>
            </div>
            <div className="budget">
              <div><strong>Потенциальный объём сбора</strong>
                <p>До {num(candidates.length * scanLimit)} записей Bright Data.
                  Фактическая оплата зависит от тарифа и результатов поставщика.</p></div>
              <span className="budget-icon">◎</span>
            </div>
            <label className="checkbox"><input type="checkbox" checked={confirmPaid}
                onChange={e => setConfirmPaid(e.target.checked)} />
              <span>Подтверждаю возможные платные запросы Bright Data</span></label>
            <label className="checkbox"><input type="checkbox" checked={confirmRights}
                onChange={e => setConfirmRights(e.target.checked)} />
              <span>Подтверждаю право скачивать и хранить выбранные публичные MP4</span></label>
            <button className="primary" disabled={busy || running || !candidates.length ||
              !confirmPaid || !confirmRights || !health?.provider.configured ||
              (diagnostic !== null && !diagnostic.ok) ||
              !Number.isInteger(scanLimit) || scanLimit < 100 || scanLimit > 5000}
              onClick={() => void action('/start', { usernames: input, scan_limit: scanLimit,
                confirm_paid: true, confirm_rights: true })}>
              {busy ? 'Обработка…' : 'Начать сбор и скачивание'} <span>→</span>
            </button>
            {!health?.provider.configured && <p className="warning">Сначала вставь и сохрани API-ключ Bright Data
              в поле выше. Платный сбор начинается только после отдельного подтверждения запуска.</p>}
            <p className="fine-print">Платный запуск создаётся только после нажатия кнопки.
              При неизвестном результате запроса автоматическая повторная оплата исключена.</p>
          </section>

          <section className="panel dashboard">
            <div className="panel-heading"><div className="step">02</div>
              <div><h2>Мониторинг</h2><p>Ход обработки и результаты по каждому аккаунту</p></div>
            </div>
            {run ? (
              <>
                <div className="current">
                  <div><div className="overline">ТЕКУЩИЙ ЗАПУСК</div>
                    <strong>{labels[run.status]}</strong>
                    <p>{run.since} — {run.until}</p></div>
                  <div className="run-actions">
                    {running && <button className="outline" onClick={() => void action('/cancel', { id: run.id })} disabled={busy}>Остановить</button>}
                    {!running && <button className="outline" onClick={() => void action('/resume', { id: run.id })} disabled={busy}>
                      {run.status === 'complete' ? 'Проверить ZIP' : 'Продолжить'}
                    </button>}
                  </div>
                </div>
                <div className="kpis">
                  <div><span>АККАУНТЫ</span><strong>{metrics.finished} / {metrics.accounts}</strong></div>
                  <div><span>НАЙДЕНО REELS</span><strong>{num(metrics.found)}</strong></div>
                  <div><span>В ТОП-100</span><strong>{num(metrics.selected)}</strong></div>
                  <div><span>MP4 ГОТОВО</span><strong>{num(metrics.downloads + metrics.cached)}</strong></div>
                </div>
                <section className="zip-exports" aria-label="ZIP архивы видео">
                  <div className="zip-heading">
                    <div><span className="overline">ЭКСПОРТ MP4</span>
                      <h3>ZIP-батчи по 100 видео</h3></div>
                    <strong>{num((run.archives || []).reduce((sum, x) => sum + x.count, 0))} MP4 упаковано</strong>
                  </div>
                  {(run.archives || []).length > 0 ? <div className="zip-list">
                    {(run.archives || []).map(batch => (
                      <a className="zip-link" key={batch.number}
                        href={base + '/archive?id=' + encodeURIComponent(run.id) + '&batch=' + batch.number}
                        download={'instagram_reels_' + run.id.slice(0, 8) + '_batch_' + String(batch.number).padStart(3, '0') + '.zip'}>
                        <span className="zip-symbol">ZIP</span>
                        <span className="zip-details"><strong>Батч {String(batch.number).padStart(3, '0')}</strong>
                          <small>{batch.count} MP4{batch.count < 100 ? ' · неполный батч' : ''} · {(batch.bytes / 1024 / 1024).toFixed(1)} МБ</small></span>
                        <span className="zip-download">Скачать ↓</span>
                      </a>
                    ))}
                  </div> : <p className="zip-placeholder">Первый ZIP появится после 100 загруженных MP4.
                    Если итоговых видео меньше 100, архив создастся при завершении.</p>}
                  {run.archive_status === 'building' && <p className="zip-progress">Упаковка ZIP продолжается…</p>}
                  {run.archive_error && <p className="zip-error">Ошибка ZIP: {run.archive_error}</p>}
                  <p className="zip-note">Все архивы сохраняются отдельно. Оригинальные MP4 остаются в InstagramArchive.</p>
                </section>
                <div className="items-heading"><h3>Аккаунты</h3><span>{num(metrics.failed)} ошибок загрузки</span></div>
                <div className="accounts">
                  {run.accounts.map(account => (
                    <div className="account" key={account.username}>
                      <button className="account-top" onClick={() => setExpanded(
                        expanded === account.username ? null : account.username)}>
                        <span className="avatar">@</span>
                        <span className="account-text"><strong>@{account.username}</strong>
                          <small>{num(account.downloaded + account.cached)} / {account.selected} MP4</small></span>
                        <span className={'status ' + account.status}>{labels[account.status]}</span>
                        <span className="chevron">{expanded === account.username ? '▴' : '▾'}</span>
                      </button>
                      {expanded === account.username && <div className="account-details">
                        <p>Получено записей: {num(account.observed)} · Отобрано с просмотрами:
                          {num(account.eligible)} · Скачано: {num(account.downloaded)} · Уже на диске: {num(account.cached)}</p>
                        {account.error && <p className="error-text">{account.error}</p>}
                        {account.posts.length > 0 && <div className="posts">
                          {account.posts.map((post, i) => <div className="post" key={post.shortcode}>
                            <span className="post-rank">{String(i + 1).padStart(2, '0')}</span>
                            <a href={post.url} target="_blank" rel="noreferrer">{post.shortcode}</a>
                            <span>{num(post.views)} просмотров</span>
                            <span className="post-status">{post.status}</span>
                            {post.error && <span className="post-error" title={post.error}>Ошибка</span>}
                          </div>)}
                        </div>}
                      </div>}
                    </div>
                  ))}
                </div>
              </>
            ) : <div className="empty"><div className="empty-symbol">↗</div>
                <h3>Здесь появятся результаты</h3>
                <p>Добавь аккаунты и запусти сбор. Все этапы сохраняются на локальном компьютере.</p>
              </div>}
            <div className="history"><h3>История запусков</h3>
              <select value={runId || ''} onChange={e => {
                setExpanded(null); setRunId(e.target.value); void refresh(e.target.value);
              }}>
                <option value="" disabled>Выбери запуск</option>
                {history.map(h => <option key={h.id} value={h.id}>{h.created_at.slice(0, 16).replace('T', ' ')} · {h.total_accounts} аккаунтов · {labels[h.status]}</option>)}
              </select>
            </div>
          </section>
        </div>
        {error && <div role="alert" className="toast">{error}<button onClick={() => setError('')}>×</button></div>}
        <footer><span>CONTENT MACHINE · PRIVATE LOCAL WORKSPACE</span>
          <span>MP4: ~/Downloads/InstagramArchive/ &nbsp; · &nbsp; Jobs: {health?.output_directory || '...'}</span></footer>
        <div className="note">Топ определяется только по записям, вернувшимся от поставщика. Неизвестные даты,
          просмотры и неподтверждённая принадлежность аккаунту исключаются из рейтинга.</div>
      </main>
    </div>
  );
}

createRoot(document.getElementById('root')!).render(<App />);
