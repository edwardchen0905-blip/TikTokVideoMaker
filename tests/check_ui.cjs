// Checks route rendering and action dispatch without pretending to validate a desktop window.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.resolve(__dirname,'..');
const choices=JSON.parse(require('node:child_process').execFileSync(process.env.PYTHON||'python',['-c','import json; from core.render_config import TRANSITIONS, RESOLUTIONS; print(json.dumps(dict(transitions=TRANSITIONS, resolution_options=RESOLUTIONS)))'],{cwd:root,encoding:'utf8'}));
const elements=new Map(),timers=[];let pageVideo=false,retainedDialogVideo=false;
const node=id=>{if(!elements.has(id))elements.set(id,{innerHTML:'',textContent:'',value:'',open:false,isConnected:true,classList:{add(){},remove(){}},setAttribute(){},scrollIntoView(){},showModal(){this.open=true},close(){this.open=false}});return elements.get(id)};
function querySelector(selector){
 if(selector==='#dialog[open] #batch-source')return node('dialog').open?node('batch-source'):null;
 if(selector==='#page video')return pageVideo?node('page-video'):null;
 if(selector==='video')return pageVideo?node('page-video'):retainedDialogVideo?node('dialog-video'):null;
 return {setAttribute(){}};
}
const context=vm.createContext({console,setTimeout,clearTimeout,setInterval(fn){timers.push(fn)},location:{hash:'#home'},localStorage:{getItem(){return null},setItem(){}},window:{addEventListener(){},scrollTo(){}},document:{getElementById:node,querySelectorAll(){return []},addEventListener(){},querySelector,activeElement:{tagName:'BODY'}},fetch:async()=>({ok:true,json:async()=>({products:[],assets:[],skus:[],links:[],tasks:[],batches:[],videos:[],publications:[],sync_tasks:[],download_rules:[],stores:[],...choices,settings:{},storage:{total:100,free:100,root:'test',output:'test/output',cache:0}})})});
vm.runInContext(fs.readFileSync(path.join(root,'ui/app.js'),'utf8'),context);
vm.runInContext(fs.readFileSync(path.join(root,'ui/connected.js'),'utf8'),context);
(async()=>{
 await new Promise(resolve=>setTimeout(resolve,10));
 for(const route of ['home','shops','products','assets','music','local','transitions','tasks','videos','publish','settings']){vm.runInContext(`location.hash='#${route}';render()`,context);const html=node('page').innerHTML;assert.ok(html.includes('<h1>'),route);assert.ok(!/undefined|fast_show|product_show/.test(html),route+' leaked internal template');console.log('Route render:',route)}
 assert.ok(!node('navigation').innerHTML.includes('#templates'));
 // Run the actual action and refresh callback. Stop at the HTTP boundary: no media or successful production is simulated.
 const observations=[],failures=[];
 const check=(name,actual,expected)=>{console.log(JSON.stringify({check:name,actual,expected}));try{assert.deepEqual(actual,expected,name)}catch(error){failures.push(error.message)}};
 context.observeCall=(name,payload)=>{observations.push({name,payload,dialogOpen:node('dialog').open,batchTarget:node('batch-transition-preview').innerHTML,pageTarget:node('transition-preview').innerHTML});throw Error('Control-flow check stopped at HTTP boundary')};
 vm.runInContext('api=async(name,payload)=>observeCall(name,payload);reportError=error=>{globalThis.observedError=error.message}',context);
 for(const open of [false,true]){
  node('dialog').open=open;node('batch-resolution').value='1920x1080';node('batch-transition-time').value='.1';
  node('batch-transition-preview').innerHTML='';node('transition-preview').innerHTML='';observations.length=0;
  await vm.runInContext("actions('preview-transition:none')",context);
  assert.equal(context.observedError,'Control-flow check stopped at HTTP boundary');assert.equal(observations.length,1);
  const actual=JSON.parse(JSON.stringify(observations[0]));
  check(open?'active batch preview':'closed batch leaves page preview active',actual,{name:'transition-preview',payload:{transition:'none',resolution:open?'1920x1080':'1080x1920',transition_duration:open?.1:.3},dialogOpen:true,batchTarget:open?'<p>正在准备实际效果预览…</p>':'',pageTarget:open?'':'<p>正在准备实际效果预览…</p>'});
 }
 for(const [name,open,visibleVideo,retainedVideo,expected] of [['closed dialog video must not block refresh',false,false,true,['state']],['visible page video must not block state refresh',false,true,true,['state']],['open dialog protects editing',true,false,true,[]]]){
  node('dialog').open=open;pageVideo=visibleVideo;retainedDialogVideo=retainedVideo;observations.length=0;
  vm.runInContext("route='tasks';online=true",context);await timers[0]();
  check(name,observations.map(o=>o.name),expected);
 }
 vm.runInContext('clearTimeout(toast.timer)',context);
 assert.deepEqual(failures,[]);
 console.log('Route and dialog control-flow checks passed (not a browser or Windows acceptance test).');
})().catch(e=>{console.error(e);process.exitCode=1});
