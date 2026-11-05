import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "vite";

const GATE = "派发闸门：你是父代理。本回合若有独立、可并行、或会把大段日志、多文件读进父上下文的子任务，先调用 delegate_task，不要自己串行做完。单步查询、要跟用户来回、机械循环用现有工具或 execute_code，不派。用户明确说亲自做，不派。子代理自报完成不是验收。";
const server = await createServer({ server: { middlewareMode: true }, appType: "custom" });
try {
  for (const modulePath of ["/src/lib/attachments.ts", "/src/react/lib/attachments.ts"]) {
    const { parseUserText } = await server.ssrLoadModule(modulePath);
    await test(`displayed user text omits the delegation gate (${modulePath})`, () => {
      const hinted = `这个配置档是什么东西\n\n[Image attached at: /root/hermes-uploads/2026-10-29/IMG_2465.jpeg]\n${GATE}`;
      assert.equal(parseUserText(hinted).text, "这个配置档是什么东西");
      assert.equal(parseUserText(`这个配置档是什么东西\n\n${GATE}`).text, "这个配置档是什么东西");
      assert.equal(parseUserText(GATE).text, "");
      assert.equal(parseUserText("派发闸门只是话题").text, "派发闸门只是话题");
    });
  }
} finally {
  await server.close();
}
