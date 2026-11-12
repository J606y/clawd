// vault.* 事件解析与答案编码（面板保险库提示卡）。
// 两套 lib 都要覆盖：sidecar 的 /src/lib 与面板的 /src/react/lib（与
// approval-contract.test.mjs 同一套双根加载方式）。Node 20 不能直接 import
// .ts，所以用 vite ssrLoadModule 加载。只用哑值（pw-TEST）；秘密绝不出现在请求事件里。
import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "vite";
import { mkdtemp, rm } from "node:fs/promises";
import { join } from "node:path";

const scratch = process.env.TMPDIR || "/root/.hermes/cache/scratch";
const cacheDir = await mkdtemp(join(scratch, "vault-contract-"));
const server = await createServer({
  configFile: false,
  root: new URL("..", import.meta.url).pathname,
  cacheDir,
  server: { middlewareMode: true, hmr: false, watch: null },
  appType: "custom",
});

const base = {
  prompt_id: "vault-abc123",
  execution_generation: 7,
  timeout_s: 180,
};

for (const root of ["/src/lib", "/src/react/lib"]) {
  const { parseVaultEvent, encodeVaultAnswer, vaultMethodLabel } = await server.ssrLoadModule(
    `${root}/vault.ts`,
  );

  test(`${root}：parseVaultEvent 接受 save_login 事件，只保留站点信息`, () => {
    const prompt = parseVaultEvent({
      ...base,
      method: "vault.save_login",
      params: { origin: "https://login.example.com", site: "login.example.com", password: "pw-TEST" },
    });
    assert.ok(prompt);
    assert.equal(prompt.method, "vault.save_login");
    assert.equal(prompt.origin, "https://login.example.com");
    assert.equal(prompt.site, "login.example.com");
    assert.equal(prompt.timeoutSeconds, 180);
    assert.equal(prompt.pending, false);
  });

  test(`${root}：parseVaultEvent 拒绝未知方法与缺失 requestId`, () => {
    assert.equal(parseVaultEvent({ ...base, method: "vault.nope", params: {} }), null);
    assert.equal(parseVaultEvent({ ...base, prompt_id: "", method: "vault.code", params: {} }), null);
  });

  test(`${root}：parseVaultEvent 超时默认按方法取（unlock 120 秒）`, () => {
    const prompt = parseVaultEvent({
      prompt_id: "vault-x",
      execution_generation: 1,
      method: "vault.unlock_prompt",
      params: { backend: "bitwarden", display_name: "Bitwarden" },
    });
    assert.ok(prompt);
    assert.equal(prompt.timeoutSeconds, 120);
    assert.equal(prompt.backend, "bitwarden");
    assert.equal(prompt.displayName, "Bitwarden");
  });

  test(`${root}：encodeVaultAnswer：save_login 编成 JSON 字符串，code / unlock 用原文`, () => {
    const save = parseVaultEvent({ ...base, method: "vault.save_login", params: {} });
    const code = parseVaultEvent({ ...base, method: "vault.code", params: {} });
    const unlock = parseVaultEvent({ ...base, method: "vault.unlock_prompt", params: {} });
    assert.ok(save && code && unlock);
    assert.equal(
      encodeVaultAnswer(save, { identifier: "user@example.com", password: "pw-TEST" }),
      JSON.stringify({ identifier: "user@example.com", password: "pw-TEST" }),
    );
    assert.equal(encodeVaultAnswer(save, { identifier: "", password: "pw-TEST" }), "");
    assert.equal(encodeVaultAnswer(code, { code: "123456" }), "123456");
    assert.equal(encodeVaultAnswer(unlock, { password: "pw-TEST" }), "pw-TEST");
  });

  test(`${root}：vaultMethodLabel 有中文标题`, () => {
    assert.equal(vaultMethodLabel("vault.save_login"), "保存登录信息");
    assert.equal(vaultMethodLabel("vault.code"), "输入一次性验证码");
    assert.equal(vaultMethodLabel("vault.unlock_prompt"), "解锁密码管理器");
  });
}

test.after(async () => {
  await server.close();
  await rm(cacheDir, { recursive: true, force: true });
});
