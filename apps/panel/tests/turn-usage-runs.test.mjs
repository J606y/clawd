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
 const {formatTurnTokens,parseServerUsage,turnOutputTokens,settleTurnUsage,formatRealTokens,createTurnUsage,applyTurnUsage}=await server.ssrLoadModule('/src/lib/turn-usage.ts');
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
 await test('server usage helpers parse deltas, settle real totals, and estimate Chinese near 0.6',()=>{
  assert.deepEqual(parseServerUsage({input_tokens:1000,output_tokens:500,total_tokens:1500,cache_read_tokens:800,cache_write_tokens:0}),{inputTokens:1000,outputTokens:500,totalTokens:1500,cacheReadTokens:800,cacheWriteTokens:0});
  assert.equal(parseServerUsage({input_tokens:1}),null);
  assert.equal(parseServerUsage({input_tokens:1,output_tokens:1.5}),null);
  assert.equal(parseServerUsage({input_tokens:1,output_tokens:'500'}),null);
  const first={inputTokens:1000,outputTokens:500,totalTokens:1500,cacheReadTokens:0,cacheWriteTokens:0};
  const second={inputTokens:2000,outputTokens:1200,totalTokens:3200,cacheReadTokens:1000,cacheWriteTokens:0};
  assert.equal(turnOutputTokens(first,second),700);
  assert.equal(turnOutputTokens(null,second),null);
  assert.equal(turnOutputTokens(second,first),null);
  const u=createTurnUsage();
  applyTurnUsage(u,'assistant.delta',{delta:'a'.repeat(40)});
  assert.equal(formatTurnTokens(u),'~10 tokens');
  settleTurnUsage(u,1234);
  assert.equal(formatTurnTokens(u),'1.2k tokens');
  assert.equal(formatRealTokens(42),'42 tokens');
  assert.equal(formatRealTokens(0),'');
  u.available=false;
  assert.equal(formatTurnTokens(u),'1.2k tokens');
  const zh=createTurnUsage();
  applyTurnUsage(zh,'tool.progress',{tool_name:'_thinking',delta:'思'.repeat(40)});
  assert.equal(formatTurnTokens(zh),'~24 tokens');
  applyTurnUsage(zh,'assistant.delta',{delta:'okay'});
  assert.equal(formatTurnTokens(zh),'~25 tokens');
 });
 await test('run.completed settles the token label from server counters',()=>{
  const sid='settle-fixture';
  const first=()=>({runId:'settle-1',sessionId:sid,startedAt:Date.now(),lastEventAt:Date.now(),status:'running',text:'',thinking:'',steps:[],steers:[],approval:null});
  const r1=first();
  runs.applyRunEvent(r1,{event:'message.delta',seq:1,delta:'a'.repeat(40)});
  assert.equal(formatTurnTokens(r1.usage),'~10 tokens');
  // First run observed: no baseline yet, so the estimate stays.
  runs.applyRunEvent(r1,{event:'run.completed',seq:2,usage:{input_tokens:1000,output_tokens:500,total_tokens:1500,cache_read_tokens:800}});
  assert.equal(r1.tokenReal,false);
  assert.equal(r1.tokenLabel,'~10 tokens');
  assert.equal(typeof r1.endedAt,'number');
  // Second run: the delta against the first snapshot is the real turn output.
  const r2=first();
  runs.applyRunEvent(r2,{event:'message.delta',seq:1,delta:'a'.repeat(40)});
  runs.applyRunEvent(r2,{event:'run.completed',seq:2,usage:{input_tokens:2000,output_tokens:1200,total_tokens:3200,cache_read_tokens:1000}});
  assert.equal(r2.tokenReal,true);
  assert.equal(r2.tokenLabel,'700 tokens');
  // Malformed usage never replaces the label.
  const r3=first();
  runs.applyRunEvent(r3,{event:'message.delta',seq:1,delta:'a'.repeat(40)});
  runs.applyRunEvent(r3,{event:'run.completed',seq:2,usage:{input_tokens:2000}});
  assert.equal(r3.tokenReal,undefined);
  assert.equal(r3.tokenLabel,'~10 tokens');
 });
 await test('runStatus polling settles the token label when the event stream is lost',()=>{
  const sid='poll-fixture';
  const mk=(id)=>({runId:id,sessionId:sid,startedAt:Date.now(),lastEventAt:Date.now(),status:'running',text:'',thinking:'',steps:[],steers:[],approval:null});
  // Seed the baseline with one event-path completion.
  const r1=mk('poll-1');
  runs.applyRunEvent(r1,{event:'run.completed',seq:1,usage:{input_tokens:1000,output_tokens:500,total_tokens:1500,cache_read_tokens:800}});
  assert.equal(r1.tokenReal,false);
  // Second run: no run.completed event arrives, polling finds it completed.
  const r2=mk('poll-2');
  runs.applyRunEvent(r2,{event:'message.delta',seq:1,delta:'a'.repeat(40)});
  assert.equal(formatTurnTokens(r2.usage),'~10 tokens');
  assert.equal(runs.finishFromStatus(r2,{run_id:'poll-2',session_id:sid,status:'completed',usage:{input_tokens:2000,output_tokens:1200,total_tokens:3200,cache_read_tokens:1000}}),true);
  assert.equal(r2.status,'completed');
  assert.equal(r2.tokenReal,true);
  assert.equal(r2.tokenLabel,'700 tokens');
  assert.equal(typeof r2.endedAt,'number');
  // A polling payload without usage keeps the estimate.
  const r3=mk('poll-3');
  runs.applyRunEvent(r3,{event:'message.delta',seq:1,delta:'a'.repeat(40)});
  assert.equal(runs.finishFromStatus(r3,{run_id:'poll-3',session_id:sid,status:'completed'}),true);
  assert.equal(r3.tokenReal,undefined);
  assert.equal(r3.tokenLabel,'~10 tokens');
  // A foreign run identity is never settled.
  const r4=mk('poll-4');
  assert.equal(runs.finishFromStatus(r4,{run_id:'poll-4',session_id:'other',status:'completed',usage:{input_tokens:3000,output_tokens:2000,total_tokens:5000}}),false);
  assert.equal(r4.status,'running');
 });
}finally{
 await server.close();await rm(owned,{recursive:true,force:true,maxRetries:3,retryDelay:100});
 for(const k of keys){if(original[k])Object.defineProperty(globalThis,k,original[k]);else delete globalThis[k];}
}
