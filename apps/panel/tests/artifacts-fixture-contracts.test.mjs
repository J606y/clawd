import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import {
  admitFixtureRun,
  artifactsJsonDecision,
  projectRunStatus,
  stripPanel,
} from './artifacts-browser.mjs';

const here = import.meta.dirname;
const artifactsSource = await readFile(path.join(here, 'artifacts-browser.mjs'), 'utf8');
const admissionSource = await readFile(path.join(here, 'react-admission-recovery-fixture.tsx'), 'utf8');

function noProfile(value, pathName = '$') {
  if (!value || typeof value !== 'object') return;
  for (const [key, item] of Object.entries(value)) {
    assert.notEqual(key, 'profileKey', pathName);
    if (key === 'profile') assert.notEqual(item, 'default', pathName);
    if (item && typeof item === 'object') noProfile(item, `${pathName}.${key}`);
  }
}

test('missing run and unknown panel routes fail closed', () => {
  const missing = artifactsJsonDecision('GET', '/panel-api/v1/runs/run_absent', new URLSearchParams(), { run: null, messages: [] });
  assert.equal(missing.status, 404);
  assert.equal(missing.body.error.code, 'run_not_found');
  assert.equal(missing.body.error.type, 'invalid_request_error');
  assert.equal(missing.body.error.param, null);
  assert.deepEqual(Object.keys(missing.body), ['error']);

  for (const pathname of ['/panel-api/api/settings', '/panel-sidecar/settings', '/panel-unknown/v1/runs']) {
    const unknown = artifactsJsonDecision('GET', pathname, new URLSearchParams(), { run: null, messages: [] });
    assert.equal(unknown.status, 404, pathname);
    assert.equal(unknown.body.error.code, 'route_not_found', pathname);
    assert.equal(JSON.stringify(unknown.body).includes('"data":[]'), false, pathname);
  }
  assert.equal(stripPanel('/panel-other/health').channel, 'unknown');
});

test('create associates the request session and status/stop/events use that pair', () => {
  const keys = new Map();
  const denied = admitFixtureRun({ input: 'x' }, 'k', 10, keys);
  assert.equal(denied.status, 400);
  assert.equal(denied.body.error.code, 'invalid_request');
  assert.equal(denied.run, undefined);

  const admitted = admitFixtureRun({ session_id: 'sess-a', input: 'hi' }, 'k1', 11, keys);
  assert.equal(admitted.status, 202);
  assert.equal(admitted.body.replayed, false);
  assert.match(admitted.run.run_id, /^run_[0-9a-f]{32}$/);
  assert.equal(admitted.run.session_id, 'sess-a');
  assert.equal(admitted.body.run_id, admitted.run.run_id);

  const replay = admitFixtureRun({ session_id: 'sess-a', input: 'hi' }, 'k1', 12, keys);
  assert.equal(replay.body.replayed, true);
  assert.equal(replay.run.run_id, admitted.run.run_id);
  const clash = admitFixtureRun({ session_id: 'sess-b', input: 'hi' }, 'k1', 13, keys);
  assert.equal(clash.status, 409);
  assert.equal(clash.body.error.code, 'idempotency_key_conflict');

  const state = { run: admitted.run, messages: [] };
  const id = admitted.run.run_id;
  const status = artifactsJsonDecision('GET', `/panel-api/v1/runs/${id}`, new URLSearchParams(), state);
  assert.equal(status.status, 200);
  assert.equal(status.body.run_id, id);
  assert.equal(status.body.session_id, 'sess-a');
  assert.equal(status.body.object, 'hermes.run');
  noProfile(status.body);

  const foreign = artifactsJsonDecision('GET', '/panel-api/v1/runs/run_' + 'a'.repeat(32), new URLSearchParams(), state);
  assert.equal(foreign.status, 404);
  assert.equal(foreign.body.error.code, 'run_not_found');

  const events = artifactsJsonDecision('GET', `/panel-api/v1/runs/${id}/events`, new URLSearchParams(), state);
  assert.equal(events.stream, true);
  assert.equal(events.run.session_id, 'sess-a');
  const missingEvents = artifactsJsonDecision('GET', '/panel-api/v1/runs/run_missing/events', new URLSearchParams(), state);
  assert.equal(missingEvents.status, 404);
  assert.equal(missingEvents.body.error.code, 'run_not_found');

  const stop = artifactsJsonDecision('POST', `/panel-api/v1/runs/${id}/stop`, new URLSearchParams(), state);
  assert.equal(stop.status, 200);
  assert.equal(stop.body.status, 'stopping');
  assert.equal(stop.body.run_id, id);
  assert.equal(stop.body.session_id, 'sess-a');
  assert.notEqual(stop.body.status, 'cancelled');

  admitted.run.status = 'completed';
  admitted.run.updated_at = 20;
  const terminal = artifactsJsonDecision('POST', `/panel-api/v1/runs/${id}/stop`, new URLSearchParams(), state);
  assert.equal(terminal.body.status, 'completed');
  assert.equal(terminal.body.run_id, id);
  assert.equal(terminal.body.session_id, 'sess-a');
  assert.deepEqual(projectRunStatus(admitted.run).session_id, 'sess-a');

  const other = artifactsJsonDecision('GET', '/panel-api/api/runtime/sessions/sess-b/run', new URLSearchParams(), state);
  assert.equal(other.body.run, null);
  assert.equal(other.body.busy, false);
  const same = artifactsJsonDecision('GET', '/panel-api/api/runtime/sessions/sess-a/run', new URLSearchParams(), state);
  assert.equal(same.body.run.run_id, id);
  assert.equal(same.body.run.session_id, 'sess-a');
});

