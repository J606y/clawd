import test from 'node:test';
import assert from 'node:assert/strict';
test('official 2.1.309 vocabulary preserves all exact English words', async () => {
 const s=await import('../src/lib/spinner-verbs.ts');
 assert.equal(s.SPINNER_VERBS.length,187);
 assert.equal(new Set(s.SPINNER_VERBS).size,187);
 for(const word of ['Kerfuffling','Onioning','Polishing','Transmogrifying','Flambéing','Sautéing',"Beboppin'"])assert.ok(s.SPINNER_VERBS.includes(word),word);
 assert.ok(!s.SPINNER_VERBS.includes('Canoodling'));
 assert.equal(s.spinnerVerb(()=>0),'Accomplishing');
 assert.equal(s.spinnerVerb(()=>0.999999),'Zigzagging');
 assert.equal(s.spinnerVerb(()=>NaN),'Thinking');
 assert.equal(s.formatElapsed(65000),'1m 5s');
 assert.equal(s.formatElapsed(-100),'0s');
 assert.equal(s.formatElapsed(NaN),'0s');
 assert.equal(s.completionVerb('fixture'),s.completionVerb('fixture'));
 assert.ok(s.COMPLETION_VERBS.includes(s.completionVerb('fixture')));
});
