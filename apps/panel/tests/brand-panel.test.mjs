import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

function read(rel) {
  return readFileSync(new URL(`../${rel}`, import.meta.url), 'utf8');
}

test('document title and PWA name are clawd without changing install routes', () => {
  const html = read('index.html');
  const reactHtml = read('src/react/index.html');
  const manifest = JSON.parse(read('public/manifest.webmanifest'));

  assert.match(html, /<title>clawd<\/title>/);
  assert.match(html, /name="apple-mobile-web-app-title" content="clawd"/);
  assert.match(html, /localStorage\.getItem\("hp\.theme"\)/);
  assert.doesNotMatch(html, /Hermes|hermes-panel/);
  assert.match(reactHtml, /<title>clawd<\/title>/);
  assert.equal(manifest.name, 'clawd');
  assert.equal(manifest.short_name, 'clawd');
  assert.equal(manifest.start_url, './');
  assert.equal(manifest.scope, './');
  assert.equal(Object.hasOwn(manifest, 'id'), false);
});

test('shell product labels say clawd', () => {
  const shell = read('src/react/shell/shell.tsx');
  assert.match(shell, /return "clawd";/);
  assert.match(shell, />\s*clawd\s*<span className="spacer"/);
  assert.equal(shell.includes('`clawd ${version}`'), true);
  assert.match(shell, /title="实时画面：查看 clawd 正在操作的浏览器"/);
  assert.doesNotMatch(shell, /Hermes|hermes-panel/);
});

test('settings product copy says clawd and keeps the real update command', () => {
  const jobs = read('src/react/pages/settings/jobs-tab.tsx');
  const update = read('src/react/pages/settings/update-tab.tsx');
  const config = read('src/react/pages/settings/config-tab.tsx');
  const usage = read('src/react/pages/settings/usage-tab.tsx');
  const plugins = read('src/react/pages/settings/plugins-tab.tsx');
  const mcp = read('src/react/pages/settings/mcp-tab.tsx');

  assert.match(jobs, /发给 clawd 的指令/);
  assert.match(update, /info\.version \|\| "clawd"/);
  assert.match(update, /没有安全的进程内更新接口，不能从这里立即更新/);
  assert.match(update, /此页不能执行更新/);
  assert.doesNotMatch(update, /hermes update|clawd update/);
  assert.match(config, /由 clawd 校验后写入；部分设置需在「状态」页重启 clawd 网关后生效/);
  assert.match(usage, /输入口径以 clawd 记录为准/);
  assert.match(plugins, /执行 clawd plugins/);
  assert.match(mcp, /clawd mcp add/);
  for (const source of [jobs, update, config, usage, plugins, mcp]) {
    assert.doesNotMatch(source, /Hermes|hermes-panel/);
  }
});