test('known capability routes are modeled and do not invent a profile', () => {
  const state = { run: null, messages: [] };
  const sessions = artifactsJsonDecision('GET', '/panel-api/api/sessions', new URLSearchParams('limit=100'), state);
  assert.equal(sessions.status, 200);
  assert.equal(Array.isArray(sessions.body.data), true);
  assert.equal(typeof sessions.body.has_more, 'boolean');
  noProfile(sessions.body);

  const subagents = artifactsJsonDecision('GET', '/panel-api/api/runtime/subagents', new URLSearchParams('parent=artifacts-test&limit=50'), state);
  assert.equal(typeof subagents.body.total, 'number');
  assert.equal(subagents.body.profileKey, undefined);
  assert.ok(subagents.body.running <= subagents.body.total);

  const effort = artifactsJsonDecision('GET', '/panel-api/api/sessions/other-session/effort', new URLSearchParams(), state);
  assert.equal(effort.body.session_id, 'other-session');
  assert.equal(effort.body.source, 'unset');
  noProfile(effort.body);

  const wait = artifactsJsonDecision('GET', '/panel-sidecar/run-wait', new URLSearchParams('session=artifacts-test'), state);
  assert.equal(wait.body.state, 'unknown');
  assert.equal(wait.body.available, false);
  assert.equal(wait.body.data, undefined);

  const artifact = artifactsJsonDecision('GET', '/panel-sidecar/files/artifact', new URLSearchParams('path=/tmp/x'), state);
  assert.equal(artifact.proxy, true);
});

test('fixture sources do not restore empty success or identity-less terminals', () => {
  assert.doesNotMatch(artifactsSource, /state\.run\s*\?\?\s*\{\}/);
  assert.doesNotMatch(artifactsSource, /running:\s*0,\s*items:\s*\{\}/);
  assert.match(artifactsSource, /session_id:run\.session_id/);
  assert.match(artifactsSource, /code: 'run_not_found'|run_not_found/);

  assert.doesNotMatch(admissionSource, /return json\(\{\s*status:\s*"cancelled"\s*\}\)/);
  assert.doesNotMatch(admissionSource, /return json\(\{\s*status:\s*"running"\s*\}\)/);
  assert.doesNotMatch(admissionSource, /"c"\.repeat\(32\)/);
  assert.doesNotMatch(admissionSource, /profileKey:\s*"default"/);
  assert.doesNotMatch(admissionSource, /profile:\s*"default"/);
  assert.match(admissionSource, /code: "run_not_found"|run_not_found/);
  assert.match(admissionSource, /session_id: sessionId/);
  assert.match(admissionSource, /crypto\.getRandomValues/);
});
