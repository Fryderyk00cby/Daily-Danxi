'use strict';
const $=s=>document.querySelector(s);
const fragment=new URLSearchParams(location.hash.slice(1));
if(fragment.has('key')){sessionStorage.setItem('readerKey',fragment.get('key'));history.replaceState(null,'',location.pathname);}
const key=sessionStorage.getItem('readerKey')||'';
let state=null, initialized=false, selected=null, offset=0, seen=new Set(), waiting=false, lastReply='', lastError='', stopped=false;
const numeric=['hours','pages','limit','min_reply','reply_weight','favorite_weight','view_weight'];
async function api(path,data){const res=await fetch('/api/'+path,{method:data===undefined?'GET':'POST',headers:{'X-Reader-Key':key,'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data)});const value=await res.json();if(!res.ok)throw Error(value.error||'请求失败');return value;}
function node(tag,cls,text){const el=document.createElement(tag);if(cls)el.className=cls;if(text!==undefined)el.textContent=text;return el;}
const postTimeFormat=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
function postTime(value){
    let raw=typeof value==='string'?value.trim():'';
    if(!raw)return node('span','post-time','时间未知');
    // The API treats timestamps without an explicit timezone as UTC.
    raw=raw.replace(' ','T');
    if(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/.test(raw))raw+='Z';
    const date=new Date(raw);
    if(Number.isNaN(date.getTime()))return node('span','post-time','时间未知');
    const parts=Object.fromEntries(postTimeFormat.formatToParts(date).map(p=>[p.type,p.value]));
    const el=node('time','post-time',`${parts.year}年${parts.month}月${parts.day}日 ${parts.hour}:${parts.minute}`);
    el.dateTime=date.toISOString();el.title='北京时间（UTC+8）';return el;
}
function tags(container,post){container.replaceChildren();for(const t of post.tags||[])if(t.name)container.append(node('span','tag','#'+t.name));if(!container.childNodes.length)container.append(node('span','tag','暂无标签'));}
function content(p){return p.floors?.first_floor?.content||'接口未返回首层正文';}
function author(f){return f?.anonyname || '接口未提供昵称';}
function rules(){const form=$('#rules'),result={};for(const [k,v] of new FormData(form))result[k]=numeric.includes(k)?Number(v):v;result.webvpn=form.elements.webvpn.checked;return result;}
async function save(){if(!$('#rules').reportValidity())throw Error('请检查筛选参数');await api('settings',rules());}
function render(){const root=$('#posts');clearContent(root);$('#count').textContent=state.posts.length;for(const [i,p] of state.posts.entries()){const card=node('article','card'),top=node('div','card-top');top.append(node('span','rank',`TOP ${String(i+1).padStart(2,'0')} / #${p.hole_id}`),node('span','',`热度 ${p.heat}`));const ts=node('div','tags');tags(ts,p);const text=content(p),bottom=node('div','card-bottom'),button=node('button','read','阅读正文与跟帖 →');button.addEventListener('click',()=>openDetail(p));bottom.append(node('span','',`回复 ${p.reply||0}  ·  浏览 ${p.view||0}  ·  收藏 ${p.favorite_count||0}`),button);card.append(top,node('div','author',author(p.floors?.first_floor)),postTime(p.time_created||p.floors?.first_floor?.time_created),ts,renderContent(text,true),bottom);root.append(card);}if(!state.posts.length){const empty=node('div','empty');empty.append(node('h2','','从一份校园日报开始'),node('p','','点击「一键生成日报」。'));root.append(empty);}}
function addFloor(f,title){if(f.floor_id&&seen.has(f.floor_id))return;if(f.floor_id)seen.add(f.floor_id);const el=node('section','floor');el.append(node('div','meta',`${author(f)}${title?' · '+title:''} · 楼层 ID ${f.floor_id||'未知'}`),postTime(f.time_created),renderContent(f.deleted?'该层已删除':f.content||'（无文本内容）'));$('#floors').append(el);}
function openDetail(p){selected=p;offset=0;seen=new Set();lastReply='';waiting=false;clearContent($('#floors'));$('#detail-title').textContent='洞号 #'+p.hole_id;tags($('#detail-tags'),p);addFloor({...p.floors?.first_floor,content:content(p),time_created:p.time_created||p.floors?.first_floor?.time_created},'楼主 · 首层正文');$('#more').disabled=false;$('#reply-status').textContent='';$('#detail').showModal();loadReplies();}
async function loadReplies(){if(waiting||!selected)return;if(state.busy){$('#reply-status').textContent='其他任务进行中，请稍后加载';return;}waiting=true;$('#more').disabled=true;$('#reply-status').textContent='正在加载跟帖…';try{await api('replies',{id:selected.hole_id,offset});}catch(e){waiting=false;$('#more').disabled=false;$('#reply-status').textContent=e.message;}}
let signature='';
async function poll(){if(stopped)return;try{state=await api('state');if(!initialized){for(const [k,v]of Object.entries(state.rules)){const input=$('#rules').elements[k];if(input){if(input.type==='checkbox')input.checked=v;else input.value=v;}}initialized=true;}$('#status').textContent=state.message;$('#coverage').textContent=`${state.coverage} · 已获取 ${state.total} 条`;$('#error').textContent=state.error;$('#dot').classList.toggle('busy',state.busy);$('#generate').disabled=state.busy;$('#stop').disabled=!state.busy;$('#resume').hidden=!state.can_resume;$('#resume').disabled=state.busy;const sig=JSON.stringify(state.posts);if(sig!==signature){signature=sig;render();}if(state.login_needed&&!$('#login').open)$('#login').showModal();if(!state.login_needed&&$('#login').open)$('#login').close();if(waiting&&!state.busy){waiting=false;const r=state.replies;if(r&&selected&&r.id===selected.hole_id&&r.offset===offset){for(const f of r.floors)addFloor(f);offset+=r.floors.length;$('#more').disabled=!r.floors.length;$('#reply-status').textContent=`已读取 ${offset} 层（含首层）${r.floors.length?'':' · 已到末尾'}`;}else{$('#more').disabled=false;$('#reply-status').textContent=state.error||'加载未完成，请重试';}}}catch(e){$('#status').textContent='本地服务未连接';$('#error').textContent=e.message;}setTimeout(poll,900);}
function action(fn){return async e=>{e?.preventDefault();try{await fn();}catch(err){$('#error').textContent=err.message;}};}
$('#rules').addEventListener('submit',action(async()=>{await save();signature='';}));
$('#generate').addEventListener('click',action(async()=>{await save();await api('generate',{});}));
$('#stop').addEventListener('click',action(()=>api('stop',{})));
$('#resume').addEventListener('click',action(()=>api('resume',{})));
$('#logout').addEventListener('click',action(async()=>{if(confirm('清除树洞和 WebVPN 的本机登录缓存？'))await api('logout',{});}));
$('#shutdown').addEventListener('click',action(async()=>{await api('shutdown',{});stopped=true;$('#status').textContent='本地服务已退出，可以关闭此页面。';document.querySelectorAll('button').forEach(b=>b.disabled=true);}));
$('#close-detail').addEventListener('click',()=>$('#detail').close());$('#more').addEventListener('click',loadReplies);
$('#login-form').addEventListener('submit',action(async()=>{const form=$('#login-form');await api('login',{email:form.elements.email.value,password:form.elements.password.value});form.elements.password.value='';$('#login').close();}));
async function cancelLogin(){await api('login',{cancel:true});$('#login-form').elements.password.value='';$('#login').close();}
$('#cancel-login').addEventListener('click',action(cancelLogin));$('#login').addEventListener('cancel',e=>{e.preventDefault();action(cancelLogin)(e);});poll();
