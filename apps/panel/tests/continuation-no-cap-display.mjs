// 静态核对续跑展示：不导入产品模块，不启动页面。准备好的断言不是通过。
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

function read(rel) {
  return readFileSync(new URL(`../${rel}`, import.meta.url), 'utf8');
}

const lib = read('src/lib/transcript.ts');
const reactLib = read('src/react/lib/transcript.ts');
const page = read('src/react/pages/chat/chat-page.tsx');

function sliceFn(source, name) {
  const start = source.indexOf(`function ${name}`);
  assert.notEqual(start, -1, `${name} missing`);
  const next = source.indexOf('\nfunction ', start + 1);
  const exportNext = source.indexOf('\nexport ', start + 1);
  const end = [next, exportNext].filter((at) => at > start).sort((a, b) => a - b)[0] ?? source.length;
  return source.slice(start, end);
}

test('两份 transcript 字节相同，并消费核心续跑语义', () => {
  assert.equal(lib, reactLib);
  assert.match(lib, /export const CLAWD_CONTINUATION_KIND = "clawd_continuation"/);
  assert.match(lib, /export const CLAWD_CONTINUATION_MARK = "\[clawd自动续跑\]"/);
  assert.match(lib, /export const AUTO_CONTINUE_MARK = "\[面板自动续跑\]"/);
  const recognize = sliceFn(lib, 'isAutoContinue');
  assert.match(recognize, /m\.display_kind === CLAWD_CONTINUATION_KIND/);
  assert.match(recognize, /m\.display_kind && !isLegacyInput\(m\)\) return false/);
  assert.match(recognize, /text\.startsWith\(CLAWD_CONTINUATION_MARK\) \|\| text\.startsWith\(AUTO_CONTINUE_MARK\)/);
  assert.match(lib, /title: "子任务已完成，自动继续"/);
  assert.match(lib, /title: failed \? "后台子任务失败"/);
});

test('成功次数不再是展示硬停，也不把失败预算当成功门槛', () => {
  const state = sliceFn(lib, 'continuationState');
  assert.match(state, /return \{ trigger, limited: false \}/);
  assert.doesNotMatch(lib, /AUTO_CONTINUE_LIMIT|streak|MAX_ATTEMPTS|MAX_STREAK|streak_limit/);
  assert.doesNotMatch(state, />=\s*5|>=\s*6|>=\s*8/);
  assert.match(lib, /成功次数不是门槛/);
  assert.match(lib, /不发起回合，也不读失败次数/);
});

test('聊天页去掉连续五次文案，保留失败取消审批与项目首发', () => {
  assert.doesNotMatch(page, /已连续自动继续|发一句话即可让 clawd 继续|AUTO_CONTINUE_LIMIT|continuationState/);
  assert.doesNotMatch(page, /continuation_trigger|sidecar\.main|setInterval\(\s*\(\)\s*=>\s*void continue/);
  assert.match(page, /setNotice\("已停止"\)/);
  assert.match(page, /setNotice\(run\.error \|\| "执行失败"\)/);
  assert.match(page, /停止失败：/);
  assert.match(page, /等待停止完成并加载历史后再编辑/);
  assert.match(page, /任务在另一端执行，等待实时连接/);
  assert.match(page, /waiting_for_approval/);
  assert.match(page, /ClarifySheet/);
  assert.match(page, /onApprove=\{\(choice: ApprovalChoice\)/);
  assert.match(page, /respondApproval/);
  assert.match(page, /prepareCreatedSession/);
  assert.match(page, /getPendingNewChatProjectId/);
  assert.match(page, /todoId/);
  assert.match(page, /profileKey === "unknown"/);
  assert.match(page, /effortNote/);
  assert.match(page, /notice === "已停止"/);
});
