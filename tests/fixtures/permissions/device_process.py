"""Independent household device simulator for V3.4 blackbox acceptance.

STANDARD LIBRARY ONLY. Speaks JSONL over stdin/stdout: one request object
per line, one response object per line. Manages one robot area, two
distinct cups and per-command attempt counts.

This process is a controllable EXTERNAL test system, not real hardware;
acceptance reports must label it as such. It never imports homemaster,
ALFWorld, or any permission code: the verifier reads it directly to check
the adapter's claims.
"""

from __future__ import annotations

import json
import sys

AREAS = ("living", "bedroom", "kitchen")


def initial_state() -> dict:
    return {
        "area": "living",
        "objects": {
            "cup-a": {"area": "living", "held": False, "clean": False, "spot": "shelf"},
            "cup-b": {"area": "bedroom", "held": False, "clean": False, "spot": "desk"},
        },
        "inventory": [],
        "ops": {
            "move": 0,
            "pick_up": 0,
            "place": 0,
            "clean": 0,
            "read": 0,
            "reset": 0,
        },
    }


class Device:
    def __init__(self) -> None:
        self.state = initial_state()
        self.fail_armed = False

    def handle(self, message: object) -> dict | None:
        if not isinstance(message, dict):
            return {"id": None, "ok": False, "code": "bad-message"}
        msg_id = message.get("id")
        cmd = message.get("cmd")
        if cmd == "shutdown":
            return None
        if cmd == "reset":
            self.state = initial_state()
            self.fail_armed = False
            self.state["ops"]["reset"] += 1
            return self._reply(msg_id, True, "reset-ok")
        if cmd == "read":
            self.state["ops"]["read"] += 1
            return self._reply(msg_id, True, "read-ok")
        if cmd == "fail_next":
            self.fail_armed = bool(message.get("fail", True))
            return self._reply(msg_id, True, "fail-next-armed")
        if cmd in ("move", "pick_up", "place", "clean"):
            self.state["ops"][cmd] += 1
            if self.fail_armed:
                self.fail_armed = False
                return self._reply(msg_id, False, "injected-failure")
            handler = getattr(self, f"_do_{cmd}")
            return handler(msg_id, message)
        return self._reply(msg_id, False, "unknown-command")

    def _reply(self, msg_id: object, ok: bool, code: str) -> dict:
        return {"id": msg_id, "ok": ok, "code": code, "state": self.state}

    def _do_move(self, msg_id: object, message: dict) -> dict:
        area = message.get("area")
        if area not in AREAS:
            return self._reply(msg_id, False, "move-unknown-area")
        self.state["area"] = area
        for obj in self.state["objects"].values():
            if isinstance(obj, dict) and obj.get("held"):
                obj["area"] = area
        return self._reply(msg_id, True, "move-ok")

    def _do_pick_up(self, msg_id: object, message: dict) -> dict:
        obj = self._objects_get(message.get("object"))
        if obj is None:
            return self._reply(msg_id, False, "pick_up-unknown-object")
        if obj["held"] or obj["area"] != self.state["area"]:
            return self._reply(msg_id, False, "pick_up-precondition")
        obj["held"] = True
        self.state["inventory"].append(message["object"])
        return self._reply(msg_id, True, "pick_up-ok")

    def _do_place(self, msg_id: object, message: dict) -> dict:
        obj = self._objects_get(message.get("object"))
        if obj is None:
            return self._reply(msg_id, False, "place-unknown-object")
        if not obj["held"]:
            return self._reply(msg_id, False, "place-precondition")
        obj["held"] = False
        obj["area"] = self.state["area"]
        obj["spot"] = str(message.get("spot") or "floor")
        if message["object"] in self.state["inventory"]:
            self.state["inventory"].remove(message["object"])
        return self._reply(msg_id, True, "place-ok")

    def _do_clean(self, msg_id: object, message: dict) -> dict:
        obj = self._objects_get(message.get("object"))
        if obj is None:
            return self._reply(msg_id, False, "clean-unknown-object")
        if not obj["held"] and obj["area"] != self.state["area"]:
            return self._reply(msg_id, False, "clean-precondition")
        obj["clean"] = True
        return self._reply(msg_id, True, "clean-ok")

    def _objects_get(self, name: object) -> dict | None:
        if not isinstance(name, str):
            return None
        obj = self.state["objects"].get(name)
        return obj if isinstance(obj, dict) else None


def main() -> int:
    device = Device()
    stdin = sys.stdin
    stdout = sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            stdout.write(json.dumps({"id": None, "ok": False, "code": "bad-json"}) + "\n")
            stdout.flush()
            continue
        try:
            response = device.handle(message)
        except BrokenPipeError:
            return 0
        if response is None:
            return 0
        stdout.write(json.dumps(response) + "\n")
        stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
