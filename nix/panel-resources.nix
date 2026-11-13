# Offline allowlist build of the panel resources the Python wheel stage copies.
#
# apps/panel is not a root npm workspace and has its own package-lock.json.
# Dependencies come only from that lockfile via importNpmLock (integrity
# hashes already recorded there). There is no second hash, no registry
# fallback, and no read of the gitignored clawd_server/bundled tree.
#
# $out is a panel root: dist/, plugins/browser-window-guard/.
# node_modules is a build input only. It is not installed into $out.
# LICENSE is not emitted here; the Python stage copies the repo LICENSE.
# This derivation is not spliced into pythonSrc (that would be
# import-from-derivation). python.nix passes $out as
# CLAWD_PANEL_RESOURCE_ROOT during the wheel build only. Not evaluated
# or built by this change.
{
  lib,
  buildNpmPackage,
  importNpmLock,
  nodejs,
  npmConfigHook,
}:
let
  panelRoot = ../apps/panel;
  panelJson = builtins.fromJSON (builtins.readFile (panelRoot + "/package.json"));
  # Host node_modules and any prebuilt dist are not inputs. The sandbox must
  # build dist from the lockfile, not from an ignored directory.
  panelSrc = lib.cleanSourceWith {
    src = lib.cleanSource panelRoot;
    name = "clawd-panel-source";
    filter =
      path: _type:
      let
        rel = lib.removePrefix (toString panelRoot + "/") (toString path);
        components = lib.splitString "/" rel;
        banned = [
          "node_modules"
          "dist"
          "release"
          ".git"
          "tests"
          "__pycache__"
        ];
      in
      rel == "" || !(lib.any (part: builtins.elem part banned) components);
  };
in
buildNpmPackage {
  pname = "clawd-panel-resources";
  version = panelJson.version or "0.0.0";
  src = panelSrc;
  inherit nodejs npmConfigHook;

  # Same helper the root workspace uses. Hashes come from package-lock.json.
  npmDeps = importNpmLock.importNpmLock {
    npmRoot = panelSrc;
  };

  # The hook already installs offline and ignores lifecycle scripts, then
  # rebuilds. Do not add a network retry. npmFlags is not set: flags apply to
  # every npm command, and ignore-scripts on the build script would skip it.
  npm_config_offline = "true";
  npm_config_fund = "false";
  npm_config_audit = "false";
  ELECTRON_SKIP_BINARY_DOWNLOAD = "1";

  buildPhase = ''
    runHook preBuild
    if [ ! -d node_modules ]; then
      echo "clawd panel resources: offline dependency tree missing; not fetching" >&2
      exit 1
    fi
    if [ -e dist ]; then
      echo "clawd panel resources: refusing a pre-existing dist" >&2
      exit 1
    fi
    npm run build
    runHook postBuild
  '';

  # Replace buildNpmPackage's npm pack install, which would publish
  # node_modules. Only the known panel resource files are copied out.
  installPhase = ''
    runHook preInstall
    set -eu
    test -f dist/index.html
    test ! -L dist
    test ! -L dist/index.html
    js_count=$(find dist/assets -type f -name '*.js' | wc -l)
    test "$js_count" -ge 1
    if find dist -type l -print -quit | grep -q .; then
      echo "clawd panel resources: refusing symlinked dist output" >&2
      exit 1
    fi
    mkdir -p "$out/dist" "$out/plugins/browser-window-guard"
    find dist -type d -name node_modules -prune -o -type f -print > "$TMPDIR/clawd-panel-dist-files"
    while IFS= read -r file; do
      case "$file" in
        *"/node_modules/"*|node_modules/*)
          echo "clawd panel resources: refusing node_modules in dist" >&2
          exit 1
          ;;
      esac
      rel="''${file#dist/}"
      mkdir -p "$out/dist/$(dirname -- "$rel")"
      cp -p -- "$file" "$out/dist/$rel"
    done < "$TMPDIR/clawd-panel-dist-files"
    install_regular() {
      src="$1"
      dest="$2"
      test -f "$src"
      test ! -L "$src"
      mkdir -p "$(dirname -- "$dest")"
      cp -p -- "$src" "$dest"
    }
    install_regular plugins/browser-window-guard/__init__.py "$out/plugins/browser-window-guard/__init__.py"
    install_regular plugins/browser-window-guard/live_task_store.py "$out/plugins/browser-window-guard/live_task_store.py"
    install_regular plugins/browser-window-guard/plugin.yaml "$out/plugins/browser-window-guard/plugin.yaml"
    install_regular plugins/browser-window-guard/README.md "$out/plugins/browser-window-guard/README.md"
    if find "$out" \( -name node_modules -o -name package.json -o -name package-lock.json \) -print -quit | grep -q .; then
      echo "clawd panel resources: refusing to publish node_modules or npm manifests" >&2
      exit 1
    fi
    test -f "$out/dist/index.html"
    test -f "$out/plugins/browser-window-guard/plugin.yaml"
    runHook postInstall
  '';

  doCheck = false;
  meta = {
    description = "Built clawd panel dist plus browser-window-guard files";
  };
}
