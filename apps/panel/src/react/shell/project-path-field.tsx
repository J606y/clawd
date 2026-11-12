/** 文件夹界面不再收集路径。保留导出，避免旧引用再画出附件根和模式按钮。 */
export function ProjectPathField(_props: {
  path: string;
  createDirectory: boolean;
  disabled: boolean;
  onPath: (path: string) => void;
  onCreateDirectory: (value: boolean) => void;
}) {
  return null;
}
