import { createServer } from 'vite';
import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, readFile, writeFile, rm } from 'node:fs/promises';
import { realpathSync } from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import { request as httpRequest } from 'node:http';
import { randomBytes } from 'node:crypto';
import { pathToFileURL } from 'node:url';

const LIVE = new Set(['queued', 'running', 'waiting_for_approval', 'stopping', 'unknown']);
const TERMINAL = new Set(['completed', 'failed', 'cancelled', 'interrupted']);

export function runningAsMain() {
  const entry = process.argv[1];
  if (!entry) return false;
  try {
    return import.meta.url === pathToFileURL(realpathSync(entry)).href;
  } catch {
    return false;
  }
}

export function fixtureError(code, message) {
  return { error: { message, type: 'invalid_request_error', param: null, code } };
}

export function stripPanel(pathname) {
  if (pathname.startsWith('/panel-api/')) return { channel: 'api', path: pathname.slice('/panel-api'.length) };
  if (pathname.startsWith('/panel-sidecar/')) return { channel: 'sidecar', path: pathname.slice('/panel-sidecar'.length) };
  if (pathname.startsWith('/panel-')) return { channel: 'unknown', path: pathname };
  return null;
}

export function mintRunId() {
  return `run_${randomBytes(16).toString('hex')}`;
}

export function projectRunStatus(run) {
  if (!run || typeof run.run_id !== 'string' || run.run_id === '' || typeof run.session_id !== 'string' || run.session_id === '') return null;
  return {
    object: 'hermes.run',
    run_id: run.run_id,
    session_id: run.session_id,
    status: run.status,
    created_at: run.created_at,
    updated_at: run.updated_at,
  };
}

function runRecord(run) {
  const projected = projectRunStatus(run);
  if (!projected) return null;
  return {
    run_id: projected.run_id,
    session_id: projected.session_id,
    status: projected.status,
    created_at: projected.created_at,
    updated_at: projected.updated_at,
  };
}

export function admitFixtureRun(body, idempotencyKey, now, runsByKey) {
  const sessionId = body && typeof body.session_id === 'string' ? body.session_id : '';
  if (!sessionId || sessionId.length > 256 || /[\r\n\0]/.test(sessionId)) {
    return { status: 400, body: fixtureError('invalid_request', 'session_id required') };
  }
  const key = typeof idempotencyKey === 'string' ? idempotencyKey : '';
  if (key) {
    const prior = runsByKey.get(key);
    if (prior) {
      if (prior.session_id !== sessionId) {
        return { status: 409, body: fixtureError('idempotency_key_conflict', 'Idempotency-Key was already used with a different request payload') };
      }
      return { status: 202, body: { run_id: prior.run_id, status: 'started', replayed: true }, run: prior, replay: true };
    }
  }
  const run = {
    run_id: mintRunId(),
    session_id: sessionId,
    status: 'running',
    created_at: now,
    updated_at: now,
    idempotencyKey: key,
    events: [],
    followers: new Set(),
  };
  if (key) runsByKey.set(key, run);
  return { status: 202, body: { run_id: run.run_id, status: 'started', replayed: false }, run, replay: false };
}

function pathSession(pathname, marker) {
  const match = pathname.match(new RegExp(`/sessions/([^/]+)/${marker}$`));
  if (!match?.[1]) return '';
  try { return decodeURIComponent(match[1]); } catch { return ''; }
}

function pathRunId(pathname) {
  const match = pathname.match(/^\/v1\/runs\/([^/]+)/);
  if (!match?.[1]) return '';
  try { return decodeURIComponent(match[1]); } catch { return ''; }
}

function ownedRun(state, id) {
  return state?.run?.run_id === id ? state.run : null;
}

export function stopDecision(run, requestedId, now) {
  const projected = projectRunStatus(run);
  if (!projected || projected.run_id !== requestedId) {
    return { status: 404, body: fixtureError('run_not_found', `Run not found: ${requestedId}`) };
  }
  if (!TERMINAL.has(run.status)) {
    run.status = 'stopping';
    run.updated_at = now;
  }
  return { status: 200, body: projectRunStatus(run) };
}

