/**
 * 面板仓库没有模型系统提示词，所以使用规则只放在这里。
 * 这个文件不会注册工具，也不会写进模型请求。模型不会因为这个文件就调用 show_widget。
 */
export const WIDGET_USAGE_RULES = "该画图时再画；图里不要放大段文字，解释写在正文；不要用 position:fixed；颜色用 CSS 变量。";
