import {createServer} from 'vite';
import {spawn} from 'node:child_process';
import {mkdtemp,rm} from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const rootDir=path.resolve(import.meta.dirname,'..');
const profile=await mkdtemp(`${process.env.TMPDIR || '/root/.hermes/cache/scratch'}/mobile-nav-`);
const server=await createServer({
  configFile:path.join(rootDir,'vite.config.ts'),
  root:rootDir,
  cacheDir:path.join(profile,'vite'),
  server:{host:'127.0.0.1',port:0,hmr:false,watch:null},
  logLevel:'error',
});
let browser,socket;
try {
  await server.listen();
  browser=spawn('/usr/bin/chromium',['--headless','--no-sandbox','--disable-dev-shm-usage','--remote-debugging-port=0',`--user-data-dir=${profile}`,'about:blank'],{stdio:['ignore','ignore','pipe']});
  const ws=await new Promise((resolve,reject)=>{
    let text='';const timer=setTimeout(()=>reject(new Error('CDP startup timeout')),15000);
    browser.on('error',reject);
    browser.stderr.on('data',chunk=>{text+=chunk;const m=text.match(/DevTools listening on (ws:\/\/\S+)/);if(m){clearTimeout(timer);resolve(m[1]);}});
  });
  socket=new WebSocket(ws);
  await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true});});
  let seq=0;const pending=new Map();
  socket.addEventListener('message',e=>{const m=JSON.parse(e.data),p=pending.get(m.id);if(p){pending.delete(m.id);clearTimeout(p.timer);m.error?p.reject(new Error(JSON.stringify(m.error))):p.resolve(m.result);}});
  const call=(method,params={},sessionId)=>new Promise((resolve,reject)=>{const id=++seq;const timer=setTimeout(()=>{pending.delete(id);reject(new Error(`${method} timeout`));},15000);pending.set(id,{resolve,reject,timer});socket.send(JSON.stringify({id,method,params,...(sessionId?{sessionId}:{})}));});
  const {targetId}=await call('Target.createTarget',{url:'about:blank'});
  const {sessionId}=await call('Target.attachToTarget',{targetId,flatten:true});
  const evaluate=async expression=>{const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true},sessionId);if(r.exceptionDetails)throw new Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
  await call('Page.navigate',{url:`http://127.0.0.1:${server.httpServer.address().port}/tests/mobile-nav-fixture.html`},sessionId);
  let ready=false;
  for(let i=0;i<100;i++){if(await evaluate('!!window.ready')){ready=true;break;}await new Promise(r=>setTimeout(r,100));}
  assert.ok(ready,'fixture must initialize');
  const counted = await evaluate('root().querySelectorAll(\'nav button\').length');
  assert.throws(() => assert.equal(counted, 0, 'navigation button count'), /navigation button count/);
  console.log('RED control: navigation button count assertion rejects 0, actual', counted);
  const settingsIcon = await evaluate(`(()=>{const svg=root().querySelector('nav button[aria-label="设置"] svg');return {outline:svg.querySelector('path')?.getAttribute('d'),hub:!!svg.querySelector('circle')};})()`);
  assert.ok(settingsIcon.hub, 'settings gear retains its central hub');
  assert.equal((settingsIcon.outline.match(/M/gi)||[]).length, 1, 'settings must have a continuous gear outline, not sun rays');
  assert.match(settingsIcon.outline, /z$/i, 'gear outline is closed');
  for(const width of [320,390,768,1280,390]){
    await call('Emulation.setDeviceMetricsOverride',{width,height:800,deviceScaleFactor:1,mobile:false},sessionId);
    await evaluate('(async()=>{setDrawer(true);await frames();})()');
    const state=await evaluate(`(()=>{const nav=root().querySelector('nav'),footer=root().querySelector('footer');const box=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom,right:r.right}};return {nav:box(nav),footer:box(footer),aside:box(root().querySelector('aside')),buttons:[...nav.querySelectorAll('button')].map(b=>({...box(b),name:b.getAttribute('aria-label'),labelVisible:b.querySelector('span')?getComputedStyle(b.querySelector('span')).display!=='none':true}))};})()`);
    assert.equal(state.buttons.length,6);
    if(width<=768){
      assert.ok(state.nav.y>=state.footer.bottom-1,'navigation must be below Hermes version footer');
      assert.deepEqual(state.buttons.map(b=>b.name),['对话','生图','文件','活动','状态','设置']);
      assert.ok(state.buttons.every(b=>!b.labelVisible&&b.width>=44&&b.height>=44));
      assert.ok(state.buttons.every(b=>Math.abs(b.y-state.buttons[0].y)<1));
      assert.ok(state.nav.right<=state.aside.right+1);
    }else{
      assert.ok(state.nav.bottom<=state.footer.y);
      assert.ok(state.buttons.every(b=>b.labelVisible));
      assert.ok(state.buttons[1].y>state.buttons[0].y);
    }
    console.log('PASS layout',width);
  }
  for(const [index,view] of ['/','/image','/files','/activity','/status','/settings'].entries()){
    await evaluate(`(async()=>{setDrawer(true);await frames();root().querySelectorAll('nav[aria-label="页面"] button')[${index}].click();for(let n=0;n<60 && root().querySelectorAll('nav[aria-label="页面"] button')[${index}].getAttribute('aria-current')!=='page';n++)await new Promise(r=>setTimeout(r,50));})()`);
    assert.equal(await evaluate('route()'),view);
    assert.equal(await evaluate('drawerOpen()'),false);
    const navState = await evaluate(`JSON.stringify({route:route(), path:reactPath(), errors:window.fixtureErrors, buttons:[...root().querySelectorAll('nav[aria-label="页面"] button')].map(b=>({name:b.getAttribute('aria-label'), current:b.getAttribute('aria-current')}))})`);
    console.log('NAV', view, navState);
    assert.equal(JSON.parse(navState).buttons[index].current, 'page', navState);
  }
  console.log('PASS mobile navigation and drawer close');
}finally{
  if(socket)socket.close();
  if(browser){browser.kill('SIGTERM');await new Promise(r=>browser.exitCode!==null?r():browser.once('exit',r));}
  await server.close();await rm(profile,{recursive:true,force:true,maxRetries:5,retryDelay:100});
}
