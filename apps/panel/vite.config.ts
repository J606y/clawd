import { appendFileSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { gzipSync } from "node:zlib";
import path from "node:path";
import { fileURLToPath, URL } from "node:url";
import babel from "@rolldown/plugin-babel";
import tailwindcss from "@tailwindcss/vite";
import react, { reactCompilerPreset } from "@vitejs/plugin-react";
import { defineConfig, loadEnv, type PluginOption } from "vite";

// React 19 不需要 react-compiler-runtime。compilationMode=infer 只编译组件和 hook，
// panicThreshold=none 让不合规函数跳过而不是让构建失败。REACT_COMPILER=0 只关掉编译器，保留 React 插件。
function reactCompilerPlugin(): PluginOption[] {
  const plugins: PluginOption[] = [react()];
  if (process.env.REACT_COMPILER === "0") return plugins;
  const logPath = process.env.REACT_COMPILER_LOG;
  if (logPath) mkdirSync(path.dirname(logPath), { recursive: true });
  const preset = reactCompilerPreset({
    compilationMode: "infer",
    panicThreshold: "none",
    target: "19",
    sources: (filename) => filename.includes(`${path.sep}src${path.sep}react${path.sep}`),
    ...(logPath
      ? {
          logger: {
            logEvent(filename: string | null, event: { kind: string; fnName?: string | null; reason?: string; detail?: { reason?: string; description?: string; category?: string }; fnLoc?: { start?: { line?: number } } | null }) {
              const detail = event.detail;
              const reason = typeof event.reason === "string"
                ? event.reason
                : detail && typeof detail.reason === "string"
                  ? detail.reason
                  : null;
              appendFileSync(logPath, `${JSON.stringify({
                filename,
                kind: event.kind,
                fnName: event.fnName ?? null,
                reason,
                description: detail && typeof detail.description === "string" ? detail.description.slice(0, 400) : null,
                category: detail?.category ?? null,
                line: event.fnLoc?.start?.line ?? null,
              })}\n`);
            },
          },
        }
      : {}),
  });
  plugins.push(babel({ presets: [preset] }));
  return plugins;
}


// 构建产物预压缩：给 .js/.css/.html 等生成 .gz 兄弟文件。面板服务用
// aiohttp FileResponse，客户端接受 gzip 时会自动改发 .gz，首屏在慢速
// 网络下少传约 70%。小于 1KB 的文件不压（.gz 反而更大）。
function gzipAssetsPlugin(): PluginOption {
  return {
    name: "clawd-gzip-assets",
    closeBundle() {
      const outDir = path.resolve("dist");
      const exts = new Set([".js", ".css", ".html", ".json", ".svg", ".webmanifest"]);
      const walk = (dir: string): string[] => {
        const out: string[] = [];
        for (const entry of readdirSync(dir, { withFileTypes: true })) {
          const p = path.join(dir, entry.name);
          if (entry.isDirectory()) out.push(...walk(p));
          else if (entry.isFile()) out.push(p);
        }
        return out;
      };
      for (const file of walk(outDir)) {
        if (!exts.has(path.extname(file)) || file.endsWith(".gz")) continue;
        const data = readFileSync(file);
        if (data.length < 1024) continue;
        writeFileSync(file + ".gz", gzipSync(data, { level: 9 }));
      }
    },
  };
}

// base "./"：同一份产物既能挂在开发路径 /__next/，也能挂在站点根 /。
// 本地 dev 的三类同源接口共用上游；可用 PANEL_DEV_UPSTREAM 指向隔离测试服务。
// 默认入口是 React。JSX 仍是 automatic runtime；编译器经 @vitejs/plugin-react 的 reactCompilerPreset 接入。
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "PANEL_DEV_");
  // 未设置时不要落到生产。127.0.0.1:9 无服务，请求在本机失败。
  // 不在加载配置时抛错：现有测试会 createServer 真实配置，但页面请求不走这条代理。
  const target = env.PANEL_DEV_UPSTREAM || "http://127.0.0.1:9";
  return {
    base: "./",
    plugins: [tailwindcss(), ...reactCompilerPlugin(), gzipAssetsPlugin()],
    resolve: {
      alias: {
        "@": fileURLToPath(new URL("./src/react", import.meta.url)),
        "react-router": fileURLToPath(new URL("./node_modules/react-router/dist/production/index.mjs", import.meta.url)),
      },
    },
    server: {
      proxy: Object.fromEntries(
        ["/panel-api", "/panel-core", "/panel-auth"].map((prefix) => [prefix, {
          target,
          changeOrigin: true,
        }]),
      ),
    },
    build: {
      target: "es2022",
      outDir: "dist",
      emptyOutDir: true,
      assetsInlineLimit: 0,
      sourcemap: false,
      rolldownOptions: {
        onwarn(warning, warn) {
          if (warning.code === "MODULE_LEVEL_DIRECTIVE") return;
          warn(warning);
        },
        output: {
          manualChunks(id: string) {
            if (!id.includes("node_modules")) return undefined;
            if (id.includes("react-router")) return "router";
            if (id.includes("@tanstack/react-query") || id.includes("@tanstack/query-core")) return "query";
            if (id.includes("/motion/") || id.includes("framer-motion")) return "motion";
            if (id.includes("highlight.js")) return "highlight";
            if (id.includes("/marked/") || id.includes("node_modules/marked")) return "marked";
            if (
              id.includes("/react-dom/") ||
              id.includes("/react/") ||
              id.includes("/scheduler/")
            ) return "react";
            return undefined;
          },
        },
      },
    },
  };
});