function knownPath(pathname) {
  return pathname === '/files/artifact'
    || pathname === '/run-wait'
    || pathname === '/v1/runs'
    || pathname === '/api/sessions'
    || pathname === '/api/model/options'
    || pathname === '/api/runtime/subagents'
    || /^\/v1\/runs\/[^/]+$/.test(pathname)
    || /^\/v1\/runs\/[^/]+\/(?:events|stop|steer|approval|clarify|resolve-unknown)$/.test(pathname)
    || /^\/api\/sessions\/[^/]+\/(?:messages|effort|model)$/.test(pathname)
    || /^\/api\/runtime\/sessions\/[^/]+\/(?:run|background|execution|context|execution\/stop)$/.test(pathname);
}

export function artifactsJsonDecision(method, pathname, query, state = { run: null, messages: [] }, body = undefined) {
  const stripped = stripPanel(pathname);
  if (!stripped || stripped.channel === 'unknown') {
    return { status: 404, body: fixtureError('route_not_found', '夹具没有这条路由') };
  }
  const route = stripped.path;
  const params = query instanceof URLSearchParams ? query : new URLSearchParams(query || '');
  if (stripped.channel === 'sidecar' && method === 'GET' && route === '/files/artifact') return { proxy: true };
  if (stripped.channel === 'sidecar' && method === 'GET' && route === '/run-wait') {
    return { status: 200, body: { state: 'unknown', source: 'durable', available: false, observedAtMs: Date.now() } };
  }
  if (method === 'POST' && route === '/v1/runs') return { create: true };
  if (method === 'GET' && /^\/v1\/runs\/[^/]+\/events$/.test(route)) {
    const id = pathRunId(route);
    const run = ownedRun(state, id);
    if (!projectRunStatus(run)) return { status: 404, body: fixtureError('run_not_found', `Run not found: ${id}`) };
    return { stream: true, run };
  }
  if (method === 'POST' && /^\/v1\/runs\/[^/]+\/stop$/.test(route)) {
    return stopDecision(ownedRun(state, pathRunId(route)), pathRunId(route), Date.now() / 1000);
  }
  if (method === 'GET' && /^\/v1\/runs\/[^/]+$/.test(route)) {
    const id = pathRunId(route);
    const projected = projectRunStatus(ownedRun(state, id));
    if (!projected) return { status: 404, body: fixtureError('run_not_found', `Run not found: ${id}`) };
    return { status: 200, body: projected };
  }
  if (method === 'POST' && /^\/v1\/runs\/[^/]+\/(steer|approval|clarify|resolve-unknown)$/.test(route)) {
    const id = pathRunId(route);
    if (!projectRunStatus(ownedRun(state, id))) return { status: 404, body: fixtureError('run_not_found', `Run not found: ${id}`) };
    const action = route.split('/').pop();
    if (action === 'steer') return { status: 409, body: fixtureError('steer_not_accepted', `Run did not accept steer text: ${id}`) };
    return { status: 409, body: fixtureError(action === 'resolve-unknown' ? 'run_not_active' : 'approval_not_pending', action === 'resolve-unknown' ? `Run is not active in this gateway process: ${id}` : '没有待处理的审批或澄清') };
  }
  if (method === 'GET' && route === '/api/sessions') {
    const limit = Number(params.get('limit') || 50);
    const offset = Number(params.get('offset') || 0);
    const all = [{
      id: 'artifacts-test', source: 'api_server', title: '合成', model: 'test-model',
      started_at: 1, last_active: 1, message_count: state.messages?.length || 0, preview: null, pinned: false, archived: false,
    }];
    const data = all.slice(offset, offset + limit);
    return { status: 200, body: { object: 'list', data, limit, offset, has_more: offset + data.length < all.length } };
  }
  if (method === 'GET' && /\/api\/sessions\/[^/]+\/messages$/.test(route)) {
    const messages = state.messages || [];
    return { status: 200, body: { data: messages, pagination: { returned: messages.length } } };
  }
  if ((method === 'GET' || method === 'PUT') && /\/api\/sessions\/[^/]+\/effort$/.test(route)) {
    const id = pathSession(route, 'effort');
    if (method === 'GET') return { status: 200, body: { session_id: id, effort: null, source: 'unset', applied: 'unset', running: null } };
    if (!body || typeof body !== 'object' || Array.isArray(body) || Object.keys(body).length !== 1 || typeof body.effort !== 'string') {
      return { status: 400, body: fixtureError('unexpected_fields', '只能提交 effort') };
    }
    const live = state.run?.session_id === id && LIVE.has(state.run.status);
    return { status: 200, body: { session_id: id, effort: body.effort, source: 'pending', applied: 'stored', running: live } };
  }
  if (method === 'POST' && /\/api\/sessions\/[^/]+\/model$/.test(route)) {
    if (!body || typeof body.model !== 'string' || typeof body.provider !== 'string' || !body.model || !body.provider) {
      return { status: 400, body: fixtureError('invalid_request', '需要 model 与 provider') };
    }
    return { status: 200, body: { model: body.model, provider: body.provider } };
  }
  if (method === 'GET' && route === '/api/model/options') {
    return { status: 200, body: { model: 'test-model', provider: 'fixture', providers: [{ slug: 'fixture', name: 'Fixture', authenticated: true, models: ['test-model'] }] } };
  }
  if (method === 'GET' && /\/api\/runtime\/sessions\/[^/]+\/run$/.test(route)) {
    const id = pathSession(route, 'run');
    const key = params.get('idempotency_key');
    const same = state.run?.session_id === id ? state.run : null;
    const matched = key == null ? same : (same?.idempotencyKey === key ? same : null);
    const record = runRecord(matched);
    const busy = Boolean(record && LIVE.has(record.status));
    return { status: 200, body: { run: record, busy, ...(key == null ? {} : { resolution: { idempotency_key: key, state: record ? 'found' : 'not_found' } }) } };
  }
  if (method === 'GET' && /\/api\/runtime\/sessions\/[^/]+\/background$/.test(route)) return { status: 200, body: { processes: [] } };
  if (method === 'GET' && /\/api\/runtime\/sessions\/[^/]+\/execution$/.test(route)) {
    const id = pathSession(route, 'execution');
    const busy = Boolean(state.run?.session_id === id && LIVE.has(state.run.status));
    return { status: 200, body: { busy, execution: null } };
  }
  if (method === 'POST' && /\/api\/runtime\/sessions\/[^/]+\/execution\/stop$/.test(route)) {
    return { status: 409, body: fixtureError('unknown_execution', '执行器身份不明，未发送信号，也未删除租约') };
  }
  if (method === 'GET' && /\/api\/runtime\/sessions\/[^/]+\/context$/.test(route)) {
    return { status: 200, body: { used: null, window: null, source: 'no-data', session_id: pathSession(route, 'context') } };
  }
  if (method === 'GET' && route === '/api/runtime/subagents') {
    return { status: 200, body: { data: [], running: 0, stalled: 0, total: 0, now: Date.now() / 1000, limit: Number(params.get('limit') || 50), offset: Number(params.get('offset') || 0) } };
  }
  if (knownPath(route)) return { status: 405, body: fixtureError('method_not_allowed', 'method not allowed') };
  return { status: 404, body: fixtureError('route_not_found', '夹具没有这条路由') };
}

