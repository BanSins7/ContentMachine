import { useEffect, useState, type FormEvent } from 'react';
import { Check, ChevronLeft, KeyRound, LoaderCircle, LockKeyhole, ShieldCheck } from 'lucide-react';

type SettingsStatus = {
  openrouter_saved: boolean;
  brightdata_saved: boolean;
  brightdata_authorized: boolean;
  openrouter_configured: boolean;
  brightdata_configured: boolean;
  brightdata_ready: boolean;
  brightdata_rights_verified: boolean;
  no_keys_returned: boolean;
  storage: string;
};
const endpoint = '/api/studio/settings';

async function settingsRequest(init?:RequestInit):Promise<SettingsStatus> {
  const response=await fetch(endpoint,{cache:'no-store',...init});
  const payload=await response.json() as SettingsStatus & {error?:string};
  if (!response.ok) throw new Error(payload.error || 'Ошибка доступа к настройкам.');
  return payload;
}

export function StudioSettings({onBack,onSaved}:{
  onBack:()=>void;
  onSaved:()=>void;
}) {
  const [status,setStatus]=useState<SettingsStatus|null>(null);
  const [openrouter,setOpenrouter]=useState('');
  const [brightdata,setBrightdata]=useState('');
  const [authorized,setAuthorized]=useState(false);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [notice,setNotice]=useState('');

  useEffect(()=>{
    let mounted=true;
    void settingsRequest().then(next=>{
      if (mounted) {setStatus(next);setAuthorized(next.brightdata_authorized);}
    }).catch(e=>{if(mounted)setError((e as Error).message);});
    return ()=>{mounted=false;};
  },[]);

  async function save(e:FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError('');setNotice('');
    setBusy(true);
    try {
      const next=await settingsRequest({
        method:'POST',
        headers:{'Content-Type':'application/json','X-ContentMachine':'1'},
        body:JSON.stringify({
          openrouter_api_key:openrouter,
          brightdata_api_token:brightdata,
          brightdata_authorized:authorized
        })
      });
      setStatus(next);
      setOpenrouter('');
      setBrightdata('');
      setNotice('Настройки сохранены на этом компьютере.');
      onSaved();
    } catch(e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return <section className="settings-page" aria-labelledby="settings-heading">
    <div className="settings-heading">
      <button type="button" className="settings-back" onClick={onBack}>
        <ChevronLeft size={16}/> Назад к Studio
      </button>
      <div className="eyebrow"><span className="eyebrow-line"/> ПОДКЛЮЧЕНИЯ</div>
      <h1 id="settings-heading">Настройки<span className="accent-dot">.</span></h1>
      <p>Два ключа для автоматического сбора TikTok-каруселей и анализа Gemini.</p>
    </div>

    <div className="panel settings-panel">
      <div className="panel-heading">
        <div><span className="section-index">01 / API-КЛЮЧИ</span><h2>Подключения сервисов</h2></div>
        <LockKeyhole size={20} className="quiet-icon"/>
      </div>
      <form className="settings-form" onSubmit={e=>{void save(e);}}>
        <div className="settings-field">
          <div className="settings-field-heading">
            <label htmlFor="openrouter-key">OpenRouter API Key</label>
            <span className={status?.openrouter_configured?'settings-ready':'settings-unset'}>
              <span className="settings-dot"/>
              {status?.openrouter_configured?'Ключ настроен':'Не настроен'}
            </span>
          </div>
          <div className="settings-input">
            <KeyRound size={17}/>
            <input id="openrouter-key" type="password" autoComplete="new-password"
              spellCheck={false} value={openrouter} onChange={e=>setOpenrouter(e.target.value)}
              placeholder={status?.openrouter_configured?'Оставь пустым, чтобы сохранить текущий':'Вставь ключ OpenRouter'}
              maxLength={4096}/>
          </div>
          <p>Gemini 3.8 Flash. Пустое поле при сохранении не заменяет существующий ключ.</p>
        </div>

        <div className="settings-divider"/>

        <div className="settings-field">
          <div className="settings-field-heading">
            <label htmlFor="brightdata-key">Bright Data API Token</label>
            <span className={status?.brightdata_ready?'settings-ready':'settings-unset'}>
              <span className="settings-dot"/>
              {status?.brightdata_ready?'Готов к сбору':
                status?.brightdata_configured?'Требуется разрешение':'Не настроен'}
            </span>
          </div>
          <div className="settings-input">
            <KeyRound size={17}/>
            <input id="brightdata-key" type="password" autoComplete="new-password"
              spellCheck={false} value={brightdata} onChange={e=>setBrightdata(e.target.value)}
              placeholder={status?.brightdata_configured?'Оставь пустым, чтобы сохранить текущий':'Вставь токен Bright Data'}
              maxLength={4096}/>
          </div>
          <p>Используется для чтения публичных публикаций и статистики TikTok.</p>
          <label className="settings-consent">
            <input type="checkbox" checked={authorized}
              onChange={e=>setAuthorized(e.target.checked)}/>
            <span>Подтверждаю, что имею право использовать Bright Data
              для сбора публичных данных и разрешаю запуск платных API-запросов.</span>
          </label>
        </div>

        <button className="start-button settings-save" type="submit" disabled={busy}>
          {busy?<LoaderCircle size={17} className="spin"/>:<Check size={17}/>}
          {busy?'Сохраняю...':'Сохранить настройки'}
        </button>
        {notice && <p className="settings-feedback" role="status"><Check size={15}/>{notice}</p>}
        {error && <p className="settings-feedback settings-error" role="alert">{error}</p>}
      </form>
    </div>

    <div className="settings-privacy">
      <ShieldCheck size={18}/>
      <div>
        <strong>Ключи остаются на твоём компьютере</strong>
        <p>Шифруются средствами Windows DPAPI для текущего пользователя.
          В интерфейс возвращается только статус подключения, без значений ключей.
          Сохранение не проверяет баланс или действительность ключа у провайдера.</p>
      </div>
    </div>
  </section>;
}
