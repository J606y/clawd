import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "vite";

const server = await createServer({ server: { middlewareMode: true }, appType: "custom" });
try {
  const { composerFieldHeight } = await server.ssrLoadModule("/src/react/lib/composer-size.ts");
  await test("one-line composer height does not add a blank band under the text", () => {
    assert.equal(composerFieldHeight(36, 12), 36);
    assert.equal(composerFieldHeight(60, 12), 60);
    assert.equal(composerFieldHeight(61, 12), 84);
    assert.equal(composerFieldHeight(400, 12), 240);
  });
} finally {
  await server.close();
}
