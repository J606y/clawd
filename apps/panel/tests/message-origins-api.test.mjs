import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {createServer} from 'vite';
const original=new Map(['document','window','fetch'].map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
const browser=Object.assign(new EventTarget(),{setTimeout,clearTimeout});
Object.defineProperty(globalThis,'document',{configurable:true,value:{baseURI:'https://example.test/__next/'}});
Object.defineProperty(globalThis,'window',{configurable:true,value:browser});
const tmp=await mkdtemp(join(process.env.TMPDIR||'/root/hermes-workspace','origins-api-'));
const server=await createServer({root:resolve(import.meta.dirname,'..'),configFile:false,cacheDir:join(tmp,'cache'),server:{middlewareMode:true,hmr:false},appType:'custom'});
try{
 const {api}=await server.ssrLoadModule('/src/lib/api.ts');
 await test('only scoped read-only provenance annotates generic notification input',async()=>{
  const calls=[];globalThis.fetch=async(url,init)=>{url=new URL(url);calls.push(url);assert.equal(init.body,undefined);assert.ok(!init.method||init.method==='GET');
   if(url.pathname.endsWith('/message-origins')){assert.equal(url.pathname,'/__next/panel-core/sessions/message-origins');assert.equal(url.searchParams.get('session_id'),'s/with space');assert.equal(url.searchParams.get('ids'),'3,4');return Response.json({items:{'3':'api_queue','4':'api_steer','2':'api_queue'}})}
   return Response.json({data:[{id:2,role:'assistant',content:'saved',input_origin:'api_queue'},{id:3,role:'user',content:'input',display_kind:'internal_notification'},{id:4,role:'user',content:'steer',display_kind:'internal_notification'}],pagination:{returned:3}})};
  const page=await api.messages('s/with space',{limit:3,offset:0});assert.equal(page.hasMore,true);assert.equal(page.messages[0].input_origin,undefined);assert.equal(page.messages[1].input_origin,'api_queue');assert.equal(page.messages[2].input_origin,'api_steer');assert.equal(calls.length,2);
 });
 await test('unavailable or malformed provenance cannot erase history or invent input origin',async()=>{
  const warn=console.warn;const warnings=[];console.warn=(...args)=>warnings.push(args);
  try{for(const response of [()=>Response.json({error:'offline'},{status:503}),()=>Response.json({items:[]}),()=>Response.json({items:{3:'human-guessed'}})]){
   globalThis.fetch=async(url)=>new URL(url).pathname.endsWith('/message-origins')?response():Response.json({data:[{id:3,role:'user',content:'input',display_kind:'internal_notification',input_origin:'api_queue'}]});
   const page=await api.messages('s',{limit:100,offset:0});assert.equal(page.messages.length,1);assert.equal(page.messages[0].input_origin,undefined);
  }assert.equal(warnings.length,2)}finally{console.warn=warn}
 });
 await test('origin-query authorization failure and caller abort remain failures',async()=>{
  globalThis.fetch=async(url)=>new URL(url).pathname.endsWith('/message-origins')?Response.json({error:'expired'},{status:401}):Response.json({data:[{id:3,role:'user',content:'x',display_kind:'internal_notification'}]});
  await assert.rejects(api.messages('s',{limit:100,offset:0}),e=>e.status===401);
  const ctrl=new AbortController();globalThis.fetch=async(url)=>{if(new URL(url).pathname.endsWith('/message-origins')){ctrl.abort();throw new DOMException('aborted','AbortError')}return Response.json({data:[{id:3,role:'user',content:'x',display_kind:'internal_notification'}]})};
  await assert.rejects(api.messages('s',{limit:100,offset:0},ctrl.signal),e=>e.name==='AbortError');
 });
}finally{await server.close();await rm(tmp,{recursive:true,force:true});for(const[k,v]of original){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k]}}
