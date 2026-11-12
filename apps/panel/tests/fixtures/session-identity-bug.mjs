/** 主会话被未打标子代理接管时的页面样本。不是线上复现。 */

export const parentId = "api_parent";
export const childId = "child_subagent";
export const compressionId = "compression_tip";

export const parentPage = [
  { id: 1, session_id: parentId, role: "user", content: "主会话问题" },
  { id: 2, session_id: parentId, role: "assistant", content: "主会话回答" },
];

export const childOnlyPage = [
  { id: 9, session_id: childId, role: "user", content: "子代理任务" },
  { id: 10, session_id: childId, role: "assistant", content: "子代理结果" },
];

export const mixedPage = [
  { id: 2, session_id: parentId, role: "assistant", content: "主会话回答" },
  { id: 10, session_id: childId, role: "assistant", content: "子代理结果" },
];

export const compressionPage = [
  { id: 2, session_id: parentId, role: "assistant", content: "压缩前" },
  { id: 11, session_id: compressionId, role: "assistant", content: "压缩后续接" },
];

export const legacyPage = [
  { id: 3, role: "user", content: "没有会话 id 的旧行" },
];

export const childIds = new Set([childId]);

export const resumedIds = ["api_parent", childId];
export const listed = [
  { id: parentId, source: "api_server" },
  { id: childId, source: "subagent" },
];
