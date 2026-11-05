// 把 dist/（→ web/）与 sidecar/ 发布到 origin 的 deploy 分支（只含产物）。HK 用只读部署密钥拉取该分支并切换版本。
// 用法：npm run release   （= 类型检查 + 构建 + 本脚本）
import { execFileSync } from "node:child_process";
import { existsSync, rmSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const git = (args, env = {}) =>
  execFileSync("git", args, { cwd: root, encoding: "utf8", env: { ...process.env, ...env } }).trim();

if (!existsSync(join(root, "dist", "index.html"))) {
  console.error("dist/index.html 不存在，先运行 npm run build");
  process.exit(1);
}
if (git(["status", "--porcelain"])) {
  console.error("工作区有未提交的改动：先提交，发布记录才能对应到源码版本");
  process.exit(1);
}

// 云端 main 上若有本地没有的提交（别的会话/机器推过来的），必须先合并再发布：发布的是"本地源码的构建产物"，
// 缺了对方的提交，这次部署就会把对方已经上线的功能从线上顶掉（2026-10-07 实际发生过：19:28 云端会话的部署顶掉了本地的
// 全部改动，20:20 本地的部署又顶掉了云端那个功能）。取不到 origin/main（断网等）只警告，不拦截。
let remoteMain = "";
try {
  git(["fetch", "--quiet", "origin", "main"]);
  remoteMain = git(["rev-parse", "FETCH_HEAD"]);
} catch {
  console.warn("警告：取不到 origin/main，跳过「云端是否有本地没有的提交」检查");
}
if (remoteMain) {
  let containsRemote = true;
  try {
    git(["merge-base", "--is-ancestor", remoteMain, "HEAD"]);
  } catch (e) {
    containsRemote = e?.status !== 1; // 1 = 不是祖先；其他错误码不是"有分叉"，不据此拦截
  }
  if (!containsRemote) {
    console.error(
      `origin/main（${remoteMain.slice(0, 7)}）有本地没有的提交：先 git merge origin/main，构建并验证后再发布。\n` +
        "否则这次部署会把对方已上线的改动覆盖掉。",
    );
    process.exit(1);
  }
}

const source = git(["rev-parse", "--short", "HEAD"]);
// 在 git worktree 里 .git 是指向主仓库的文件而不是目录：用 git 自己报告的目录放临时索引。
const index = join(resolve(root, git(["rev-parse", "--git-dir"])), "publish-index");
rmSync(index, { force: true });
try {
  const env = { GIT_INDEX_FILE: index };
  // 产物树：web/ = 静态站点，sidecar/ = 辅助服务（HK 部署脚本分别安装，sidecar 源码不进静态目录）。
  const webTree = (() => {
    git(["--work-tree=dist", "add", "-A", "--force", "."], env);
    const t = git(["write-tree"], env);
    rmSync(index, { force: true });
    return t;
  })();
  // --force 是为了绕过 .gitignore 收进构建产物；sidecar 里本地跑过 Python 留下的 __pycache__ 不是产物，必须排除。
  git(["--work-tree=sidecar", "add", "-A", "--force", ".", ":(exclude)__pycache__"], env);
  const sidecarTree = git(["write-tree"], env);
  rmSync(index, { force: true });
  const tree = execFileSync("git", ["mktree"], {
    cwd: root,
    encoding: "utf8",
    input: `040000 tree ${webTree}\tweb\n040000 tree ${sidecarTree}\tsidecar\n`,
  }).trim();

  let parent = "";
  try {
    git(["fetch", "--quiet", "origin", "deploy"]);
    parent = git(["rev-parse", "FETCH_HEAD"]);
  } catch {
    // 首次发布：deploy 分支尚不存在。
  }
  if (parent && git(["rev-parse", `${parent}^{tree}`]) === tree) {
    console.log(`产物与 deploy 分支一致，无需发布（${parent.slice(0, 7)}）`);
    process.exit(0);
  }
  const msg = `build from ${source}`;
  const commit = git(["commit-tree", tree, ...(parent ? ["-p", parent] : []), "-m", msg]);
  git(["push", "--quiet", "origin", `${commit}:refs/heads/deploy`]);
  console.log(`已发布 deploy@${commit.slice(0, 7)}（源码 ${source}）`);
  // deploy 分支只含构建产物；源码要靠 main 分支同步给别的会话/机器。
  if (remoteMain) {
    const ahead = Number(git(["rev-list", "--count", `${remoteMain}..HEAD`]));
    if (ahead > 0) console.warn(`提醒：本地 main 领先 origin/main ${ahead} 个提交，别忘了 git push origin main，否则别的会话看不到这些源码改动`);
  }
} finally {
  rmSync(index, { force: true });
}
