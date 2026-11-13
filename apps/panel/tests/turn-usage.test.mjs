import test from 'node:test';
import assert from 'node:assert/strict';
import {createTurnUsage,applyTurnUsage,formatTurnTokens,formatRealTokens,parseServerUsage,settleTurnUsage,turnOutputTokens} from '../src/lib/turn-usage.ts';

test('observable output accumulates without counting echoed commentary or repeated thinking snapshots',()=>{
 const u=createTurnUsage();
 applyTurnUsage(u,'assistant.delta',{delta:'a'.repeat(40)});
 applyTurnUsage(u,'assistant.commentary',{text:'a'.repeat(40),already_streamed:true});
 applyTurnUsage(u,'tool.progress',{tool_name:'_thinking',delta:'b'.repeat(40)});
 applyTurnUsage(u,'tool.progress',{tool_name:'_thinking',delta:'b'.repeat(40)});
 assert.equal(formatTurnTokens(u),'~20 tokens');
 applyTurnUsage(u,'assistant.commentary',{text:'c'.repeat(40)});
 assert.equal(formatTurnTokens(u),'~30 tokens');
});
test('tool input is counted once while tool results and user input are excluded',()=>{
 const u=createTurnUsage(); const args={command:'safe fixture'};
 applyTurnUsage(u,'tool.started',{call_id:'a',args});
 applyTurnUsage(u,'tool.started',{call_id:'a',args});
 applyTurnUsage(u,'tool.completed',{preview:'x'.repeat(4000)});
 applyTurnUsage(u,'user.message',{text:'x'.repeat(4000)});
 assert.equal(u.argumentChars,JSON.stringify(args).length);
 assert.equal(u.outputChars,0);
});
test('one turn keeps thinking and skill calls until a new usage object starts',()=>{
 const turn=createTurnUsage();
 applyTurnUsage(turn,'tool.progress',{tool_name:'_thinking',delta:'a'.repeat(40)});
 applyTurnUsage(turn,'tool.started',{call_id:'skill-1',preview:'p'.repeat(40)});
 applyTurnUsage(turn,'tool.completed',{preview:'x'.repeat(400)});
 applyTurnUsage(turn,'tool.progress',{tool_name:'_thinking',delta:'b'.repeat(40)});
 applyTurnUsage(turn,'assistant.delta',{delta:'c'.repeat(40)});
 assert.equal(formatTurnTokens(turn),'~40 tokens');
 const next=createTurnUsage();
 assert.equal(formatTurnTokens(next),'');
 assert.equal(next.settledThinkingChars,0);
});
test('Chinese thinking is counted near 0.6 tokens per character, not one per character',()=>{
 const u=createTurnUsage();
 applyTurnUsage(u,'tool.progress',{tool_name:'_thinking',delta:'思'.repeat(40)});
 assert.equal(formatTurnTokens(u),'~24 tokens');
 applyTurnUsage(u,'assistant.delta',{delta:'okay'});
 assert.equal(formatTurnTokens(u),'~25 tokens');
});
test('unavailable data and invalid numbers never render fabricated zero',()=>{
 const u=createTurnUsage();assert.equal(formatTurnTokens(u),'');
 applyTurnUsage(u,'assistant.delta',{delta:'x'.repeat(4000)});assert.equal(formatTurnTokens(u),'~1k tokens');
 u.available=false;assert.equal(formatTurnTokens(u),'');
 for(const n of [NaN,Infinity,-1]){const invalid=createTurnUsage();invalid.outputChars=n;assert.equal(formatTurnTokens(invalid),'');}
 assert.equal(formatTurnTokens(createTurnUsage()),'');
});
test('server usage parses the run.completed payload and rejects bad shapes',()=>{
 assert.deepEqual(parseServerUsage({input_tokens:1000,output_tokens:500,total_tokens:1500,cache_read_tokens:800,cache_write_tokens:0}),{inputTokens:1000,outputTokens:500,totalTokens:1500,cacheReadTokens:800,cacheWriteTokens:0});
 assert.deepEqual(parseServerUsage({input_tokens:1,output_tokens:0}),{inputTokens:1,outputTokens:0,totalTokens:0,cacheReadTokens:0,cacheWriteTokens:0});
 assert.equal(parseServerUsage({input_tokens:1}),null);
 assert.equal(parseServerUsage({input_tokens:1,output_tokens:1.5}),null);
 assert.equal(parseServerUsage({input_tokens:1,output_tokens:-1}),null);
 assert.equal(parseServerUsage({input_tokens:1,output_tokens:'500'}),null);
 assert.equal(parseServerUsage({input_tokens:1,output_tokens:500,cache_read_tokens:'x'}),null);
 assert.equal(parseServerUsage(null),null);
 assert.equal(parseServerUsage('nope'),null);
});
test('turn output is the delta of session-cumulative counters',()=>{
 const first={inputTokens:1000,outputTokens:500,totalTokens:1500,cacheReadTokens:0,cacheWriteTokens:0};
 const second={inputTokens:2000,outputTokens:1200,totalTokens:3200,cacheReadTokens:1000,cacheWriteTokens:0};
 assert.equal(turnOutputTokens(first,second),700);
 assert.equal(turnOutputTokens(null,second),null);
 assert.equal(turnOutputTokens(second,first),null);
 assert.equal(turnOutputTokens(first,first),0);
});
test('settled real usage replaces the estimate and formats without an approximation mark',()=>{
 const u=createTurnUsage();
 applyTurnUsage(u,'assistant.delta',{delta:'a'.repeat(40)});
 assert.equal(formatTurnTokens(u),'~10 tokens');
 settleTurnUsage(u,1234);
 assert.equal(formatTurnTokens(u),'1.2k tokens');
 assert.equal(formatRealTokens(1234),'1.2k tokens');
 assert.equal(formatRealTokens(42),'42 tokens');
 assert.equal(formatRealTokens(0),'');
 assert.equal(formatRealTokens(NaN),'');
 // Real usage wins even when the stream marked the estimate unavailable.
 u.available=false;
 assert.equal(formatTurnTokens(u),'1.2k tokens');
});
