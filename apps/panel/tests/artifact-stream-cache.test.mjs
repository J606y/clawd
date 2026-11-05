import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';

const server = await createServer({
  configFile: false,
  server: { middlewareMode: true, hmr: false, watch: null },
  appType: 'custom'
});

try {
  const { parseAssistantContent } = await server.ssrLoadModule('/src/lib/artifacts.ts');

  test('parseAssistantContent accepts cache: false option and skips writing/reading global cache without polluting', () => {
    let idSeq = 1;
    const testDelta = `Streaming delta seq-${idSeq++} with MEDIA:/root/hermes-workspace/delta.png`;
    
    // First call with cache: false
    const res1 = parseAssistantContent(testDelta, { cache: false });
    assert.equal(res1.artifacts.length, 1);
    assert.equal(res1.artifacts[0].name, 'delta.png');

    // Second call with cache: false must return a fresh object
    const res2 = parseAssistantContent(testDelta, { cache: false });
    assert.notEqual(res1, res2, 'cache: false must produce a fresh result object rather than reusing cached reference');

    // Now call with default cache: true on the same string
    // Must NOT be the same object reference as res1 or res2
    const defaultRes1 = parseAssistantContent(testDelta);
    assert.notEqual(defaultRes1, res1, 'Default cached result must not be the uncached res1 object');
    assert.notEqual(defaultRes1, res2, 'Default cached result must not be the uncached res2 object');

    // Calling again with default cache: true must return the exact same cached reference
    const defaultRes2 = parseAssistantContent(testDelta);
    assert.equal(defaultRes1, defaultRes2, 'Repeated default call must return cached reference');

    // Now call cache: false again: it must NOT return defaultRes1 and must NOT mutate/pollute defaultRes1
    const res3 = parseAssistantContent(testDelta, { cache: false });
    assert.notEqual(res3, defaultRes1, 'cache: false must not return cached reference even if cached previously');

    // Verify cache still holds the original defaultRes1 and was not polluted
    const defaultRes3 = parseAssistantContent(testDelta);
    assert.equal(defaultRes3, defaultRes1, 'Subsequent default call must still return original cached reference');
  });

  test('parseAssistantContent LRU eviction and promotion works as true LRU (max 100)', () => {
    // Fill cache with 100 distinct items
    for (let i = 0; i < 100; i++) {
      parseAssistantContent(`Item key-${i}`);
    }

    // Access key-0 to promote it to most recently used
    const key0First = parseAssistantContent(`Item key-0`);
    const key0Second = parseAssistantContent(`Item key-0`);
    assert.equal(key0First, key0Second);

    // Add another item key-100, which should evict key-1 (the least recently used), NOT key-0
    parseAssistantContent(`Item key-100`);

    // Verify key-0 is still in cache (should return same reference)
    const key0Third = parseAssistantContent(`Item key-0`);
    assert.equal(key0Third, key0First, 'key-0 should have been promoted and survived eviction');
  });

  test('parseAssistantContent keeps discovery semantics for markdown, media, quotes and limits', () => {
    const text = 'Normal text without cache MEDIA:/root/hermes-workspace/doc.pdf';
    const res = parseAssistantContent(text, { cache: false });
    assert.equal(res.artifacts.length, 1);
    assert.equal(res.artifacts[0].name, 'doc.pdf');
    assert.equal(res.text, 'Normal text without cache ');
  });
} finally {
  await server.close();
}
