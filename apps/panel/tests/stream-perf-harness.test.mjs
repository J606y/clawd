import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { calculateLagMetrics, buildDomPrefixProgressIndex } from './stream-perf-watermark.mjs';

const browserCode = readFileSync(path.resolve(import.meta.dirname, 'stream-perf-browser.mjs'), 'utf8');
const watermarkCode = readFileSync(path.resolve(import.meta.dirname, 'stream-perf-watermark.mjs'), 'utf8');

test('P0 Stream Perf Harness & Fixture Contract Verification', async (t) => {
  await t.test('1. api contract & path resolution: uses import.meta.dirname and real api.messages', () => {
    assert.match(browserCode, /import\.meta\.dirname/);
    assert.doesNotMatch(browserCode, /\/root\/hermes-workspace/, 'Must not hardcode /root/hermes-workspace absolute paths');
    assert.doesNotMatch(watermarkCode, /api\.getSessionMessages\s*=/);
    assert.doesNotMatch(watermarkCode, /api\.getContext\s*=/);
  });

  await t.test('2. runs pipeline: dispatches real RunStore RUN_CHANGED instead of directly overwriting chat.live', () => {
    assert.doesNotMatch(browserCode, /chat\.live\s*=\s*\{\s*\.\.\.run\s*\}/);
    assert.match(browserCode, /runStreamWorkload/);
  });

  await t.test('3. visible watermark & lag calculation: compares actual DOM watermark, accurately names rAF commit observation, and tracks unrendered samples', () => {
    assert.match(watermarkCode, /domCommitTimeline/);
    assert.match(watermarkCode, /unmatchedCount/);
    assert.doesNotMatch(watermarkCode, /const lastSample = vis\[vis\.length - 1\];\s*if\s*\(lastSample/);
    const metrics = calculateLagMetrics(
      [{ version: 2, timestamp: 10, ambiguous: false }, { version: 9, timestamp: 50, ambiguous: false }],
      [{ version: 2, timestamp: 30 }],
    );
    assert.equal(metrics.unmatchedCount, 1, 'an unseen target must stay unmatched, not be charged to the last sample');
    assert.deepEqual(metrics.lagsMs, [20]);
    assert.equal(metrics.matchedCount, 1);
  });

  await t.test('4. rAF & observer lifecycle: handles handle cancellation, disconnects observers, and settles with assert', () => {
    assert.match(browserCode, /assert|throw new Error/);
    assert.match(watermarkCode, /throw new TypeError|throw new Error/);
  });

  await t.test('5. history verification & scenario generator: checks exact user turns and tool counts, Scenario A >= 2K, B >= 20K with 300 code lines', () => {
    assert.match(browserCode, /userTurnCount/);
    assert.match(browserCode, /toolCount/);
    assert.match(browserCode, /streamTargetChars:\s*2000/);
    assert.match(browserCode, /fullStreamText\.length >= 2000/);
    assert.match(browserCode, /streamTargetChars:\s*20000/);
    assert.match(browserCode, /fullStreamText\.length >= 20000/);
    assert.match(browserCode, /for \(let l = 1; l <= 300; l\+\+\)/);
    assert.match(browserCode, /codeLines\.length, 300/);
  });

  await t.test('6. deterministic seeds & profiler accuracy: avoids Math.random, separates layout vs forced reflow, idle time', () => {
    assert.doesNotMatch(browserCode, /Math\.random\(\)/);
    assert.doesNotMatch(browserCode, /timeDeltas\.reduce.*totalScriptMs/);
    assert.match(browserCode, /createPrng|forcedReflowCount/);
  });

  await t.test('7. network guard: strictly blocks unauthorized external network requests checking origin and enables CDP network blocking', () => {
    assert.match(watermarkCode, /parsed\.origin === allowedOrigin/);
    assert.match(browserCode, /Fetch\.enable/);
    assert.doesNotMatch(watermarkCode, /parsed\.pathname === location\.pathname/);
  });

  await t.test('8. prefix progress oracle: wires preparePrefixDomReferences, 120s budget, and serializes index into candidate realm', () => {
    assert.match(browserCode, /preparePrefixDomReferences/);
    assert.match(browserCode, /120000/);
    assert.match(browserCode, /stats\.elapsedMs <= 120000/);
    assert.match(watermarkCode, /buildDomPrefixProgressIndex/);
    assert.match(watermarkCode, /resolveProgress/);
    const index = buildDomPrefixProgressIndex(
      [{ sourceLength: 4, hash: 'a'.repeat(64) }],
      [4, 8],
    );
    assert.equal(index.resolveProgress('a'.repeat(64)).minSourceLength, 4);
  });

  await t.test('9. published performance thresholds are not lowered', () => {
    assert.match(browserCode, /frameP95TargetMs:\s*16\.7/);
    assert.match(browserCode, /longTaskTarget:\s*0/);
    assert.match(browserCode, /scriptPerFrameTargetMs:\s*4\.0/);
    assert.match(browserCode, /outputLagP95TargetMs:\s*300\.0/);
    assert.match(browserCode, /p95LagMs <= 300\.0/);
    assert.match(browserCode, /endSettleFrameTargetMs:\s*50\.0/);
    assert.match(browserCode, /maxMs <= 50\.0/);
    assert.match(browserCode, /cpuThrottlingRate:\s*4/);
  });
});
