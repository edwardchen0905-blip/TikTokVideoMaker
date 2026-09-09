const {chromium}=require('playwright'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),{createHash}=require('node:crypto');
(async()=>{
 const evidence=path.join(__dirname,'..','build','evidence');
 const expected=JSON.parse(fs.readFileSync(path.join(evidence,'restart-expected.json'),'utf8'));
 let b;
 const deadline=Date.now()+45000;
 while(!b){try{b=await chromium.connectOverCDP(process.argv[2],{timeout:1000})}catch(e){if(Date.now()>=deadline)throw e;await new Promise(r=>setTimeout(r,250))}}
 let p;
 for(let n=0;n<60;n++){p=b.contexts().flatMap(c=>c.pages()).find(p=>p.url().startsWith('http://127.0.0.1:'));if(p)break;await new Promise(r=>setTimeout(r,250))}
 assert.ok(p);p.setDefaultTimeout(20000);const errors=[];p.on('pageerror',e=>errors.push(e.message));
 await p.locator('#navigation a[href="#assets"]').waitFor();
 const s=await p.evaluate(async()=>{const r=await fetch('/api/state');return r.json()});
 assert.equal(s.videos.length,4);assert.ok(s.tasks.every(t=>t.status==='done'));assert.equal(s.publications[0].status,'pending');
 assert.ok(s.local_records.some(r=>r.key==='验收音乐规则'));assert.equal(s.local_records.filter(r=>r.kind==='text').length,6);
 assert.ok(s.tasks.some(t=>JSON.parse(t.config).text_trace.language==='en'));assert.ok(s.tasks.some(t=>JSON.parse(t.config).text_trace.language==='th'));
 const ordered=x=>Array.isArray(x)?[...x].sort((a,b)=>String(a.id??JSON.stringify(a)).localeCompare(String(b.id??JSON.stringify(b)))):x;
 for(const [key,value] of Object.entries(expected.state))assert.deepEqual(ordered(s[key]),ordered(value),`restart ${key}`);
 for(const file of [...expected.files,...expected.originals]){
  assert.ok(fs.statSync(file.path).isFile(),file.path);
  assert.equal(createHash('sha256').update(fs.readFileSync(file.path)).digest('hex'),file.sha256,file.path);
 }
 await p.locator('#navigation a[href="#videos"]').click();
 for(const video of s.videos){
  await p.locator(`[data-action="focus-video:${video.id}"]`).filter({hasText:'查看'}).click();
  await p.locator('video').evaluate(v=>{v.currentTime=0;return v.play()});
  await p.waitForFunction(()=>document.querySelector('video')?.currentTime>0);
  await p.locator('video').evaluate(v=>v.pause());
 }
 assert.deepEqual(errors,[]);
 await p.screenshot({path:path.join(evidence,'windows-restarted.png'),fullPage:true});
 fs.writeFileSync(path.join(evidence,'windows-restart.json'),JSON.stringify({passed:true,records_equal:true,files_unchanged:true,originals_protected:true,played_videos:s.videos.map(v=>v.id)},null,2));
 console.log('ORIGINAL_EXE_RESTART_PASSED');process.exit(0);
})().catch(e=>{console.error(e);process.exit(1)});
