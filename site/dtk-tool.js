'use strict';
/* DTK v5 local tool: explicit submit, safe status poll, JSON diagnostics only. */
(() => {
  const el = id => document.getElementById(id);
  const fields = {
    search_posts: ['keyword','Поисковый запрос'],
    search_users: ['keyword','Поисковый запрос'],
    video: ['aweme_id','ID ролика'],
    related_posts: ['aweme_id','ID ролика'],
    comments: ['aweme_id','ID ролика'],
    comment_replies: ['aweme_id','ID ролика'],
    user: ['sec_user_id','secUid автора'],
    user_posts: ['sec_user_id','secUid автора'],
    hashtag: ['name','Название хэштега'],
    hashtag_posts: ['hashtag_id','ID хэштега'],
    music: ['music_id','ID звука'],
    music_posts: ['music_id','ID звука'],
  };
  const paginated = new Set(['search_posts','search_users','comments',
    'comment_replies','user_posts','hashtag_posts','music_posts']);
  let lastTask = null;
  let pollCount = 0;
  let polling = false;
  let busy = false;
  const print = data => { el('dtkResult').textContent = JSON.stringify(data,null,2); };
  const notice = value => { el('dtkNotice').textContent = value; };
  async function request(path, body) {
    const result = await fetch(path, body === undefined ? {cache:'no-store'} : {
      method:'POST',
      headers:{'Content-Type':'application/json','X-ContentMachine':'1'},
      body:JSON.stringify(body)
    });
    let data;
    try { data = await result.json(); } catch { throw Error('Некорректный JSON Content Machine.'); }
    if(!result.ok || !data.ok) throw Error(data.error?.message || 'Ошибка '+result.status);
    return data;
  }
  function inputs() {
    const action = el('dtkAction').value;
    const [name,label] = fields[action];
    el('dtkValueLabel').textContent = label;
    el('dtkValue').placeholder = label;
    const hasExtra = action === 'comment_replies';
    el('dtkExtraLabel').hidden = !hasExtra;
    el('dtkExtra').hidden = !hasExtra;
    el('dtkExtra').required = hasExtra;
    el('dtkCursor').disabled = !paginated.has(action);
    return name;
  }
  async function check() {
    try {
      const data = await request('/api/tools/dtk/status');
      const ok = data.available && data.read_scope;
      const status = el('dtkStatus');
      status.textContent = ok ? 'Готов к запросам' : (data.warning || 'Недоступен');
      status.classList.toggle('is-connected',ok);
      status.classList.toggle('is-missing',!ok);
      el('dtkRun').disabled = !ok;
      print({tool_id:data.tool_id,configured:data.configured,available:data.available,
        read_scope:data.read_scope,operations:data.operations,warning:data.warning||null});
    } catch(error) {notice(error.message);}
  }
  async function task() {
    if(!lastTask || polling)return;
    polling=true;
    try {
      const data=await request('/api/tools/dtk/tasks/'+encodeURIComponent(lastTask));
      print(data);
      notice('Задание '+lastTask+': '+data.state);
      if(['done','failed','cancelled','canceled'].includes(data.state)) {
        pollCount=100;
      }
    }catch(error){notice(error.message);pollCount=100;}
    finally{polling=false;}
  }
  el('dtkAction').addEventListener('change',inputs);
  el('dtkCheck').addEventListener('click',check);
  el('dtkRefreshTask').addEventListener('click',task);
  el('dtkForm').addEventListener('submit', async e => {
    e.preventDefault();
    if(busy)return;
    busy=true;
    el('dtkRun').disabled=true;
    const action=el('dtkAction').value;
    const name=fields[action][0];
    const params={[name]:el('dtkValue').value.trim()};
    if(action==='comment_replies') params.comment_id=el('dtkExtra').value.trim();
    const cursor=el('dtkCursor').value.trim();
    if(cursor && paginated.has(action))params.cursor=cursor;
    try {
      const data=await request('/api/tools/dtk/run',{action,params});
      lastTask=data.task_id;
      pollCount=0;
      el('dtkRefreshTask').disabled=false;
      print(data);
      notice('Задание принято: '+lastTask+'. Проверяем состояние.');
      await task();
    }catch(error){notice(error.message);}
    finally{busy=false;el('dtkRun').disabled=false;}
  });
  document.addEventListener('cm-discovery-open', check);
  // Polling only reads existing DTK tasks; never repeats submitted requests.
  setInterval(() => {
    if(lastTask && pollCount<120 && !el('discoveryView').hidden) {
      pollCount++;
      void task();
    }
  },4000);
  inputs();
})();