if (runningAsMain()) {
  await runArtifactsBrowser();
}

async function runArtifactsBrowser() {
const profile=await mkdtemp(path.join('/root/hermes-workspace','panel-artifacts-browser-'));
let sidecar,sidecarPort;
const stopProcess=async proc=>{if(proc && proc.exitCode===null){proc.kill('SIGTERM');await new Promise(resolve=>proc.once('exit',resolve));}};
const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=','base64');
const workspace=path.join(profile,'workspace');
const image=path.join(workspace,'测试 图片.png');
const body=`生成结果\\nMEDIA:\"${image}\"`;
const file=path.join(workspace,'报告 结果.txt');
const archive=path.join(workspace,'合成.zip');
const fileBytes=Buffer.from('合成产物，下载必须保留原始字节\n','utf8');
const state={requests:[],posts:[],run:null,runsByKey:new Map(),messages:[{id:1,role:'user',content:'合成请求',timestamp:1},{id:2,role:'assistant',content:body,timestamp:2}]};
const event=(data)=>{const run=state.run;if(!projectRunStatus(run))return;const payload={...data,run_id:run.run_id,session_id:run.session_id,seq:run.events.length,timestamp:Date.now()/1000};const frame=`id: ${payload.seq}\ndata: ${JSON.stringify(payload)}\n\n`;run.events.push(frame);for(const res of run.followers)res.write(frame);};
const json=(res,value,status=200)=>{res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(value));};
const headerValue=req=>{const raw=req.headers['idempotency-key'];return Array.isArray(raw)?raw[0]||'':raw||'';};
const backend={name:'synthetic-artifact-backend',configureServer(server){const handler=async(req,res,next)=>{
 const url=new URL(req.url,'http://fixture');
 if(url.pathname.startsWith('/unsafe-fetch')) {state.requests.push({method:req.method,path:url.pathname});return json(res,fixtureError('route_not_found','夹具没有这条路由'),404);}
 if(url.pathname==='/tests/artifacts-fixture.html'){
  const html=await readFile(path.resolve(import.meta.dirname,'artifacts-fixture.html'),'utf8');
  const roots=JSON.stringify([path.join(profile,'uploads')+'/',path.join(profile,'workspace')+'/',path.join(profile,'images')+'/',path.join(profile,'cron','output')+'/']);
  res.writeHead(200,{'Content-Type':'text/html; charset=utf-8'});
  res.end(html.replace('<head>',`<head><script>window.__CLAWD_ARTIFACT_ROOTS__=${roots};</script>`));
  return;
 }
 if(!url.pathname.startsWith('/panel-')) return next();
 state.requests.push({method:req.method,path:url.pathname,query:url.search});
 let parsed;
 if(req.method!=='GET' && req.method!=='HEAD'){
  let raw='';for await(const chunk of req)raw+=chunk;
  try{parsed=raw?JSON.parse(raw):{};}catch{parsed=null;}
 }
 const decision=artifactsJsonDecision(req.method,url.pathname,url.searchParams,state,parsed);
 if(decision.create){
  if(parsed==null) return json(res,fixtureError('invalid_request','Invalid JSON'),400);
  state.posts.push(parsed);
  const admitted=admitFixtureRun(parsed,headerValue(req),Date.now()/1000,state.runsByKey);
  if(!admitted.run||admitted.replay) return json(res,admitted.body,admitted.status);
  state.run=admitted.run;
  state.messages.push({id:7,role:'user',content:parsed.input,timestamp:admitted.run.created_at});
  event({event:'message.interim',text:`旁白文件\nMEDIA:${archive}`,already_streamed:false});
  return json(res,admitted.body,admitted.status);
 }
 if(req.method!=='GET' && req.method!=='HEAD') state.posts.push(req.url);
 if(decision.proxy){
  const upstream=httpRequest({hostname:'127.0.0.1',port:sidecarPort,path:'/files/artifact'+url.search,headers:{'X-Panel-Key':'browser-fixture-only'}},response=>{res.writeHead(response.statusCode,response.headers);response.pipe(res);});
  upstream.on('error',error=>{if(!res.headersSent)json(res,{error:error.message},502);else res.destroy(error);});
  res.on('close',()=>upstream.destroy());
  upstream.end();return;
 }
 if(decision.stream){
  const run=decision.run;
  res.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-cache'});
  const seq=Number(url.searchParams.get('last_seq')??-1);for(let i=seq+1;i<run.events.length;i++)res.write(run.events[i]);
  if(TERMINAL.has(run.status))return res.end();run.followers.add(res);req.on('close',()=>run.followers.delete(res));return;
 }
 return json(res,decision.body,decision.status);
 };server.middlewares.use(handler);return ()=>{const stack=server.middlewares.stack;const index=stack.findIndex((layer)=>layer.handle===handler);if(index>0)stack.unshift(stack.splice(index,1)[0]);};}};
