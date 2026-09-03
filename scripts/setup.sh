#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
offline_bundle=""
with_browser=0
while (($#)); do
  case "$1" in
    --offline)
      (($# >= 2)) || { echo "--offline requires a bundle path" >&2; exit 2; }
      offline_bundle="$2"; shift 2 ;;
    --with-browser) with_browser=1; shift ;;
    -h|--help)
      printf '%s\n' 'Usage: ./scripts/setup.sh [--with-browser] [--offline BUNDLE]'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

case "$(uname -s):$(uname -m)" in
  Linux:x86_64) ;;
  *) echo "HomeMaster setup requires Linux x86_64" >&2; exit 2 ;;
esac

uv_bin="${HOMEMASTER_UV:-}"
if [[ -z "$uv_bin" ]]; then uv_bin="$(command -v uv || true)"; fi
if [[ -z "$uv_bin" || ! -x "$uv_bin" ]]; then
  echo "uv executable is required (expected uv 0.12.9); install it or set HOMEMASTER_UV" >&2
  exit 2
fi

if [[ -n "$offline_bundle" ]]; then
  [[ -d "$offline_bundle" ]] || { echo "offline bundle is missing: $offline_bundle" >&2; exit 2; }
  [[ -f "$offline_bundle/SHA256SUMS" ]] || { echo "offline bundle manifest is missing: $offline_bundle/SHA256SUMS" >&2; exit 2; }
  (cd "$offline_bundle" && sha256sum --check SHA256SUMS --quiet)
  export UV_NO_INDEX=1 UV_FIND_LINKS="$offline_bundle/wheelhouse"
fi

"$uv_bin" venv --allow-existing --python 3.11 "$repo_root/.venv"
sync_args=(sync --frozen)
if ((with_browser)); then sync_args+=(--extra browser); fi
"$uv_bin" "${sync_args[@]}" --project "$repo_root"

config="$repo_root/config/homemaster.yaml"
template="$repo_root/config/homemaster.example.yaml"
mkdir -p "$(dirname "$config")"
if [[ ! -e "$config" ]]; then
  cp -- "$template" "$config"
  chmod 600 "$config"
  "$repo_root/.venv/bin/python" - "$config" <<'PY'
from pathlib import Path
import secrets
import sys
import yaml

path = Path(sys.argv[1])
payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
neo4j = payload.setdefault("memory", {}).setdefault("neo4j", {})
neo4j["mode"] = "managed_local"
if not str(neo4j.get("password") or "").strip():
    neo4j["password"] = secrets.token_urlsafe(32)
path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")
PY
else
  [[ "$(stat -c '%a' "$config")" == 600 ]] || { echo "private config must be mode 0600: $config" >&2; exit 2; }
fi

neo4j_home="${HOMEMASTER_NEO4J_HOME:-$repo_root/.runtime/neo4j}"
java_home="${HOMEMASTER_JAVA_HOME:-$repo_root/.runtime/java}"
python_path="$repo_root/.venv/bin/python"
[[ -x "$neo4j_home/bin/neo4j" ]] || { echo "Neo4j executable is missing: $neo4j_home/bin/neo4j" >&2; exit 2; }
[[ -x "$java_home/bin/java" ]] || { echo "Java executable is missing: $java_home/bin/java" >&2; exit 2; }

"$python_path" "$repo_root/scripts/setup_memory_runtime.py" setup \
  --repo-root "$repo_root" --config "$config" --python "$python_path" \
  --neo4j-home "$neo4j_home" --java-home "$java_home"

if ((with_browser)); then
  "$python_path" -m playwright install chromium
fi

export HOMEMASTER_CONFIG_PATH="$config"
exec "$repo_root/scripts/homemaster" doctor --json
