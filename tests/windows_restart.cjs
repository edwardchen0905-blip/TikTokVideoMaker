const {chromium}=require('playwright'),assert=require('node:assert/strict');
(async()=>{
 let b;
 for(let n=0;n<60;n++){try{b=await chromium.connectOverCDP(process.argv[2]);break}catch(e){if(n===59)throw e;await new Promise(r=>setTimeout(r,250))}}
 let p;
 for(let n=0;n<60;n++){p=b.contexts().flatMap(c=>c.pages()).find(p=>p.url().startsWith('http://127.0.0.1:'));if(p)break;await new Promise(r=>setTimeout(r,250))}
 assert.ok(p);await p.locator('#navigation a[href="#assets"]').waitFor();
 const s=await p.evaluate(async()=>{const r=await fetch('/api/state');return r.json()});
 assert.equal(s.videos.length,4);assert.ok(s.tasks.every(t=>t.status==='done'));assert.equal(s.publications[0].status,'pending');
 assert.ok(s.local_records.some(r=>r.key==='验收音乐规则'));assert.equal(s.local_records.filter(r=>r.kind==='text').length,6);
 assert.ok(s.tasks.some(t=>JSON.parse(t.config).text_trace.language==='en'));assert.ok(s.tasks.some(t=>JSON.parse(t.config).text_trace.language==='th'));
 console.log('ORIGINAL_EXE_RESTART_PASSED');process.exit(0);
})().catch(e=>{console.error(e);process.exit(1)});
