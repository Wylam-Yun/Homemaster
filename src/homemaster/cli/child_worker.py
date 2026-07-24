"""Line-oriented HomeMaster child worker used by agent and task tools."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from homemaster.cli.renderers import result_exit_code
from homemaster.cli.run_command import execute_one_shot


def run_child_worker(
    *,
    model: str | None = None,
    config_path: Path | None = None,
) -> int:
    for line in sys.stdin:
        prompt = _prompt(line)
        if not prompt:
            continue
        try:
            execution = execute_one_shot(
                prompt=prompt,
                model=model,
                quiet=True,
                config_path=config_path,
            )
            result = execution.result
            payload = {
                "run_id": result.run_id,
                "status": str(result.status),
                "reply": result.final_reply,
                "return_code": result_exit_code(result),
            }
        except Exception as exc:
            payload = {
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "return_code": 1,
            }
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)
    return 0


def _prompt(line: str) -> str:
    value = line.strip()
    if not value:
        return ""
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return value
    if isinstance(payload, dict) and isinstance(payload.get("text"), str):
        return payload["text"].strip()
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model")
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    raise SystemExit(run_child_worker(model=args.model, config_path=args.config))


if __name__ == "__main__":
    main()