const rootDir = path.resolve(import.meta.dirname, '..');
const server=await createServer({
 configFile: path.join(rootDir, 'vite.config.ts'),
 root: rootDir,
 cacheDir: path.join(profile, 'vite'),
 plugins:[backend],
 server:{host:'127.0.0.1',port:0,hmr:false,watch:null},
 logLevel:'error',
});
let browser,socket,failure;
try{
 await Promise.all([mkdir(path.join(profile,'uploads'),{recursive:true}),mkdir(workspace,{recursive:true}),mkdir(path.join(profile,'images'),{recursive:true}),mkdir(path.join(profile,'cron','output'),{recursive:true}),writeFile(image,png),writeFile(file,fileBytes),writeFile(archive,fileBytes)]);
 const python=`import sys\nfrom pathlib import Path\nfrom sidecar.hermes_panel_sidecar import Handler, ThreadingHTTPServer, FILE_ROOTS\nFILE_ROOTS.clear()\nhome=Path(sys.argv[1])\nFILE_ROOTS['uploads']={'path':home/'uploads','label':'上传','writable':True,'create':True}\nFILE_ROOTS['workspace']={'path':home/'workspace','label':'工作区','writable':False,'create':True}\nFILE_ROOTS['images']={'path':home/'images','label':'图片','writable':False,'create':True}\nFILE_ROOTS['cron']={'path':home/'cron'/'output','label':'定时任务输出','writable':False,'create':True}\nHandler.key='browser-fixture-only'\nserver=ThreadingHTTPServer(('127.0.0.1',0),Handler)\nserver.daemon_threads=True\nprint(server.server_address[1],flush=True)\nserver.serve_forever()\n`;
 sidecar=spawn('python3',['-u','-c',python,profile],{stdio:['ignore','pipe','pipe'],env:{...process.env,CLAWD_HOME:profile,HERMES_HOME:path.join(profile,'hermes-home')}});
 sidecarPort=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(new Error('Sidecar startup timeout')),10000);sidecar.stdout.on('data',chunk=>{output+=chunk;const match=output.match(/^([0-9]+)\n/);if(match){clearTimeout(timer);resolve(Number(match[1]));}});sidecar.once('error',error=>{clearTimeout(timer);reject(error);});sidecar.once('exit',code=>{clearTimeout(timer);reject(new Error(`Sidecar exited ${code}`));});});
 await server.listen(); const port=server.httpServer.address().port;
 browser=spawn('/usr/bin/chromium',['--headless','--no-sandbox','--disable-dev-shm-usage','--remote-debugging-port=0',`--user-data-dir=${profile}`,'about:blank'],{stdio:['ignore','ignore','pipe']});
 const wsUrl=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(new Error('CDP startup timeout')),15000);browser.stderr.on('data',chunk=>{output+=chunk;const match=output.match(/DevTools listening on (ws:\/\/\S+)/);if(match){clearTimeout(timer);resolve(match[1]);}});browser.on('error',reject);});
 socket=new WebSocket(wsUrl);await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true});});
 let seq=0; const pending=new Map();
 socket.addEventListener('message',event=>{const msg=JSON.parse(event.data);const p=pending.get(msg.id);if(!p)return;pending.delete(msg.id);clearTimeout(p.timer);msg.error?p.reject(new Error(JSON.stringify(msg.error))):p.resolve(msg.result);});
 const call=(method,params={},sessionId)=>new Promise((resolve,reject)=>{const id=++seq;const timer=setTimeout(()=>{pending.delete(id);reject(new Error(`${method} timeout`));},15000);pending.set(id,{resolve,reject,timer});socket.send(JSON.stringify({id,method,params,...(sessionId?{sessionId}:{})}));});
 const client=async mobile=>{
  const {browserContextId}=await call('Target.createBrowserContext'); const {targetId}=await call('Target.createTarget',{url:'about:blank',browserContextId});const {sessionId}=await call('Target.attachToTarget',{targetId,flatten:true});
  await call('Emulation.setDeviceMetricsOverride',{width:mobile?390:1280,height:800,deviceScaleFactor:1,mobile},sessionId);
  await call('Emulation.setFocusEmulationEnabled',{enabled:true},sessionId);
  const evaluate=async expression=>{const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true},sessionId);if(r.exceptionDetails)throw new Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
  const wait=async(expression,message=expression)=>{for(let n=0;n<80;n++){if(await evaluate(expression))return;await new Promise(resolve=>setTimeout(resolve,100));}assert.fail(`${message}: ${await evaluate('chat.shadowRoot.textContent')}`);};
  await call('Page.navigate',{url:`http://127.0.0.1:${port}/tests/artifacts-fixture.html`},sessionId);await wait('window.ready && !chat.loading');return{evaluate,wait,sessionId,browserContextId};
 };
 const desktop=await client(false);
 await desktop.wait('artifactRoots().some(r=>r?.querySelector("img")?.naturalWidth>0)','history generated image must load inline');
 assert.equal(await desktop.evaluate('artifactRoots().flatMap(r=>[...r.querySelectorAll("a[download]")]).length'),0,'image download must not be a navigating link');
 assert.equal(await desktop.evaluate('artifactRoots().flatMap(r=>[...r.querySelectorAll("button.save")]).length'),1);
 assert.equal(state.posts.length,0);
 console.log('PASS history MEDIA image loaded inline');
 state.messages.push({id:3,role:'user',content:'结构化合成图片',timestamp:3},{id:4,role:'assistant',content:[{type:'image_url',image_url:{url:`data:image/png;base64,${png.toString('base64')}`}}],timestamp:4});
 await desktop.evaluate('chat.load("artifacts-test")');
 await desktop.wait('artifactRoots().flatMap(r=>[...r.querySelectorAll("img")]).filter(i=>i.naturalWidth>0).length===2','structured history must render inline');
 console.log('PASS structured image_url history loaded inline');
 await desktop.evaluate('artifactRoots().find(r=>r.querySelector("img")?.src.startsWith("data:")).querySelector(".actions a,.actions button").click()');
 await desktop.wait('artifactRoots().some(r=>r.querySelector("dialog")?.open && r.querySelector("dialog img")?.naturalWidth>0)','data image enlargement must display a loaded modal, not blocked top-level data navigation');
 await desktop.evaluate('artifactRoots().find(r=>r.querySelector("dialog")?.open).querySelector("dialog [data-close]").click()');
 await desktop.wait('artifactRoots().every(r=>!r.querySelector("dialog")?.open)');
 state.messages[1].content=body+'\n```text\nMEDIA:/root/.hermes/images/code.png\n```';
 await desktop.evaluate('chat.load("artifacts-test")');
 await desktop.wait('artifactRoots().some(r=>r.querySelector("[data-copy]"))');
 await desktop.evaluate('window.copied=null; Object.defineProperty(navigator,"clipboard",{configurable:true,value:{writeText:async text=>{window.copied=text;}}}); artifactRoots().find(r=>r.querySelector("[data-copy]")).querySelector("[data-copy]").click()');
 await desktop.wait('window.copied==="MEDIA:/root/.hermes/images/code.png"','assistant code copy must remain working across new shadow boundary');
 state.messages.push({id:5,role:'user',content:'安全测试',timestamp:5},{id:6,role:'assistant',content:'<p style="background-image:url(/unsafe-fetch/css)">安全文本</p>\n<img src="/unsafe-fetch/image" onerror="window.injected=true">\n<svg><image href="/unsafe-fetch/svg"></image></svg>\n![远程图](/unsafe-fetch/markdown)\n[危险](javascript:alert(1))',timestamp:6});
 await desktop.evaluate('chat.load("artifacts-test")');
 await desktop.wait('artifactRoots().some(r=>r.textContent.includes("安全文本"))');
 await new Promise(resolve=>setTimeout(resolve,300));
 assert.deepEqual(state.requests.filter(r=>r.path.startsWith('/unsafe-fetch')),[],'assistant prose must not trigger arbitrary image/CSS/SVG fetch');
 assert.equal(await desktop.evaluate('window.injected'),undefined);
 console.log('PASS code-copy preserved; CSS/SVG/markdown arbitrary fetch and XSS blocked');
 await desktop.evaluate('fixtureRuns.start("artifacts-test","实时合成请求",[],"实时合成请求","test-model","fixture")');
 await desktop.wait('fixtureRuns.get("artifacts-test")?.runId && artifactRoots().some(r=>r.textContent.includes("合成.zip"))','live commentary file must render');
 const final=['实时结果','![图片](<'+image+'>)','"'+file+'"','MEDIA:'+path.join(workspace,'不存在.png'),'`MEDIA:/root/.hermes/images/inline.png`','','```text','MEDIA:/root/.hermes/images/fenced.png','```'].join('\n');
 event({event:'message.delta',delta:final});
 await desktop.wait('artifactRoots().some(r=>r.querySelector(".error")?.textContent.includes("图片无法预览"))','missing live image must retain Chinese fallback');
 assert.ok(await desktop.evaluate('artifactRoots().some(r=>r.textContent.includes("报告 结果.txt") && r.textContent.includes("下载文件"))'));
 await desktop.wait('artifactRoots().some(r=>[...r.querySelectorAll("code")].some(c=>c.textContent.includes("fenced.png")))','fenced media stays inside code');
 assert.equal(state.requests.some(r=>r.query?.includes('inline.png')||r.query?.includes('fenced.png')),false);
 const download=await desktop.evaluate(`(async()=>{const name=${JSON.stringify(file)}.split('/').at(-1);const url='/panel-sidecar/files/artifact?path='+encodeURIComponent(${JSON.stringify(file)});const r=await fetch(url);return {status:r.status,bytes:[...new Uint8Array(await r.arrayBuffer())],disposition:r.headers.get('Content-Disposition'),button:!!saveButton(name)};})()`);
 assert.equal(download.status,200);assert.deepEqual(Buffer.from(download.bytes),fileBytes);assert.match(download.disposition,/attachment/);
 const downloads=path.join(profile,'downloads');await mkdir(downloads);
 await call('Browser.setDownloadBehavior',{behavior:'allow',downloadPath:downloads,browserContextId:desktop.browserContextId,eventsEnabled:true});
 const beforePage=await desktop.evaluate('location.href');
 await desktop.evaluate(`saveButton(${JSON.stringify(file)}.split('/').at(-1)).click()`);
 let downloaded;
 for(let i=0;i<80;i++){try{downloaded=await readFile(path.join(downloads,path.basename(file)));break;}catch(error){if(error.code!=='ENOENT')throw error;await new Promise(resolve=>setTimeout(resolve,100));}}
 assert.deepEqual(downloaded,fileBytes,'actual download click must save identical bytes');
 assert.equal(await desktop.evaluate('location.href'),beforePage,'download must not replace the page');
 const imageDownload=await desktop.evaluate(`(async()=>{const img=[...document.querySelectorAll('.artifact img')].find(node=>node.alt==='测试 图片.png');const url=img.src.replace(/([?&])preview=1&?/,'$1').replace(/[?&]$/,'');const r=await fetch(url);return [...new Uint8Array(await r.arrayBuffer())];})()`);
 assert.deepEqual(Buffer.from(imageDownload),png);
 const previews=state.requests.filter(r=>r.path.endsWith('/files/artifact')&&r.query?.includes('preview=1')).length;
 await desktop.evaluate('(async()=>{for(let i=0;i<10;i++)chat.requestUpdate(); await chat.updateComplete})()');
 await new Promise(resolve=>setTimeout(resolve,200));
 assert.equal(state.requests.filter(r=>r.path.endsWith('/files/artifact')&&r.query?.includes('preview=1')).length,previews,'reactive rerender must not repeat preview fetch');
 assert.ok(await desktop.evaluate('artifactRoots().some(r=>r.querySelector(".error") && [...r.querySelectorAll("button.save")].some(a=>a.closest(".artifact")?.querySelector(".name")?.textContent==="不存在.png"))'));
 assert.equal(state.requests.some(r=>r.query?.includes('preview=1')&&(r.query.includes(encodeURIComponent(file))||r.query.includes(encodeURIComponent(archive)))),false,'non-image files never preview');
 console.log('PASS live final and commentary, missing preview fallback, code untouched, download byte identity and stable preview requests');
 state.run.status='completed';state.run.updated_at=Date.now()/1000;
 state.messages.push({id:8,role:'assistant',content:`旁白文件\nMEDIA:${archive}`,tool_calls:[{id:'synthetic-tool',function:{name:'terminal',arguments:'{}'}}],timestamp:state.run.updated_at},{id:9,role:'tool',tool_call_id:'synthetic-tool',content:'合成工具输出',timestamp:state.run.updated_at},{id:10,role:'assistant',content:final,timestamp:state.run.updated_at});
 event({event:'run.completed'});for(const res of state.run.followers)res.end();
 await desktop.wait('!chat.live && artifactRoots().some(r=>r.textContent.includes("报告 结果.txt"))');
 const generation=await desktop.evaluate('fixtureGeneration');
 await call('Page.reload',{},desktop.sessionId);
 await desktop.wait(`window.ready && fixtureGeneration!==${JSON.stringify(generation)} && !chat.loading && artifactRoots().some(r=>r.querySelector('.error'))`);
 const mobile=await client(true);
 await mobile.wait('artifactRoots().flatMap(r=>[...r.querySelectorAll("img")]).filter(i=>i.naturalWidth>0).length>=3');
 for(const c of [desktop,mobile]){
  assert.ok(await c.evaluate('artifactRoots().some(r=>r.textContent.includes("合成.zip")) && artifactRoots().some(r=>r.textContent.includes("报告 结果.txt"))'));
  assert.ok(await c.evaluate('artifactRoots().flatMap(r=>[...r.querySelectorAll(".artifact")]).every(e=>{const b=e.getBoundingClientRect();return b.left>=0 && b.right<=innerWidth+1})'),'artifact cards must fit viewport');
  assert.ok(await c.evaluate('artifactRoots().flatMap(r=>[...r.querySelectorAll("a")]).filter(a=>a.textContent==="放大查看").every(a=>a.tabIndex===0 && a.target==="_blank" && a.rel.includes("noopener"))'));
 }
 await mobile.evaluate('artifactRoots().flatMap(r=>[...r.querySelectorAll("a")]).find(a=>a.textContent==="放大查看").focus()');
 await call('Page.bringToFront',{},mobile.sessionId);
 await call('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13,text:'\r',unmodifiedText:'\r'},mobile.sessionId);
 await call('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13},mobile.sessionId);
 const targets=await call('Target.getTargets');
 assert.ok(targets.targetInfos.some(t=>t.url.includes('/files/artifact?path=')&&t.url.includes('preview=1')),'keyboard enlarge must open image preview');
 assert.equal(state.posts.length,1,'reload and observing mobile must not submit another task');
 console.log('PASS refreshed history/commentary, desktop + 390px fit, keyboard enlarge, no extra model submission');
}catch(error){failure=error;}finally{
 for(const res of state.run?.followers??[])res.end();
 socket?.close();if(browser){browser.kill('SIGTERM');await new Promise(resolve=>browser.exitCode!==null?resolve():browser.once('exit',resolve));}await server.close();await stopProcess(sidecar);await rm(profile,{recursive:true,force:true,maxRetries:5,retryDelay:200});
}
if(failure)throw failure;
}
