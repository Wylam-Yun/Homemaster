#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
alfworld_root=""
python_executable=""
uv_bin="${HOMEMASTER_UV:-}"

while (($#)); do
  case "$1" in
    --root)
      (($# >= 2)) || { echo "--root requires an ALFWorld checkout" >&2; exit 2; }
      alfworld_root="$2"; shift 2 ;;
    --python)
      (($# >= 2)) || { echo "--python requires an executable" >&2; exit 2; }
      python_executable="$2"; shift 2 ;;
    --repo-root)
      (($# >= 2)) || { echo "--repo-root requires a checkout" >&2; exit 2; }
      repo_root="$(cd -- "$2" && pwd -P)"; shift 2 ;;
    -h|--help)
      printf '%s\n' 'Usage: scripts/setup-alfworld.sh --root /path/to/alfworld [--python /path/to/python]'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$alfworld_root" ]] || { echo "--root is required" >&2; exit 2; }
alfworld_root="$(cd -- "$alfworld_root" && pwd -P)"
[[ -f "$alfworld_root/configs/base_config.yaml" ]] || {
  echo "ALFWorld configs/base_config.yaml is missing: $alfworld_root" >&2; exit 2;
}
[[ -d "$alfworld_root/data/json_2.1.1" ]] || {
  echo "ALFWorld data/json_2.1.1 is missing: $alfworld_root" >&2; exit 2;
}

runtime_root="$repo_root/.runtime"
worker_venv="$runtime_root/alfworld-venv"
mkdir -p "$runtime_root"

if [[ -z "$python_executable" ]]; then
  [[ -n "$uv_bin" ]] || uv_bin="$(command -v uv || true)"
  [[ -x "$uv_bin" ]] || { echo "uv is required when --python is omitted" >&2; exit 2; }
  "$uv_bin" venv --allow-existing --python 3.11 "$worker_venv"
  python_executable="$worker_venv/bin/python"
  "$uv_bin" pip install --python "$python_executable" --requirement "$repo_root/config/alfworld/requirements.lock"
else
  python_executable="$(cd -- "$(dirname -- "$python_executable")" && pwd -P)/$(basename -- "$python_executable")"
fi

[[ -x "$python_executable" ]] || { echo "worker Python is not executable: $python_executable" >&2; exit 2; }
"$python_executable" - "$repo_root" "$alfworld_root" "$python_executable" <<'PY'
import json
import pathlib
import subprocess
import sys

repo_root, alfworld_root, python_executable = map(pathlib.Path, sys.argv[1:])
probe = subprocess.run(
    [str(python_executable), "-c", "import alfworld, ai2thor, torch, cv2, PIL, numpy"],
    capture_output=True,
    text=True,
    check=False,
)
if probe.returncode:
    detail = (probe.stderr or probe.stdout).strip().splitlines()[-1:]
    raise SystemExit(f"worker dependency probe failed: {detail[0] if detail else 'unknown'}")
payload = {
    "schema": "homemaster-alfworld-binding-v1",
    "python": str(python_executable.resolve()),
    "root": str(alfworld_root.resolve()),
    "data_root": str((alfworld_root / "data").resolve()),
    "requirements": "config/alfworld/requirements.lock",
}
binding = repo_root / ".runtime" / "alfworld-binding.json"
binding.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
runtime_venv = repo_root / ".runtime" / "alfworld-venv"
if runtime_venv.exists() and runtime_venv.resolve() != python_executable.parent.parent.resolve():
    raise SystemExit(f"existing runtime binding conflicts with {runtime_venv}")
if not runtime_venv.exists():
    runtime_venv.symlink_to(python_executable.parent.parent.resolve(), target_is_directory=True)
runtime_root = repo_root / ".runtime" / "alfworld"
if runtime_root.exists() and runtime_root.resolve() != alfworld_root.resolve():
    raise SystemExit(f"existing runtime binding conflicts with {runtime_root}")
if not runtime_root.exists():
    runtime_root.symlink_to(alfworld_root.resolve(), target_is_directory=True)
print(json.dumps(payload, sort_keys=True))
PY
