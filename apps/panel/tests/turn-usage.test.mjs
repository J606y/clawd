import test from 'node:test';
import assert from 'node:assert/strict';
import {createTurnUsage,applyTurnUsage,formatTurnTokens} from '../src/lib/turn-usage.ts';

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
test('Chinese thinking is counted near one token per character, not one per four',()=>{
 const u=createTurnUsage();
 applyTurnUsage(u,'tool.progress',{tool_name:'_thinking',delta:'思'.repeat(40)});
 assert.equal(formatTurnTokens(u),'~40 tokens');
 applyTurnUsage(u,'assistant.delta',{delta:'okay'});
 assert.equal(formatTurnTokens(u),'~41 tokens');
});
test('unavailable data and invalid numbers never render fabricated zero',()=>{
 const u=createTurnUsage();assert.equal(formatTurnTokens(u),'');
 applyTurnUsage(u,'assistant.delta',{delta:'x'.repeat(4000)});assert.equal(formatTurnTokens(u),'~1k tokens');
 u.available=false;assert.equal(formatTurnTokens(u),'');
 for(const n of [NaN,Infinity,-1]){const invalid=createTurnUsage();invalid.outputChars=n;assert.equal(formatTurnTokens(invalid),'');}
 assert.equal(formatTurnTokens(createTurnUsage()),'');
});
