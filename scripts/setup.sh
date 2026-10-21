#!/usr/bin/env bash
# Repo-development entry point: forwards to `homemaster setup` with the
# checkout's .runtime as the runtime root. The CLI owns all logic.
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
runtime_root="$repo_root/.runtime"
state_file="$runtime_root/setup-state.json"

cli_args=(--runtime-root "$runtime_root")
with_browser=0
offline_bundle=""
tier_set=0
while (($#)); do
  case "$1" in
    --with-browser) with_browser=1; shift ;;
    --offline)
      (($# >= 2)) || { echo "--offline requires a bundle path" >&2; exit 2; }
      offline_bundle="$2"; shift 2 ;;
    --offline-bundle) (($# >= 2)) || { echo "--offline-bundle requires a path" >&2; exit 2; }
      offline_bundle="$2"; shift 2 ;;
    --files) tier_set=1; cli_args+=(--files); shift ;;
    --full) tier_set=1; cli_args+=(--full); shift ;;
    --rebuild) cli_args+=(--rebuild); shift ;;
    --no-doctor) cli_args+=(--no-doctor); shift ;;
    --yes|-y) shift ;; # accepted for forward compatibility; setup is non-interactive
    --config) (($# >= 2)) || { echo "--config requires a path" >&2; exit 2; }
      cli_args+=(--config "$2"); shift 2 ;;
    -h|--help)
      printf '%s\n' 'Usage: ./scripts/setup.sh [--with-browser] [--offline BUNDLE] [--files|--full] [--rebuild] [--no-doctor]'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

# Repo checkouts historically provisioned the full tier; keep that default
# while the user-facing CLI defaults to files.
(( tier_set )) || cli_args+=(--full)

# Re-run stickiness: a prior --with-browser install keeps browser on reruns.
if (( ! with_browser )) && [[ -f "$state_file" ]] && grep -q '"browser"[[:space:]]*:[[:space:]]*true' "$state_file"; then
  with_browser=1
fi
(( with_browser )) && cli_args+=(--with-browser)

if [[ -n "$offline_bundle" ]]; then
  cli_args+=(--offline --offline-bundle "$offline_bundle")
  # uv run below may need the bundle wheelhouse to materialize the dev env.
  export UV_NO_INDEX=1 UV_FIND_LINKS="$offline_bundle/wheelhouse"
fi

uv_bin="${HOMEMASTER_UV:-}"
if [[ -z "$uv_bin" ]]; then uv_bin="$(command -v uv || true)"; fi
if [[ -z "$uv_bin" || ! -x "$uv_bin" ]]; then
  echo "uv executable is required (supported range: >=0.11,<0.13); install it or set HOMEMASTER_UV" >&2
  exit 2
fi

exec "$uv_bin" run --project "$repo_root" python -m homemaster.cli setup "${cli_args[@]}"
