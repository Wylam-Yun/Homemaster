#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
output=""
with_browser=0
while (($#)); do
  case "$1" in
    --output) (($# >= 2)) || { echo "--output requires a directory" >&2; exit 2; }; output="$2"; shift 2 ;;
    --with-browser) with_browser=1; shift ;;
    -h|--help) echo 'Usage: ./scripts/build_runtime_bundle.sh --output PATH [--with-browser]'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
[[ -n "$output" ]] || { echo "--output is required" >&2; exit 2; }
mkdir -p "$output/wheelhouse" "$output/assets"
rm -f "$output"/source.tar.gz "$output"/homemaster-*.whl "$output"/SHA256SUMS

uv_bin="${HOMEMASTER_UV:-$(command -v uv || true)}"
[[ -x "$uv_bin" ]] || { echo "uv executable is required (set HOMEMASTER_UV)" >&2; exit 2; }
git -C "$repo_root" archive --format=tar.gz --prefix=Homemaster/ HEAD > "$output/source.tar.gz"
"$uv_bin" build --wheel --out-dir "$output/wheelhouse" --project "$repo_root" >/dev/null
cp "$repo_root/uv.lock" "$output/uv.lock"
cp "$repo_root/config/homemaster.example.yaml" "$output/homemaster.example.yaml"
python - "$repo_root/config/runtime-assets.lock.json" "$output/assets" <<'PY'
import hashlib
import json
import pathlib
import platform
import subprocess
import sys


def current_platform() -> str:
    sysname = platform.system()
    machine = platform.machine().lower()
    if sysname == "Linux" and machine in ("x86_64", "amd64"):
        return "linux-x86_64"
    if sysname == "Darwin" and machine in ("arm64", "aarch64"):
        return "darwin-arm64"
    raise SystemExit(f"unsupported platform: {sysname}:{machine}")


lock = json.loads(pathlib.Path(sys.argv[1]).read_text())
out = pathlib.Path(sys.argv[2])
assets = {"neo4j": lock["neo4j"], "java": lock["platforms"][current_platform()]["java"]}
for name, asset in assets.items():
    target = out / pathlib.PurePosixPath(asset["url"].split("?")[0]).name
    if not target.exists():
        subprocess.run(["curl", "--fail", "--location", "--retry", "3", "--output", str(target), asset["url"]], check=True)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    if digest != asset["sha256"]:
        raise SystemExit(f"{name} SHA256 mismatch: expected {asset['sha256']}, got {digest}")
PY
cp "$repo_root/config/runtime-assets.lock.json" "$output/runtime-assets.lock.json"
printf 'browser=%s\n' "$with_browser" > "$output/bundle-metadata.txt"
if command -v sha256sum >/dev/null 2>&1; then
  sha256_bin=(sha256sum)
else
  sha256_bin=(shasum -a 256)
fi
(cd "$output" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 "${sha256_bin[@]}" > SHA256SUMS)
