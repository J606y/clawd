import test from 'node:test';
import assert from 'node:assert/strict';
import { splitSteerTimeline } from '../src/lib/steer-timeline.ts';

const steer = (id, step, offset) => ({ id, text: id, images: [], files: [], uploads: [], status: 'sent', anchor: { step, offset } });
const note = text => ({ kind: 'note', text });
const tool = id => ({ kind: 'tool', id, name: 'terminal', text: id, status: 'running' });
const project = (steps, text, steers) => splitSteerTimeline({ steps, text, steers });
const visible = parts => parts.map(p => p.kind === 'steer' ? `USER:${p.steer.id}` : p.steps.map(s => s.kind === 'note' ? s.text : `TOOL:${s.id}`).join('') + p.text).filter(Boolean);

test('steering stays between tool groups, never above the preceding process', () => {
  assert.deepEqual(visible(project([note('before'), tool('one'), note('after'), tool('two')], 'tail', [steer('insert', 2, 0)])), ['beforeTOOL:one', 'USER:insert', 'afterTOOL:two' + 'tail']);
});

test('multiple steering boundaries split a growing reply without duplicating or losing text', () => {
  const steers = [steer('one', 0, 3), steer('two', 0, 6)];
  assert.deepEqual(visible(project([], 'abcdefghi', steers)), ['abc', 'USER:one', 'def', 'USER:two', 'ghi']);
  assert.deepEqual(visible(project([note('abcdefghi'), tool('next')], 'final', steers)), ['abc', 'USER:one', 'def', 'USER:two', 'ghiTOOL:nextfinal']);
});

test('consecutive, sending and failed steering keep their original boundary and order', () => {
  const one = { ...steer('one', 0, 0), status: 'sending' };
  const two = { ...steer('two', 0, 0), status: 'failed', error: 'rejected' };
  const input = [one, two];
  const parts = project([], 'later', input);
  assert.deepEqual(visible(parts), ['USER:one', 'USER:two', 'later']);
  assert.equal(parts[1].steer, one);
  assert.equal(parts[3].steer, two);
  assert.equal(input[1].status, 'failed');
});

test('note images and tool object identity survive splitting; source data is never mutated', () => {
  const original = { ...note('abcdef'), images: ['https://example.test/p.png'] };
  const call = tool('one');
  const parts = project([original, call], '', [steer('insert', 0, 3)]);
  assert.deepEqual(visible(parts), ['abc', 'USER:insert', 'defTOOL:one']);
  assert.equal(parts.flatMap(p => p.kind === 'assistant' ? p.steps : []).filter(s => s.images?.length).length, 1);
  assert.equal(parts.at(-1).steps.at(-1), call);
  assert.deepEqual(original, { ...note('abcdef'), images: ['https://example.test/p.png'] });
});

test('unanchored legacy steering falls after known process rather than above it', () => {
  const legacy = { ...steer('legacy', 0, 0), anchor: undefined };
  assert.deepEqual(visible(project([tool('one')], 'before', [legacy])), ['TOOL:onebefore', 'USER:legacy']);
});

test('no steering preserves existing stream and an empty tail always exists for task controls', () => {
  const steps = [note('before'), tool('one')];
  assert.deepEqual(project(steps, 'reply', []), [{ kind: 'assistant', steps, text: 'reply' }]);
  assert.equal(project([], '', [steer('one', 0, 0)]).at(-1).kind, 'assistant');
});
