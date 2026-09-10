"""V3.4 acceptance verifier: external blackbox gates with evidence.

Usage:
    .venv/bin/python scripts/verify_v34_permissions.py --backend process \
        --output .runtime/v34/acceptance/process-001 [--skip-browser] [--port 18300]

Only ``process`` backend exists; anything else exits unsupported (never
PASS). The output directory must not exist; it is created locked with a
manifest, raw SQLite readbacks, HTTP transcripts, screenshots and logs.
Exit 0 only when every REQUIRED area passes. Columns that cannot run here
(ALFWorld real backend, robot hardware) are recorded not-run, never PASS.
"""

from __future__ import annotations

import argparse
import json
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _run(cmd: list[str], cwd: Path, timeout_s: float = 600.0) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout_s)


def _wait_http(port: int, timeout_s: float = 40.0) -> None:
    deadline = time.time() + timeout_s
    url = f"http://127.0.0.1:{port}/api/sessions"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.3)
    raise RuntimeError(f"server on {port} never became ready")


def _http(method: str, url: str, body: Any = None) -> tuple[int, Any]:
    import httpx

    with httpx.Client(timeout=15.0) as client:
        response = client.request(
            method, url, json=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001
        payload = {"raw": response.text[:500]}
    return response.status_code, payload


def _sqlite_rows(db: Path, sql: str, params: tuple = ()) -> list[tuple]:
    conn = sqlite3.connect(str(db))
    try:
        return [tuple(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _phase_pytest(out: Path, manifest: dict) -> bool:
    cases = [
        ("process_blackbox", ["tests/homemaster/permissions/test_blackbox.py"]),
        ("alfworld_adapter_fake_seam", ["tests/homemaster/benchmarking/test_alfworld_permissions.py"]),
    ]
    ok = True
    for name, files in cases:
        proc = _run(
            [sys.executable, "-m", "pytest", *files, "-q", "-p", "no:cacheprovider"],
            REPO_ROOT,
        )
        (out / f"pytest-{name}.log").write_text(proc.stdout + proc.stderr, encoding="utf-8")
        passed = proc.returncode == 0
        ok = ok and passed
        tail = [line for line in proc.stdout.splitlines() if "passed" in line or "failed" in line]
        manifest["areas"][name] = {
            "status": "pass" if passed else "fail",
            "required": name == "process_blackbox",
            "log": f"pytest-{name}.log",
            "summary": tail[-1] if tail else "",
        }
    return ok


class _Server:
    def __init__(self, out: Path, name: str, db: Path, port: int, seed: bool,
                 extra_args: list[str] | None = None) -> None:
        self.out = out
        self.name = name
        self.db = db
        self.port = port
        self.seed = seed
        self.extra_args = extra_args or []
        self.proc: subprocess.Popen | None = None
        self.log_path = out / "logs" / f"{name}.stderr.log"
        self._log_handle: Any = None

    def start(self) -> None:
        cmd = [sys.executable, "scripts/v34_approval_server.py",
               "--db", str(self.db), "--port", str(self.port)]
        if self.seed:
            cmd += ["--seed", "--ready-file", str(self.out / "ready.json")]
        cmd += self.extra_args
        import os

        env = dict(os.environ)
        # uvicorn needs a WebSocket implementation; the project venv does
        # not vendors one, so the verifier supplements it from an
        # out-of-tree directory (never installed into the project venv).
        # Recorded in the manifest as environment, not product behavior.
        ws_lib = str(REPO_ROOT / ".runtime" / "v34" / "ws-lib")
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = ws_lib + (":" + existing if existing else "")
        self._log_handle = open(self.log_path, "w", encoding="utf-8")
        self.proc = subprocess.Popen(
            cmd, cwd=str(REPO_ROOT), stdout=subprocess.DEVNULL,
            stderr=self._log_handle, text=True, env=env,
        )
        _wait_http(self.port)

    def stop(self) -> str:
        assert self.proc is not None
        self.proc.terminate()
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=15)
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None
        text = self.log_path.read_text(encoding="utf-8")
        self.proc = None
        return text

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def _service_phase(out: Path, manifest: dict, port: int) -> bool:
    from homemaster.permissions.models import (
        PreparedPhysicalRequest,
        PreparedStep,
        Requirement,
        ResourceKey,
    )
    from homemaster.permissions.store import PermissionStore

    checks: list[dict[str, Any]] = []
    passed = True

    def record(name: str, ok: bool, detail: dict[str, Any]) -> None:
        nonlocal passed
        passed = passed and ok
        checks.append({"case": name, "status": "pass" if ok else "fail", **detail})

    db = out / "db" / "service.sqlite3"
    server = _Server(out, "service", db, port, seed=False)
    server.start()
    try:
        suffix = uuid4().hex[:8]
        item_id = f"item-take-{suffix}"
        request = PreparedPhysicalRequest(
            request_id=f"request-{suffix}", approval_id=f"approval-{suffix}",
            environment_id="home-test", session_id="session-svc", run_id="run-svc",
            intent_id=f"intent-{suffix}", intent_summary="拿杯子", revision=1,
            requirements=(Requirement(
                item_id=item_id,
                key=ResourceKey(environment_id="home-test", resource_kind="object",
                                resource_id="cup-a", action="pick_up"),
                display_name="白色杯子", location="客厅", action_label="拿取",
                step_ids=(f"step-take-{suffix}",),
            ),),
            steps=(PreparedStep(
                step_id=f"step-take-{suffix}", binding_ref=f"take-{suffix}",
                required_item_ids=(item_id,), summary="拿取杯子",
            ),),
            target_snapshot_revision="snap-svc",
            created_at=_now_iso(), deadline_at="2036-09-10T01:00:00Z",
        )
        writer = PermissionStore.open(db)
        try:
            writer.create_request(request)
        finally:
            writer.close()

        body = {"protocol_version": 2, "submission_id": "svc-sub-1",
                "request_revision": 1,
                "decisions": [{"item_id": item_id, "choice": "allow_always"}]}
        code, payload = _http("POST", f"{server.base}/api/approvals/{request.approval_id}", body)
        record("http-submit-always", code == 200 and payload.get("request_status") == "ready",
               {"http_code": code, "request_status": payload.get("request_status"),
                "persisted_grants": payload.get("persisted_grant_ids")})

        code2, payload2 = _http("POST", f"{server.base}/api/approvals/{request.approval_id}", body)
        record("http-duplicate-submit-idempotent",
               code2 == 200 and payload2.get("request_status") == "ready",
               {"http_code": code2})

        db_grants = _sqlite_rows(
            db, "SELECT grant_id, resource_id, action, status_hint FROM "
                "(SELECT grant_id, resource_id, action, "
                "CASE WHEN revoked_at IS NULL THEN 'active' ELSE 'revoked' END AS status_hint "
                "FROM permission_grants)")
        (out / "db" / "service-grants-readback.json").write_text(
            json.dumps(db_grants, ensure_ascii=False, indent=2), encoding="utf-8")
        code3, listed = _http("GET", f"{server.base}/api/permissions/grants?status=active")
        api_ids = sorted(g["grant_id"] for g in listed.get("grants", []))
        db_ids = sorted(row[0] for row in db_grants if row[3] == "active")
        record("api-matches-raw-db", code3 == 200 and api_ids == db_ids and len(api_ids) == 1,
               {"http_code": code3, "api_grants": api_ids, "db_grants": db_ids,
                "db_ref": "db/service-grants-readback.json"})
        decisions = _sqlite_rows(
            db, "SELECT item_id, decision FROM permission_request_items WHERE request_id = ?",
            (request.request_id,))
        record("decision-persisted", decisions == [(item_id, "allow_always")],
               {"items": decisions})

        grant_id = api_ids[0]
        code4, revoked = _http(
            "POST", f"{server.base}/api/permissions/grants/{grant_id}/revoke",
            {"submission_id": "svc-rev-1", "expected_revision": 1})
        record("http-revoke", code4 == 200 and revoked.get("status") == "revoked",
               {"http_code": code4, "status": revoked.get("status")})
    finally:
        stderr_text = server.stop()
    record("server-stderr-clean", "Traceback" not in stderr_text,
           {"log": "logs/service.stderr.log"})

    server2 = _Server(out, "service-restarted", db, port, seed=False)
    server2.start()
    try:
        code5, listed5 = _http("GET", f"{server2.base}/api/permissions/grants?status=revoked")
        revoked_ids = [g["grant_id"] for g in listed5.get("grants", [])]
        record("restart-keeps-history", code5 == 200 and grant_id in revoked_ids,
               {"http_code": code5, "revoked_grants": revoked_ids})
        code6, listed6 = _http("GET", f"{server2.base}/api/permissions/grants?status=active")
        record("restart-active-empty", code6 == 200 and listed6.get("grants") == [],
               {"http_code": code6})
    finally:
        stderr2 = server2.stop()
    record("restarted-stderr-clean", "Traceback" not in stderr2,
           {"log": "logs/service-restarted.stderr.log"})

    manifest["areas"]["service_http_persistence"] = {
        "status": "pass" if passed else "fail", "required": True, "checks": checks,
    }
    return passed


def _browser_phase(out: Path, manifest: dict, port: int, chrome_exe: str,
                   skip_browser: bool) -> bool:
    checks: list[dict[str, Any]] = []

    def record(name: str, ok: bool, detail: dict[str, Any]) -> None:
        checks.append({"case": name, "status": "pass" if ok else "fail", **detail})
        if not ok:
            raise AssertionError(f"browser check failed: {name} {detail}")

    if skip_browser:
        manifest["areas"]["browser_round"] = {
            "status": "not-run", "required": True,
            "reason": "skipped by --skip-browser",
        }
        return False
    try:
        sys.path.insert(
            0, str(REPO_ROOT / "tests" / "fixtures" / "permissions"))
        from cdp_driver import CDP, debugger_page_ws, launch_chrome
    except ImportError as exc:
        manifest["areas"]["browser_round"] = {
            "status": "not-run", "required": True, "reason": f"cdp driver missing: {exc}",
        }
        return False

    db = out / "db" / "browser.sqlite3"
    server = _Server(out, "browser", db, port, seed=True,
                     extra_args=["--signal-file", str(out / "signal-second")])
    server.start()
    shots = out / "shots"
    user_data = out / "chrome-profile"
    chrome = launch_chrome(chrome_exe, port + 100, user_data)
    status = "fail"
    try:
        deadline = time.time() + 30
        ready: dict[str, Any] = {}
        while time.time() < deadline:
            try:
                ready = json.loads((out / "ready.json").read_text(encoding="utf-8"))
                if ready.get("session_id"):
                    break
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.3)
        session_id = ready.get("session_id", "")
        record("seed-session-ready", bool(session_id), {"session_id": session_id})

        ws_url = debugger_page_ws(port + 100)
        page = CDP(ws_url)
        try:
            page.navigate(f"http://127.0.0.1:{port}/?session_id={session_id}")
            page.wait_for("!!document.querySelector('[role=\"dialog\"]')", 90.0)
            text = page.body_text()
            record("card-shows-names", all(w in text for w in ("卧室", "白色杯子", "拿取", "进入")),
                   {"excerpt": text[:300]})

            approval_ids = [r[0] for r in _sqlite_rows(db, "SELECT approval_id FROM permission_requests")]
            item_ids = [r[0] for r in _sqlite_rows(db, "SELECT item_id FROM permission_request_items")]
            hidden = [i for i in ([*approval_ids, *item_ids]) if i and i in text]
            record("dom-hides-internal-ids", hidden == [], {"leaked": hidden})
            page.screenshot(shots / "01-card.png")

            chose = page.evaluate(
                """(() => {
                  const choose = (legendPart, choicePart) => {
                    const dlg = document.querySelector('[role="dialog"]');
                    if (!dlg) return 'no-dialog';
                    const fs = [...dlg.querySelectorAll('fieldset')]
                      .find(f => (f.querySelector('legend')||{}).textContent.includes(legendPart));
                    if (!fs) return 'missing:' + legendPart;
                    const lab = [...fs.querySelectorAll('label')]
                      .find(l => l.textContent.includes(choicePart));
                    if (!lab) return 'missing-choice:' + choicePart;
                    lab.querySelector('input').click();
                    return 'ok';
                  };
                  return [choose('卧室', '始终允许'), choose('白色杯子', '本次允许')].join('|');
                })()""")
            record("choose-per-item", chose == "ok|ok", {"result": chose})
            submit_state = page.evaluate(
                """(() => {
                  const btn = [...document.querySelectorAll('[role="dialog"] button')]
                    .find(b => b.textContent.includes('提交决定'));
                  if (!btn) return 'missing';
                  const enabled = !btn.disabled;
                  if (enabled) btn.click();
                  return enabled ? 'clicked' : 'disabled';
                })()""")
            record("submit-decisions", submit_state == "clicked", {"result": submit_state})
            page.wait_for("!document.querySelector('[role=\"dialog\"]')", 30.0)

            page.evaluate("document.querySelector('button[aria-label=\"权限\"]').click()")
            page.evaluate(
                "document.querySelector('[role=\"tablist\"] [role=\"tab\"]:nth-child(2)').click()")
            page.wait_for("document.body.innerText.includes('卧室')", 30.0)
            grants_text = page.body_text()
            record("grants-page-lists", "卧室" in grants_text and "白色杯子" not in grants_text,
                   {"excerpt": grants_text[:300]})
            page.screenshot(shots / "02-grants.png")

            _, api_grants = _http("GET", f"{server.base}/api/permissions/grants?status=active")
            db_active = _sqlite_rows(
                db, "SELECT grant_id, resource_id, action, revision FROM permission_grants "
                    "WHERE revoked_at IS NULL")
            (out / "db" / "browser-grants-readback.json").write_text(
                json.dumps({"api": api_grants, "db": db_active},
                           ensure_ascii=False, indent=2, default=str),
                encoding="utf-8")
            api_rows = sorted((g["grant_id"], g["resource_id"], g["action"], g["revision"])
                              for g in api_grants.get("grants", []))
            record("browser-api-matches-raw-db",
                   api_rows == sorted(db_active) and len(api_rows) >= 1,
                   {"api": api_rows, "db": db_active,
                    "db_ref": "db/browser-grants-readback.json"})

            page.reload()
            page.wait_for("document.body.innerText.includes('对话')", 30.0)
            page.evaluate("document.querySelector('button[aria-label=\"权限\"]').click()")
            page.evaluate(
                "document.querySelector('[role=\"tablist\"] [role=\"tab\"]:nth-child(2)').click()")
            page.wait_for("document.body.innerText.includes('卧室')", 30.0)
            page.screenshot(shots / "03-grants-after-reload.png")
            record("reload-keeps-grants", "卧室" in page.body_text(), {})

            revoked = page.evaluate(
                """(() => {
                  const btn = document.querySelector('button[aria-label^="撤销"]');
                  if (!btn) return 'missing';
                  const name = btn.getAttribute('aria-label');
                  btn.click();
                  return name;
                })()""")
            record("revoke-one-action", isinstance(revoked, str) and revoked.startswith("撤销"),
                   {"button": revoked})
            page.wait_for("!document.body.innerText.includes('已允许进入')", 30.0)
            record("revoke-hides-row-keeps-rest",
                   "已允许进入" not in page.body_text(), {})

            (out / "signal-second").write_text("go", encoding="utf-8")
            page.wait_for("!!document.querySelector('[role=\"dialog\"]')", 60.0)
            second_text = page.body_text()
            record("rerequest-card-appears",
                   all(w in second_text for w in ("卧室", "白色杯子")),
                   {"excerpt": second_text[:300]})
            hidden2 = [i for i in (approval_ids + item_ids) if i and i in second_text]
            record("second-dom-hides-internal-ids", hidden2 == [], {"leaked": hidden2})
            page.screenshot(shots / "04-card2.png")
            status = "pass"
        finally:
            try:
                page.close()
            except Exception:  # noqa: BLE001
                pass
    finally:
        chrome.terminate()
        try:
            chrome.wait(timeout=15)
        except Exception:  # noqa: BLE001
            chrome.kill()
        import shutil

        shutil.rmtree(user_data, ignore_errors=True)
        stderr_text = server.stop()
    record("server-stderr-clean", "Traceback" not in stderr_text,
           {"log": "logs/browser.stderr.log"})

    restarted = _Server(out, "browser-restarted", db, port, seed=False)
    restarted.start()
    try:
        code_r, revoked_list = _http(
            "GET", f"{restarted.base}/api/permissions/grants?status=revoked")
        revoked_ids = [g["grant_id"] for g in revoked_list.get("grants", [])]
        record("restart-keeps-revocation", code_r == 200 and len(revoked_ids) >= 1,
               {"http_code": code_r, "revoked_grants": revoked_ids})
        code_a, active_list = _http(
            "GET", f"{restarted.base}/api/permissions/grants?status=active")
        record("restart-active-empty", code_a == 200 and active_list.get("grants") == [],
               {"http_code": code_a})
        pending = _sqlite_rows(
            db, "SELECT request_id, status FROM permission_requests "
                "WHERE status NOT IN ('ready','blocked','expired','cancelled')")
        record("restart-interrupts-pending",
               all(status == "interrupted" for _, status in pending),
               {"pending": pending})
    finally:
        stderr_restart = restarted.stop()
    record("restarted-stderr-clean", "Traceback" not in stderr_restart,
           {"log": "logs/browser-restarted.stderr.log"})
    manifest["areas"]["browser_round"] = {
        "status": status, "required": True, "checks": checks,
        "shots": ["shots/01-card.png", "shots/02-grants.png",
                  "shots/03-grants-after-reload.png", "shots/04-card2.png"],
    }
    return status == "pass"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="V3.4 acceptance verifier")
    parser.add_argument("--backend", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--skip-browser", action="store_true")
    parser.add_argument("--port", type=int, default=18300)
    parser.add_argument("--chrome", default="/usr/bin/google-chrome")
    args = parser.parse_args(argv)

    if args.backend != "process":
        print(f"unsupported backend {args.backend!r}; only 'process' exists",
              file=sys.stderr)
        return 2
    out = Path(args.output)
    if out.exists():
        print(f"refusing to reuse evidence directory: {out}", file=sys.stderr)
        return 2
    (out / "db").mkdir(parents=True)
    (out / "shots").mkdir(parents=True)
    (out / "logs").mkdir(parents=True)

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "created_at": _now_iso(),
        "backend": "process",
        "areas": {},
    }
    try:
        manifest["source_commit"] = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT),
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        manifest["source_commit"] = "unknown"
    manifest["python"] = sys.version.split()[0]
    manifest["ws_transport"] = "websockets supplemented via .runtime/v34/ws-lib (out-of-tree)"
    import sqlite3 as _sqlite3

    manifest["sqlite"] = _sqlite3.sqlite_version

    overall = True
    try:
        overall = _phase_pytest(out, manifest) and overall
        overall = _service_phase(out, manifest, args.port) and overall
        overall = _browser_phase(out, manifest, args.port + 10, args.chrome,
                                 args.skip_browser) and overall
    except AssertionError as exc:
        print(f"verifier assertion: {exc}", file=sys.stderr)
        overall = False
    except Exception as exc:  # noqa: BLE001
        print(f"verifier error: {type(exc).__name__}: {exc}", file=sys.stderr)
        manifest["verifier_error"] = f"{type(exc).__name__}: {exc}"
        overall = False

    manifest["areas"]["hardware"] = {
        "status": "not-run", "required": False,
        "reason": "no robot hardware, drivers, maps or object registry in this environment",
    }
    manifest["areas"]["alfworld_real_backend"] = {
        "status": "not-run", "required": False,
        "reason": "no THOR runtime on hkust4; fake-seam adapter tests pass (see alfworld_adapter_fake_seam)",
    }
    manifest["result"] = "pass" if overall else "fail"
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"result": manifest["result"],
                      "areas": {k: v.get("status") for k, v in manifest["areas"].items()}},
                     ensure_ascii=False))
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())
