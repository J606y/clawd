import test from 'node:test';
import assert from 'node:assert/strict';
import { isIosDownloadHost, safeDownloadName, shouldShareDownload } from '../src/lib/save-file.ts';

test('an iPhone download stays on the page through the share sheet', () => {
  const iphone = { userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)', platform: 'iPhone', maxTouchPoints: 5 };
  const desktop = { userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0', platform: 'Linux', maxTouchPoints: 0 };
  assert.equal(isIosDownloadHost(iphone), true);
  assert.equal(isIosDownloadHost(desktop), false);
  assert.equal(shouldShareDownload(true, true), true);
  assert.equal(shouldShareDownload(true, false), false);
  assert.equal(shouldShareDownload(false, true), false);
});

test('download names keep the visible filename and drop path separators', () => {
  assert.equal(safeDownloadName('hermes-panel-repo-map.png'), 'hermes-panel-repo-map.png');
  assert.equal(safeDownloadName('原件（测试）].png'), '原件（测试）].png');
  assert.equal(safeDownloadName('/root/hermes-workspace/hermes-panel-repo-map.png'), '_root_hermes-workspace_hermes-panel-repo-map.png');
});
