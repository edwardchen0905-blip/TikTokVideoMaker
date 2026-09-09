'use strict';
let shops, products, assets, taskPage, videoPage, publicationQueue, publishPage, config, settingsPage, save, localPage;
// The desktop host will supply the shared production data. No sample business records.
const icons={home:'M3 10 12 3l9 7M5 9v12h5v-7h4v7h5V9',shop:'M3 4h18l-1 6H4L3 4ZM5 10v11h14V10M9 21v-7h6v7M3 10c0 3 4 3 4 0 0 3 5 3 5 0 0 3 5 3 5 0 0 3 4 3 4 0',box:'m3 7 9-4 9 4v11l-9 4-9-4V7Zm0 0 9 4 9-4M12 11v11M7 5l10 4',folder:'M3 6h7l2 3h9v11H3V6Z',image:'M3 3h18v18H3V3Zm0 13 6-6 5 5 3-3 4 4M16 7h.01',music:'M9 18V5l12-2v13M9 5l12-2M5 22c-5 0-5-6 0-6 5 0 5 6 0 6ZM17 20c-5 0-5-6 0-6 5 0 5 6 0 6Z',grid:'M3 3h7v7H3V3Zm11 0h7v7h-7V3ZM3 14h7v7H3v-7Zm11 0h7v7h-7v-7Z',tasks:'M4 3h16v18H4V3ZM8 7h8M8 12h8M8 17h5',video:'M3 5h18v14H3V5Zm6 3 7 4-7 4V8Z',send:'m3 3 18 9-18 9 4-9-4-9Zm4 9h14',settings:'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Zm-2-5h4l1 3 3-1 3 4-2 3 2 3-3 4-3-1-1 3h-4l-1-3-3 1-3-4 2-3-2-3 3-4 3 1 1-3Z',search:'M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14Zm5 12 6 6',plus:'M12 4v16M4 12h16',arrow:'m9 5 7 7-7 7',download:'M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5',check:'m5 12 4 4L20 5',clock:'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Zm0 4v6l4 2',alert:'M12 3 2 21h20L12 3Zm0 6v5m0 3h.01',play:'m7 3 14 9-14 9V3Z',close:'m5 5 14 14M5 19 19 5',trash:'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7',refresh:'M20 7a9 9 0 1 0 1 9M20 3v5h-5',link:'m9 15 6-6M8 16l-2 2a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0M16 8l2-2a4 4 0 0 1 6 6l-5 5a4 4 0 0 1-6 0',list:'M8 5h13M8 12h13M8 19h13M3 5h.01M3 12h.01M3 19h.01',lock:'M6 10h12v11H6V10Zm2 0V7a4 4 0 0 1 8 0v3',upload:'M12 16V3m-5 5 5-5 5 5M4 16v5h16v-5',more:'M5 12h.01M12 12h.01M19 12h.01'};
const icon=n=>`<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${icons[n]||icons.box}"/></svg>`;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const routes=[['home','首页','home'],['shops','店铺商品','shop'],['products','商品库','box'],['assets','素材中心','folder'],['music','音乐中心','music'],['local','本地资料库','list'],['transitions','转场效果','grid'],['tasks','生产任务','tasks'],['videos','视频管理','video'],['publish','发布中心','send'],['settings','设置','settings']];
const defaults={source:'local',music:'none',cacheDays:'7',density:'normal'};
let settings={...defaults};
let route='home',tab='all',view='grid',query='';
const data={products:[],assets:[],tasks:[],videos:[],publications:[],stores:[]};
const unavailable='当前操作不可用，请检查选择项或连接状态';
const btn=(text,action,cls='',disabled=false,ico='')=>`<button class="${cls}" ${disabled?`disabled title="${unavailable}"`:''} ${action?`data-action="${esc(action)}"`:''}>${ico?icon(ico):''}${text}</button>`;
const go=(text,to,cls='',ico='')=>btn(text,'go:'+to,cls,false,ico);
const empty=(title,description,ico='box',action='',cls='')=>`<div class="empty ${cls}"><div class="empty-symbol">${icon(ico)}</div><h3>${title}</h3><p>${description}</p>${action}</div>`;
const heading=(title,sub,actions='')=>`<div class="page-heading"><div><h1>${title}</h1><p class="subtitle">${sub}</p></div><div class="heading-actions">${actions}</div></div>`;
const panel=(title,body,action='',cls='')=>`<section class="panel ${cls}"><div class="panel-title"><h2>${title}</h2>${action}</div>${body}</section>`;
const table=(headers,description='暂无记录')=>`<div class="table-wrap"><table><thead><tr>${headers.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody><tr><td class="table-empty" colspan="${headers.length}">${description}</td></tr></tbody></table></div>`;
const select=(name,options,value,disabled=false)=>`<select aria-label="${({source:'素材来源',music:'音乐方式',cacheDays:'缓存保留天数',download_retention:'下载素材保留',output_retention:'输出视频保留'}[name])||'默认设置'}" data-pref="${name}" ${disabled?'disabled':''}>${options.map(([v,t])=>`<option value="${v}" ${value===v?'selected':''} ${v==='ai'?'disabled':''}>${t}</option>`).join('')}</select>`;
const tabs=(items)=>`<div class="tabs" role="tablist">${items.map(([id,title,count])=>`<button class="tab ${tab===id?'active':''}" role="tab" aria-selected="${tab===id}" data-tab="${id}">${title}${count===undefined?'':` <span class="count">(${count})</span>`}</button>`).join('')}</div>`;
const search=placeholder=>`<label class="search">${icon('search')}<input data-search aria-label="${placeholder}" placeholder="${placeholder}" value="${esc(query)}"></label>`;
const noRecords=()=>query?`没有找到与“${esc(query)}”匹配的记录`:'暂无记录';
const countStats=()=>`<div class="stats">${[['商品总数',data.products.length,'box'],['素材总数',data.assets.filter(a=>!a.deleted).length,'image'],['待生成视频',data.tasks.filter(t=>t.status==='waiting').length,'play'],['已生成视频',data.videos.length,'check'],['失败任务',data.tasks.filter(t=>t.status==='failed').length,'alert']].map(([label,num,ico])=>`<div class="stat"><div class="stat-icon">${icon(ico)}</div><div><label>${label}</label><strong>${num.toLocaleString()}</strong></div></div>`).join('')}</div>`;
function flow(current){return `<div class="flow">${[['shops','同步 / 导入商品','管理商品与图片'],['transitions','选择转场效果','查看真实切换效果'],['tasks','批量生成视频','查看任务与进度'],['videos','管理视频','预览与关联信息'],['publish','发布辅助','管理独立发布任务']].map(([r,n,s],i)=>`<button data-action="go:${r}" class="${current===r?'current':''}"><span class="step-number">${i+1}</span><span><b>${n}</b><small>${s}</small></span></button>`).join('')}</div>`}
function home(){return heading('欢迎使用 TikTokVideoMaker','从店铺商品或本地素材，批量生产商品视频',btn('使用帮助','help','',false,'alert'))+`<div class="dashboard"><div><div class="shortcuts">${[['rose','shop','店铺商品同步','等待连接真实店铺环境','go:shops'],['azure','folder','导入本地素材','图片 / 视频 / 音乐','import'],['purple','grid','选择转场效果','查看图片如何切换','go:transitions'],['teal','tasks','创建生产任务','按商品或素材批量生成','create-task']].map(([c,i,t,s,a])=>`<button class="shortcut ${c}" data-action="${a}"><span class="tile-icon">${icon(i)}</span><span><b>${t}</b><small>${s}</small></span></button>`).join('')}</div>${countStats()}${panel('最近导入的商品',empty('商品将集中显示在这里','导入本地商品，或在连接店铺后同步。每个商品的素材、商品规格 和视频保持关联。','box',go('前往商品库','products','','plus'),'compact'),go('查看全部','products','link','arrow'))}${panel('生产任务 <span class="note">(0)</span>',table(['任务名称','素材来源','对象数量','转场效果','状态','进度','开始时间','操作'],'尚未创建生产任务'),go('查看全部','tasks','link','arrow'))}</div><aside>${config()}${panel('快捷工具',`<div class="quick-tools">${btn(icon('folder')+'<span>素材管理</span><small>整理生产素材</small>','go:assets')}${btn(icon('refresh')+'<span>缓存管理</span><small>查看保留策略</small>','go:settings')}${btn(icon('video')+'<span>输出管理</span><small>查看生成视频</small>','go:videos')}</div>`)}</aside></div>`}
function transitionPage(){
 const chosen=settings.production?.transitions||['none'];
 const shown=(data.transitions||[]).filter(t=>searchable(t.name,t.description));
 return heading('转场效果','只设置图片之间如何切换。可选一种，或按勾选顺序循环使用多种。',btn('保存为默认','save-config')+btn('创建生产任务','create-task','primary',false,'play'))+
 `<div class="toolbar">${search('搜索转场名称或说明…')}<span class="note">已选：${esc(transitionSummary(chosen))}</span></div><div class="transition-grid">${shown.map(t=>`<article class="panel transition-card ${chosen.includes(t.id)?'selected':''}"><h2>${esc(t.name)}</h2><p>${esc(t.description)}</p><div class="actions">${btn('预览实际效果','preview-transition:'+t.id,'',false,'play')}${btn(chosen.includes(t.id)?'已选择 · 取消':'选择此效果','choose-transition:'+t.id,chosen.includes(t.id)?'primary':'')}</div></article>`).join('')}</div>${shown.length?'':empty('没有匹配的转场','请换一个搜索词。','search')}<p class="note">预览使用与视频生产相同的渲染程序。任务中可调整展示时间、转场时间和视频尺寸。</p>`;
}
function render(){route=location.hash.slice(1).split('?')[0]||'home';if(!routes.some(r=>r[0]===route)){location.hash='home';return}const label=routes.find(r=>r[0]===route)[1];document.title=label+' · TikTokVideoMaker';document.getElementById('breadcrumb').textContent='工作空间 / '+label;document.getElementById('top-settings').innerHTML=icon('settings');document.getElementById('navigation').innerHTML=routes.map(([id,title,ico])=>`<a href="#${id}" class="${route===id?'active':''}" ${route===id?'aria-current="page"':''} title="${title}">${icon(ico)}<span>${title}</span></a>`).join('');document.getElementById('page').innerHTML=({home,shops,products,assets,transitions:transitionPage,music:musicPage,local:localPage,tasks:taskPage,videos:videoPage,publish:publishPage,settings:settingsPage}[route])();document.querySelectorAll('.view-toggle button').forEach((b,i)=>b.setAttribute('aria-label',i?'列表视图':'卡片视图'))}
function toast(message){
 const inside=document.getElementById('dialog').open&&document.getElementById('dialog-message');
 if(inside){inside.textContent=message;inside.hidden=false;return}
 const el=document.getElementById('toast');el.textContent=message;el.classList.add('visible');clearTimeout(toast.timer);toast.timer=setTimeout(()=>el.classList.remove('visible'),4000);
}
function fieldError(message,field){const error=new Error(message);error.field=field;return error}
function reportError(error){
 const dialog=document.getElementById('dialog'),box=document.getElementById('dialog-error');
 if(dialog.open&&box){
  box.textContent=displayReason(error.message||String(error));box.hidden=false;
  const field=error.field&&document.getElementById(error.field);
  if(field){for(let p=field.parentElement;p;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true;field.setAttribute('aria-invalid','true');field.focus();field.scrollIntoView({block:'center'});}
  else document.getElementById('dialog-content').scrollTop=0;
 }else toast(error.message||String(error));
}
function modal(title,body,footer=''){
 document.getElementById('dialog-content').innerHTML=`<div class="dialog-heading"><h2>${title}</h2>${btn(icon('close'),'close')}</div><div class="dialog-notices"><p id="dialog-error" role="alert" hidden></p><p id="dialog-message" role="status" hidden></p></div>${body}<div class="dialog-footer">${footer||btn('关闭','close')}</div>`;
 document.querySelector('#dialog .dialog-heading button').setAttribute('aria-label','关闭对话框');document.getElementById('dialog').showModal();
}
document.addEventListener('click',e=>{const b=e.target.closest('[data-action]');if(b&&!b.disabled)actions(b.dataset.action);const t=e.target.closest('[data-tab]');if(t){tab=t.dataset.tab;render()}});
document.addEventListener('change',e=>{if(e.target.matches('[data-pref]')){settings[e.target.dataset.pref]=e.target.value;if(route==='home')render()}});
document.addEventListener('input',e=>{if(e.target.matches('[data-search]')){query=e.target.value;const pos=e.target.selectionStart;render();const next=document.querySelector('[data-search]');next?.focus();next?.setSelectionRange(pos,pos)}});
window.addEventListener('hashchange',()=>{tab='all';query='';render();window.scrollTo(0,0)});
function actions(action){
 if(action.startsWith('go:')){location.hash=action.slice(3);return}
 if(action==='close'){document.getElementById('dialog').close();return}
 if(action.startsWith('view:')){view=action.slice(5);render();return}
 if(action==='save-settings'||action==='save-config')return save();
}
