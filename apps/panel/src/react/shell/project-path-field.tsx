import { useEffect, useState } from "react";
import { sidecar, type DirListing, type FileRoot } from "@/lib/sidecar.js";

export function ProjectPathField(props: {
  path: string;
  createDirectory: boolean;
  disabled: boolean;
  onPath: (path: string) => void;
  onCreateDirectory: (value: boolean) => void;
}) {
  const [roots, setRoots] = useState<FileRoot[]>([]);
  const [rootsError, setRootsError] = useState("");
  const [browse, setBrowse] = useState<DirListing | null>(null);
  const [browseError, setBrowseError] = useState("");

  useEffect(() => {
    let cancelled = false;
    void sidecar.fileRoots().then((data) => {
      if (cancelled) return;
      setRoots(data.filter((item) => typeof item.path === "string" && item.path.startsWith("/")));
    }).catch(() => {
      if (!cancelled) setRootsError("目录列表不可用，请填写绝对路径。不会因此创建目录。");
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const chooseExisting = (path: string) => {
    props.onPath(path);
    props.onCreateDirectory(false);
  };

  const openRoot = (root: FileRoot) => {
    chooseExisting(root.path);
    setBrowseError("");
    void sidecar.listDir(root.id, "").then(setBrowse).catch(() => {
      setBrowse(null);
      setBrowseError("无法列出子目录，仍可使用已填绝对路径。");
    });
  };

  const openChild = (name: string) => {
    if (!browse) return;
    const abs = browse.absPath.endsWith("/") ? `${browse.absPath}${name}` : `${browse.absPath}/${name}`;
    chooseExisting(abs);
    const next = browse.path ? `${browse.path}/${name}` : name;
    void sidecar.listDir(browse.root, next).then(setBrowse).catch(() => {
      setBrowseError("无法继续列出子目录，仍可使用已填绝对路径。");
    });
  };

  return (
    <>
      <input
        aria-label="项目目录"
        value={props.path}
        disabled={props.disabled}
        placeholder="绝对目录，例如 /root/work"
        onChange={(event) => props.onPath(event.target.value)}
      />
      <button type="button" aria-pressed={!props.createDirectory} disabled={props.disabled} onClick={() => props.onCreateDirectory(false)}>
        使用已有目录
      </button>
      <button type="button" aria-pressed={props.createDirectory} disabled={props.disabled} onClick={() => props.onCreateDirectory(true)}>
        创建此目录
      </button>
      {roots.map((root) => (
        <button key={root.id} type="button" disabled={props.disabled} onClick={() => openRoot(root)}>
          选择：{root.label}
        </button>
      ))}
      {browse?.entries.filter((entry) => entry.dir).map((entry) => (
        <button key={entry.name} type="button" disabled={props.disabled} onClick={() => openChild(entry.name)}>
          选择：{browse.absPath.endsWith("/") ? `${browse.absPath}${entry.name}` : `${browse.absPath}/${entry.name}`}
        </button>
      ))}
      {rootsError ? <span role="status">{rootsError}</span> : null}
      {browseError ? <span role="status">{browseError}</span> : null}
    </>
  );
}
