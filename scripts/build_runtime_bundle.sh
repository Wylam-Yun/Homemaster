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
mkdir -p "$output/wheelhouse"
rm -f "$output"/source.tar.gz "$output"/homemaster-*.whl "$output"/SHA256SUMS

uv_bin="${HOMEMASTER_UV:-$(command -v uv || true)}"
[[ -x "$uv_bin" ]] || { echo "uv executable is required (set HOMEMASTER_UV)" >&2; exit 2; }
git -C "$repo_root" archive --format=tar.gz --prefix=Homemaster/ HEAD > "$output/source.tar.gz"
"$uv_bin" build --wheel --out-dir "$output/wheelhouse" --project "$repo_root" >/dev/null
cp "$repo_root/uv.lock" "$output/uv.lock"
cp "$repo_root/config/homemaster.example.yaml" "$output/homemaster.example.yaml"
printf 'browser=%s\n' "$with_browser" > "$output/bundle-metadata.txt"
(cd "$output" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
