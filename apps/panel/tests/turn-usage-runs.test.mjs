import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'vite';
import {mkdtemp,rm} from 'node:fs/promises';
import path from 'node:path';
const keys=['document','window','navigator','localStorage'];
const original=Object.fromEntries(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
const storage={getItem:()=>null,setItem:()=>{}};const win=new EventTarget();Object.assign(win,{localStorage:storage,setTimeout,clearTimeout});
for(const [k,v] of Object.entries({window:win,document:Object.assign(new EventTarget(),{baseURI:'https://fixture.invalid/',visibilityState:'visible'}),navigator:{onLine:true},localStorage:storage}))Object.defineProperty(globalThis,k,{configurable:true,value:v});
const owned=await mkdtemp(path.join(process.env.TMPDIR||'/root/hermes-workspace','turn-usage-'));
const server=await createServer({configFile:false,cacheDir:path.join(owned,'vite'),server:{middlewareMode:true},appType:'custom'});
try{
 const {runs}=await server.ssrLoadModule('/src/lib/runs.ts');
 const {formatTurnTokens}=await server.ssrLoadModule('/src/lib/turn-usage.ts');
 const fresh=()=>({runId:'fixture',sessionId:'fixture',startedAt:Date.now(),lastEventAt:Date.now(),status:'running',text:'',thinking:'',steps:[],steers:[],approval:null});
 await test('actual RunStore counts one turn across tool boundaries and rejects duplicate sequence',()=>{
  const r=fresh();
  const ev=d=>runs.applyRunEvent(r,d);
  ev({event:'message.delta',seq:1,delta:'a'.repeat(40)});
  ev({event:'message.interim',seq:2,text:'a'.repeat(40),already_streamed:true});
  ev({event:'tool.started',seq:3,tool:'fixture',id:'call-a'});
  const delta={event:'message.delta',seq:4,delta:'b'.repeat(40)};ev(delta);ev(delta);
  ev({event:'reasoning.available',seq:5,text:'c'.repeat(40)});
  ev({event:'reasoning.available',seq:6,text:'c'.repeat(40)});
  assert.equal(formatTurnTokens(r.usage),'~30 tokens');
  assert.equal(r.text,'b'.repeat(40));
  const again=fresh();
  runs.applyRunEvent(again,{event:'reasoning.available',seq:1,text:'d'.repeat(40)});
  runs.applyRunEvent(again,{event:'tool.started',seq:2,tool:'skill',id:'skill-1',preview:'e'.repeat(40)});
  runs.applyRunEvent(again,{event:'tool.completed',seq:3,tool:'skill',id:'skill-1',preview:'x'.repeat(400)});
  runs.applyRunEvent(again,{event:'reasoning.available',seq:4,text:'f'.repeat(40)});
  runs.applyRunEvent(again,{event:'message.delta',seq:5,delta:'g'.repeat(40)});
  assert.equal(formatTurnTokens(again.usage),'~40 tokens');
  assert.equal(formatTurnTokens(fresh().usage),'');
  assert.equal(formatTurnTokens(fresh().usage),'');
 });
 await test('truncated, gapped or lost replay makes estimates unavailable',()=>{
  for(const kind of ['truncated','gap','lost']){
   const r=fresh();runs.applyRunEvent(r,{event:'message.delta',seq:1,delta:'a'.repeat(40)});
   if(kind==='truncated')runs.applyRunEvent(r,{event:'replay.truncated',seq:2});
   if(kind==='gap')runs.applyRunEvent(r,{event:'message.delta',seq:3,delta:'b'.repeat(40)});
   if(kind==='lost')r.streamGone=true;
   assert.equal(formatTurnTokens(r.streamGone?undefined:r.usage),'');
   if(kind!=='lost')assert.equal(r.usage.available,false);
  }
 });
}finally{
 await server.close();await rm(owned,{recursive:true,force:true,maxRetries:3,retryDelay:100});
 for(const k of keys){if(original[k])Object.defineProperty(globalThis,k,original[k]);else delete globalThis[k];}
}
