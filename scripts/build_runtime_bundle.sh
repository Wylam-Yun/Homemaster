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
import subprocess
import sys

lock = json.loads(pathlib.Path(sys.argv[1]).read_text())
out = pathlib.Path(sys.argv[2])
for name in ("java", "neo4j"):
    asset = lock[name]
    target = out / pathlib.PurePosixPath(asset["url"].split("?")[0]).name
    if not target.exists():
        subprocess.run(["curl", "--fail", "--location", "--retry", "3", "--output", str(target), asset["url"]], check=True)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    if digest != asset["sha256"]:
        raise SystemExit(f"{name} SHA256 mismatch: expected {asset['sha256']}, got {digest}")
PY
cp "$repo_root/config/runtime-assets.lock.json" "$output/runtime-assets.lock.json"
printf 'browser=%s\n' "$with_browser" > "$output/bundle-metadata.txt"
(cd "$output" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
