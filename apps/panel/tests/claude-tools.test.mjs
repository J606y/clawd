import test from 'node:test';
import assert from 'node:assert/strict';
import * as tools from '../src/lib/tools.ts';
const step = (name, args = null, status = 'running') => ({kind:'tool', id:'fixture', name, args, status, output:null, preview:null});
test('MCP display separates server and exact tool identifier', () => {
 assert.equal(tools.toolLabel('mcp__moss__get_status'), 'moss - get_status (MCP)');
 assert.equal(tools.toolLabel('functions.mcp__github__get_me'), 'github - get_me (MCP)');
 assert.equal(tools.toolLabel('mcp__my_server__custom_tool'), 'my_server - custom_tool (MCP)');
 assert.equal(tools.toolLabel('mcp__moss__'), 'mcp__moss__');
 assert.equal(tools.toolLabel('terminal'), 'Bash');
});
test('deferred and parallel MCP calls expose actual servers without guessing scripts', () => {
 assert.equal(typeof tools.toolActivity, 'function');
 assert.equal(tools.toolActivity(step('tool_call', {calls:[{name:'mcp__moss__get_status', arguments:{}}]})), 'Calling moss…');
 assert.equal(tools.toolActivity(step('functions.tool_call', {calls:[{name:'mcp__github__get_me'}]})), 'Calling github…');
 assert.equal(tools.toolActivity(step('multi_tool_use.parallel', {tool_uses:[{recipient_name:'functions.mcp__moss__get_status'},{recipient_name:'functions.mcp__github__get_me'}]})), 'Calling moss, github…');
 assert.equal(tools.toolActivity(step('terminal', {command:'echo mcp__moss__get_status'})), 'Bash');
 assert.equal(tools.toolActivity(step('tool_call', {calls:[null, {}, {name:5}]})), 'Tool Call');
});
test('completed MCP calls aggregate by server and running labels remain truthful', () => {
 assert.equal(typeof tools.toolGroupSummary, 'function');
 assert.equal(tools.toolGroupSummary([step('mcp__moss__a', null, 'done'), step('mcp__moss__b', null, 'failed')]), 'Called moss 2 times');
 assert.equal(tools.toolGroupSummary([step('mcp__moss__a')]), 'Calling moss…');
 assert.equal(tools.toolGroupSummary([step('read_file', null, 'done'),step('read_file', null, 'done')]), 'Read 2 files');
 assert.equal(tools.toolGroupSummary([]), '');
});
test('tool output preview never cuts a Unicode character in half', () => {
 assert.equal(typeof tools.outputPreview, 'function');
 assert.equal(tools.outputPreview('hello\nworld\nthird\nfourth').text, 'hello\nworld\nthird');
 const p = tools.outputPreview('a'.repeat(3999)+'😀'+'rest');
 assert.equal(p.truncated, true);
 assert.ok(!/[\uD800-\uDBFF]$/.test(p.text));
 assert.deepEqual(tools.outputPreview(''), {text:'',truncated:false});
});
