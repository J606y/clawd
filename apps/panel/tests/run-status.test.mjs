import test from 'node:test';
import assert from 'node:assert/strict';
import {retryReason,retryLine,silentLine} from '../src/lib/run-status.ts';

test('retry labels preserve actual HTTP status without inventing account exhaustion',()=>{
 assert.equal(retryReason('HTTP 429: too many requests'),'Rate limited (429)');
 assert.equal(retryReason('RateLimitError'),'Rate limited');
 assert.equal(retryReason('HTTP 503: unavailable'),'Upstream error (503)');
 assert.equal(retryReason('request timeout'),'Request timed out');
 assert.equal(retryReason('quota exceeded'),'Usage limit reached');
 assert.equal(retryReason('unknown'),'Request failed');
});
test('retry line uses actual attempts and does not invent a deadline',()=>{
 assert.equal(retryLine({state:'retrying',summary:'HTTP 429',attempt:2,maxAttempts:3},12),'Rate limited (429) · Retrying in 12s · attempt 2/3');
 assert.equal(retryLine({state:'retrying',summary:'HTTP 429'},null),'Rate limited (429) · Retrying…');
 assert.equal(retryLine({state:'retrying',summary:'timeout',attempt:-1,maxAttempts:NaN},-10),'Request timed out · Awaiting retry…');
});
test('silent output never claims rate limit or failure',()=>{
 assert.equal(silentLine(1000),'');
 assert.match(silentLine(45000),/Waiting for model response/);
 assert.doesNotMatch(silentLine(45000),/429|failed|limit/i);
});
