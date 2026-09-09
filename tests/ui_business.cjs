// Real DOM clicks + real HTTP service + actual media output. No mocked production routes.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const {createHash}=require('node:crypto');
const {spawn}=require('node:child_process');
const {once}=require('node:events');
const root=path.resolve(__dirname,'..'),evidence=path.join(root,'build','evidence');
fs.mkdirSync(evidence,{recursive:true});
const fileHash=p=>createHash('sha256').update(fs.readFileSync(p)).digest('hex');
const recordStage=(name,details={})=>fs.appendFileSync(path.join(evidence,'ui-stages.jsonl'),JSON.stringify({name,...details})+'\n');
function childResult(child){return new Promise((resolve,reject)=>{let text='';child.stdout?.on('data',d=>text+=d);child.stderr?.on('data',d=>text+=d);child.once('error',reject);child.once('exit',code=>code===0?resolve(text):reject(new Error(`Test helper exited ${code}: ${text}`)))})}
(async()=>{
 let server,browser,page;const errors=[];let fixture;
 try{
  if(process.env.TVM_CDP){
   fixture=JSON.parse(fs.readFileSync(process.env.TVM_FIXTURE,'utf8'));
   browser=await chromium.connectOverCDP(process.env.TVM_CDP);
   // Attach the first original EXE page. Do not reload or create a replacement page.
   for(let n=0;n<100;n++){page=browser.contexts().flatMap(c=>c.pages()).find(p=>p.url().startsWith('http://127.0.0.1:'));if(page)break;await new Promise(r=>setTimeout(r,100))}
   assert.ok(page,'Original EXE page not found');
  }else{
   server=spawn(process.env.PYTHON||'python',['tests/serve_ui.py'],{cwd:root,stdio:['pipe','pipe','inherit']});
   fixture=await new Promise((resolve,reject)=>{let buf='';server.stdout.on('data',d=>{buf+=d;if(buf.includes('\n')){try{resolve(JSON.parse(buf.split('\n')[0]))}catch(e){reject(e)}}});server.once('exit',c=>reject(new Error('UI fixture stopped '+c)))});
   browser=await chromium.launch({headless:true,args:['--no-sandbox']});page=await browser.newPage({viewport:{width:1440,height:1000}});await page.goto(fixture.url);
  }
  page.setDefaultTimeout(20000);page.on('pageerror',e=>errors.push(e.message));
  const click=action=>{
   const locator=page.locator(`[data-action="${action}"]`);
   // These are intentional duplicate entry points: thumbnail/details and header/empty state.
   if(action.startsWith('focus-video:'))return locator.filter({hasText:'查看'}).click();
   return (['close','new-product','import'].includes(action)?locator.first():locator).click();
  };
  const state=()=>page.evaluate(async()=>{const r=await fetch('/api/state');if(!r.ok)throw Error('state failed');return r.json()});
  let nativeSequence=0;
  const native=async(action,arg='')=>{
   if(process.env.TVM_CAPTURE_ONLY==='1'){
    const directory=path.join(evidence,`native-${++nativeSequence}-${action}`);
    fs.mkdirSync(directory,{recursive:true});
    try{
     const log=await childResult(spawn(process.env.PYTHON||'python',['tests/windows_dialog.py',String(fixture.pid),'diagnose',directory,action],{cwd:root}));
     fs.writeFileSync(path.join(directory,'diagnostic.log'),log);
    }catch(error){fs.writeFileSync(path.join(directory,'diagnostic.log'),String(error));throw error}
    if(action==='folder'&&arg===fixture.output){
     fs.writeFileSync(path.join(evidence,'windows-capture.json'),JSON.stringify({capture_complete:true,acceptance_passed:false,pid:fixture.pid,stage:'output-picker-open',captures:nativeSequence},null,2));
     console.log('WINDOW_EVIDENCE_CAPTURED; production acceptance was not run');process.exit(0);
    }
   }
   const result=await childResult(spawn(process.env.PYTHON||'python',['tests/windows_dialog.py',String(fixture.pid),action,arg],{cwd:root}));
   fs.appendFileSync(path.join(evidence,'native-actions.log'),result);return result;
  };
  const waitFor=async(fn,label)=>{const until=Date.now()+90000;while(Date.now()<until){const s=await state();if(s.tasks.some(t=>t.status==='failed'))throw Error(s.tasks.filter(t=>t.status==='failed').map(t=>t.error).join('\n'));if(fn(s))return s;await new Promise(r=>setTimeout(r,250))}throw Error('Timed out: '+label)};
  await page.locator('#navigation a[href="#assets"]').waitFor();
  recordStage('original-page-connected');
  if(process.env.TVM_CDP)await page.screenshot({path:path.join(evidence,'windows-startup.png'),fullPage:true});
  assert.equal((await state()).products.length,0);
  if(process.env.TVM_CDP){
   await page.locator('a[href="#assets"]').first().click();
   // Cancel, then reopen and choose actual files through the Windows picker.
   await click('import');await native('cancel');await page.getByRole('status').filter({hasText:'已取消选择'}).waitFor();
   await click('import');await native('files',fixture.files.map(p=>'"'+p+'"').join(' '));
   await waitFor(s=>s.assets.length>=3,'native asset import');
   await click('import-folder');await native('folder',fixture.source);
   await page.getByRole('status').filter({hasText:'已识别'}).waitFor();
   recordStage('native-assets-imported');
  }
  await page.locator('a[href="#assets"]').first().click();
  const before=await state();assert.ok(before.assets.length>=3);
  const images=before.assets.filter(a=>a.kind==='image');const songs=before.assets.filter(a=>a.kind==='music');
  assert.equal(images.length,5,'Five actual images are required to verify transition cycling');
  await page.locator('a[href="#local"]').click();
  if(process.env.TVM_CDP){
   await click('import-local');await native('files','"'+fixture.local_file+'"');
   await page.locator('#dialog-content').filter({hasText:'成功 2 条'}).waitFor();await click('close');
  }else{
   // Linux fixture imports through the real Workspace importer before the original page loads.
   assert.ok((await state()).local_records.some(r=>r.key==='验收图案'));
  }
  let record=(await state()).local_records.find(r=>r.key==='验收图案');assert.ok(record);
  await page.locator('[data-search]').fill('验收图案');await click('edit-local:'+record.id);
  await page.fill('#record-source','真实界面修改并保存的验收资料');await click('save-local');await page.locator('#dialog').waitFor({state:'hidden'});
  record=(await state()).local_records.find(r=>r.id===record.id);assert.equal(record.version,2);
  recordStage('local-library-imported-and-edited',{id:record.id,version:record.version});
  await page.locator('a[href="#music"]').click();await click('music-info:'+songs[0].id);await page.fill('#music-tags','quiet');await click('save-music:'+songs[0].id);await page.locator('#dialog').waitFor({state:'hidden'});
  await page.locator('a[href="#assets"]').click();

  for(const image of images)await page.locator(`[data-select="asset"][value="${image.id}"]`).check();
  await click('asset-tasks');await page.locator('#batch-source').waitFor();
  assert.equal(await page.locator('#batch-advanced').getAttribute('open'),null);
  assert.equal(await page.locator('#batch-count-number').inputValue(),'1');
  assert.equal(await page.locator('#batch-resolution').inputValue(),'1080x1920');
  assert.equal(await page.locator('[name=batch-transition][value=none]').isChecked(),true);
  assert.equal(await page.locator('[data-action=submit-batch]').isEnabled(),true,'Selected materials and safe defaults must be enough');
  await page.screenshot({path:path.join(evidence,'create-task-simple.png'),fullPage:true});
  await click('batch-none');
  assert.equal(await page.locator('[data-action=submit-batch]').isEnabled(),false);
  assert.ok((await page.locator('#batch-missing').innerText()).trim());
  assert.equal(await page.locator('#batch-missing').isVisible(),true);
  await click('batch-all');
  await page.locator('#batch-advanced > summary').click();
  await page.selectOption('#group-mode','group');
  await page.selectOption('#batch-timing','total');await click('submit-batch');
  await page.locator('#dialog-error').filter({hasText:'冲突'}).waitFor();
  assert.equal((await state()).tasks.length,0,'Conflicting timing must not create a task');
  assert.equal(await page.locator('#dialog-error').isVisible(),true);
  await page.screenshot({path:path.join(evidence,'create-task-error.png'),fullPage:true});
  const errorBounds=await page.locator('#dialog-error').boundingBox(),viewportHeight=await page.evaluate(()=>innerHeight);
  assert.ok(errorBounds&&errorBounds.y>=0&&errorBounds.y+errorBounds.height<=viewportHeight,'Dialog error must remain on screen');
  assert.equal(await page.locator('#batch-seconds').evaluate(e=>document.activeElement===e),true);
  await page.selectOption('#batch-timing','images');
  await page.fill('#batch-seconds','0.5');await page.fill('#batch-transition-time','0.1');await page.selectOption('#batch-motion','none');
  await page.locator('[name=batch-transition][value=slideleft]').check();await page.locator('[name=batch-transition][value=dissolve]').check();
  await page.selectOption('#batch-music','local');await page.selectOption('#local-language','en');await page.locator('[name=copy-source][value=existing]').check();await page.locator('summary').filter({hasText:'选择已有关键词'}).click();await page.locator(`[name=copy-keyword][value="${record.id}"]`).check();
  await page.fill('#batch-count-number','2');
  if(process.env.TVM_CDP){
   await click('pick-batch-output');await native('folder',fixture.output);
   await page.waitForFunction(p=>document.querySelector('#batch-output')?.value.toLowerCase()===p,fs.realpathSync.native(fixture.output).toLowerCase());
   recordStage('output-directory-selected',{path:fixture.output});
  }
  else await page.locator('#batch-output').evaluate((e,p)=>{e.value=p},fixture.output);
  await click('preview-batch');await page.locator('#batch-preview').filter({hasText:'2.300'}).waitFor();
  await click('submit-batch');await page.locator('#dialog').waitFor({state:'hidden'});
  let s=await waitFor(s=>s.videos.length===2,'two actual local videos');
  assert.ok(s.videos.every(v=>JSON.parse(v.validation).full_decode));
  assert.ok(s.tasks.every(t=>JSON.parse(t.config).music_id===songs[0].id));
  assert.ok(s.tasks.every(t=>JSON.parse(t.config).text_trace.language==='en'&&JSON.parse(t.config).music_match.status==='matched'));
  for(const task of s.tasks){
   const config=JSON.parse(task.config);
   assert.equal(task.template,'');assert.equal(config.template_id,undefined);
   assert.deepEqual(config.transitions,['none','slideleft','dissolve']);
   assert.deepEqual(config.transition_sequence,['none','slideleft','dissolve','none']);
   assert.deepEqual(config.transition_durations,[0,.1,.1,0]);
   assert.equal(config.resolution,'1080x1920');assert.equal(config.ratio,'9:16');assert.equal(config.fit,'contain');
  }
  assert.ok(s.videos.every(v=>JSON.parse(v.content).caption.includes('geometric details')&&JSON.parse(v.content).tags.includes('#geometricdetails')));
  assert.ok(s.videos.every(v=>fs.realpathSync.native(path.dirname(v.path))===fs.realpathSync.native(fixture.output)));
  assert.ok(s.tasks.every(t=>fs.realpathSync.native(JSON.parse(t.config).output_dir)===fs.realpathSync.native(fixture.output)));
  recordStage('local-videos-produced',{videos:s.videos.map(v=>v.id)});
  await page.locator('a[href="#videos"]').first().click();await click('focus-video:'+s.videos[0].id);
  await page.locator('video').waitFor();await page.locator('video').evaluate(v=>v.play());
  await page.waitForFunction(()=>document.querySelector('video')?.currentTime>0);
  await page.locator('video').evaluate(v=>v.pause());
  await click('edit-copy:'+s.videos[0].id);await page.fill('#copy-caption','真实商品说明');await page.fill('#copy-tags','#商品');await click('save-copy:'+s.videos[0].id);
  await page.locator('#dialog').waitFor({state:'hidden'});assert.equal(JSON.parse((await state()).videos[0].content).caption,'真实商品说明');
  await page.locator(`[data-select="video"][value="${s.videos[0].id}"]`).check();
  await click('queue-selected');await page.fill('#queue-target','验收账号（未连接）');await click('confirm-queue');
  await page.locator('#dialog').waitFor({state:'hidden'});assert.equal((await state()).publications[0].status,'pending');
  await page.locator('a[href="#local"]').click();await click('delete-local:'+record.id);await click('confirm-local:'+record.id);await page.locator('#dialog').waitFor({state:'hidden'});
  if(process.env.TVM_CDP){
   await page.locator('#navigation a[href="#settings"]').click();await click('pick-default-output');await native('folder',fixture.alternate_output);
   await page.waitForFunction(p=>document.querySelector('#settings-output')?.value.toLowerCase()===p,fs.realpathSync.native(fixture.alternate_output).toLowerCase());
   await click('save-settings');await waitFor(s=>!!s.settings.output_dir,'saved default output');
   assert.equal(fs.realpathSync.native((await state()).settings.output_dir),fs.realpathSync.native(fixture.alternate_output));
   recordStage('default-output-changed-before-regeneration');
  }
  await page.locator('a[href="#videos"]').click();
  const originalVideo=s.videos[0],originalTask=s.tasks.find(t=>t.id===originalVideo.task_id),originalHash=fileHash(originalVideo.path);
  const existingVideos=new Set(s.videos.map(v=>v.id));
  await click('regenerate:'+originalVideo.id);s=await waitFor(s=>s.videos.length===3,'regenerated video');
  const regenerated=s.videos.find(v=>!existingVideos.has(v.id));assert.ok(regenerated);
  const regeneratedTask=s.tasks.find(t=>t.id===regenerated.task_id);
  for(const key of ['config','assets','template','product_id','sku_id'])assert.deepEqual(regeneratedTask[key],originalTask[key],`regenerated ${key}`);
  assert.notEqual(regenerated.task_id,originalVideo.task_id);assert.notEqual(regenerated.path,originalVideo.path);
  assert.equal(fs.realpathSync.native(path.dirname(regenerated.path)),fs.realpathSync.native(path.dirname(originalVideo.path)));
  assert.equal(fileHash(originalVideo.path),originalHash);assert.ok(fs.statSync(regenerated.path).size>0);
  recordStage('regeneration-consistent',{original:originalVideo.id,regenerated:regenerated.id,path:regenerated.path});
  await page.locator('a[href="#music"]').first().click();
  await page.locator('audio').evaluate(a=>a.play());
  await page.waitForFunction(()=>document.querySelector('audio')?.currentTime>0);
  await page.locator('audio').evaluate(a=>a.pause());
  await click('music-history:'+songs[0].id);
  assert.match(await page.locator('#dialog-content').innerText(),/已完成/);await click('close');
  await click('delete-music:'+songs[0].id);await click('confirm-delete-music:'+songs[0].id);await page.locator('#dialog').waitFor({state:'hidden'});
  assert.equal((await state()).assets.find(a=>a.id===songs[0].id).available,false);
  // A square output uses the same simple task flow and a single direct cut.
  await page.locator('a[href="#assets"]').click();
  for(const image of images)await page.locator(`[data-select="asset"][value="${image.id}"]`).check();
  await click('asset-tasks');await page.locator('#batch-source').waitFor();
  assert.equal(await page.locator('#batch-advanced').getAttribute('open'),null);
  await page.selectOption('#batch-resolution','1080x1080');await page.selectOption('#batch-music','none');
  await page.selectOption('#local-language','en');
  await click('submit-batch');await page.locator('#dialog').waitFor({state:'hidden'});
  s=await waitFor(s=>s.videos.length===4,'square video with safe defaults');
  const square=s.tasks.find(t=>JSON.parse(t.config).resolution==='1080x1080');assert.ok(square);
  assert.deepEqual(JSON.parse(square.config).transitions,['none']);assert.equal(JSON.parse(square.config).fit,'contain');
  // Real product creation, association, and production via DOM controls.
  await page.locator('a[href="#products"]').first().click();await click('new-product');
  await page.fill('#product-id','UI-P1');await page.fill('#product-title','界面商品');await click('submit-product');await page.locator('#dialog').waitFor({state:'hidden'});
  await click('link-assets:UI-P1');for(const image of images)await page.locator(`[name=link-asset][value="${image.id}"]`).check();await click('submit-link:UI-P1');await page.locator('#dialog').waitFor({state:'hidden'});
  await page.locator('[data-select="product"][value="UI-P1"]').check();await click('product-tasks');
  await page.selectOption('#batch-resolution','1920x1080');await page.selectOption('#batch-music','none');await page.selectOption('#local-language','th');
  await page.locator('#batch-advanced > summary').click();await page.fill('#batch-seconds','.4');await click('copy-none');
  await page.locator('[name=batch-transition][value=none]').uncheck();await page.locator('[name=batch-transition][value=fade]').check();
  await page.fill('#batch-transition-time','.1');
  await click('submit-batch');await page.locator('#dialog').waitFor({state:'hidden'});s=await waitFor(s=>s.videos.length===5,'product video');
  assert.ok(s.tasks.some(t=>t.product_id==='UI-P1'&&t.status==='done'));
  const productTask=s.tasks.find(t=>t.product_id==='UI-P1');assert.equal(JSON.parse(productTask.config).text_trace.language,'th');assert.ok(JSON.parse(productTask.config).text_trace.fallback.caption);
  assert.deepEqual(JSON.parse(productTask.config).transition_sequence,['fade','fade','fade','fade']);
  assert.equal(JSON.parse(productTask.config).resolution,'1920x1080');
  recordStage('product-video-produced',{task:productTask.id});
  // Every advertised effect previews a real renderer output through the application's UI.
  assert.equal(await page.locator('#navigation a[href="#templates"]').count(),0);
  await page.locator('#navigation a[href="#home"]').click();
  await page.getByRole('button',{name:'查看或编辑生产设置',exact:true}).click();
  await page.locator('#batch-source').waitFor();await click('close');
  await click('go:transitions');
  await page.locator('#navigation a[href="#transitions"].active').waitFor();
  await page.screenshot({path:path.join(evidence,'transition-effects.png'),fullPage:true});
  const effects=(await state()).transitions;
  assert.deepEqual(effects.map(e=>e.id),['none','fade','dissolve','slideleft','slideright','slideup','slidedown','coverleft','zoomsafe']);
  for(const effect of effects){
   await click('preview-transition:'+effect.id);
   await page.locator('#dialog video').waitFor();
   await page.locator('#dialog video').evaluate(v=>{v.currentTime=0;return v.play()});
   await page.waitForFunction(()=>document.querySelector('#dialog video')?.currentTime>0);
   await page.locator('#dialog video').evaluate(v=>v.pause());
   assert.match(await page.locator('#dialog-content').innerText(),new RegExp(effect.name));
   await click('close');
  }
  recordStage('all-transition-previews-played',{effects:effects.map(e=>e.id)});
  if(process.env.TVM_CDP){
   await page.locator('#navigation a[href="#products"]').click();await click('import-products');await click('pick-products');
   await native('files','"'+fixture.products_file+'"');
   await waitFor(s=>s.products.some(p=>p.id==='CSV-P1'),'native CSV product import');
   assert.equal((await state()).products.find(p=>p.id==='CSV-P1').title,'CSV导入商品');
   recordStage('native-product-csv-imported');
  }
  for(const route of ['home','shops','products','assets','music','local','transitions','tasks','videos','publish','settings']){
   await page.locator(`#navigation a[href="#${route}"]`).click();
   await page.waitForFunction(route=>document.querySelector(`#navigation a[href="#${route}"]`)?.classList.contains('active'),route);
   const text=await page.locator('#page').innerText();assert.ok(!/undefined|fast_show|product_show/.test(text),route);
   if(['music','videos','home'].includes(route))await page.screenshot({path:path.join(evidence,route+'.png'),fullPage:true});
  }
  s=await state();
  const outputFiles=[];
  for(const video of s.videos){
   const task=s.tasks.find(t=>t.id===video.task_id),config=JSON.parse(task.config),check=JSON.parse(video.validation);
   assert.equal(task.status,'done');assert.equal(task.output,video.path);assert.equal(video.available,true);
   assert.equal(fs.realpathSync.native(path.dirname(video.path)),fs.realpathSync.native(config.output_dir));
   const [width,height]=config.resolution.split('x').map(Number);
   assert.equal(check.full_decode,true);assert.equal(check.width,width);assert.equal(check.height,height);assert.equal(check.fps,30);
   assert.equal(check.resolution,config.resolution);assert.equal(check.ratio,config.ratio);assert.equal(check.fit,'contain');
   assert.equal(check.audio,!!config.music_id);assert.ok(Math.abs(video.duration-config.expected_duration)<=.1);
   assert.ok(fs.statSync(video.path).size>0);outputFiles.push({path:video.path,sha256:fileHash(video.path)});
  }
  if(process.env.TVM_CDP){
   await page.locator('#navigation a[href="#videos"]').click();
   for(const video of s.videos){
    await click('focus-video:'+video.id);await page.locator('video').evaluate(v=>{v.currentTime=0;return v.play()});
    await page.waitForFunction(()=>document.querySelector('video')?.currentTime>0);await page.locator('video').evaluate(v=>v.pause());
   }
   await page.screenshot({path:path.join(evidence,'all-videos-verified.png'),fullPage:true});
   const keys=['products','assets','skus','links','tasks','videos','publications','local_records','transitions','resolution_options','settings'];
   fs.writeFileSync(path.join(evidence,'restart-expected.json'),JSON.stringify({state:Object.fromEntries(keys.map(k=>[k,s[k]])),files:outputFiles,originals:fixture.originals},null,2));
  }
  recordStage('all-output-files-verified',{videos:outputFiles.length});
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(evidence,process.env.TVM_CDP?'windows-ui.json':'linux-ui.json'),JSON.stringify({passed:true,original_page:true,real_videos:s.videos.length,errors,native_dialogs:!!process.env.TVM_CDP},null,2));
  console.log('REAL_UI_BUSINESS_PASSED');
  if(process.env.TVM_CDP)process.exit(0);
 }catch(error){
  if(page&&!page.isClosed())await page.screenshot({path:path.join(evidence,'failure.png'),fullPage:true});
  throw error;
 }finally{
  if(browser&&!process.env.TVM_CDP)await browser.close();
  if(server){server.stdin.end('\n');const [code]=await once(server,'exit');assert.equal(code,0,'UI fixture did not exit normally')}
 }
})().catch(e=>{console.error(e);process.exit(1)});
