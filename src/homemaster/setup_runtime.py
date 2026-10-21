"""Productized ``homemaster setup`` runtime provisioning.

This module is the single source of truth for two related layers:

* the locked runtime-asset downloaders (Neo4j + Temurin JDK, sha256-pinned via
  ``runtime-assets.lock.json``) — historically ``scripts/download_runtime_assets.py``,
  which is now a thin wrapper so ``python -m scripts.download_runtime_assets``
  keeps working from a checkout;
* :func:`setup_runtime`, the install orchestrator behind
  ``homemaster setup`` — platform gate → uv check → runtime venv → asset
  download → config bootstrap → doctor.

Two provisioning contexts are supported:

* **repository checkout** (``--runtime-root <repo>/.runtime``, used by the
  ``scripts/setup.sh`` shim): the runtime venv is built with
  ``uv sync --frozen --no-editable --project <repo>`` so the installed tree
  comes from the checked-out source and lockfile, and the managed config lives
  at ``<repo>/config/homemaster.yaml`` with portable ``../.runtime/…`` paths.
* **installed tool** (``uv tool install`` / wheel): the runtime root defaults
  to ``~/.homemaster/runtime``; the venv is populated via
  ``uv pip install "<pkg>[memory]"`` and the managed config lives at
  ``$HOMEMASTER_HOME/config.yaml`` (``~/.homemaster/config.yaml``) with
  absolute paths.

The package spec for tool-mode installs defaults to ``homemaster`` and can be
overridden with ``HOMEMASTER_SETUP_PACKAGE`` (e.g. a local checkout or wheel
path) because the project is not published to an index. Lock JSON and the
example config are packaged resources (``homemaster/resources/``); a repo
``config/runtime-assets.lock.json`` still wins when present so developers edit
one truth source.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.resources
import json
import os
import platform
import secrets
import shutil
import subprocess
import tarfile
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import yaml

from homemaster.config.config import _homemaster_home


class SetupError(RuntimeError):
    """Fatal, user-facing setup failure (maps to exit code 2)."""


# ---------------------------------------------------------------------------
# Locked asset downloaders (moved from scripts/download_runtime_assets.py)
# ---------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _current_platform() -> str:
    # platform.* is portable; os.uname() does not exist on Windows.
    sysname = platform.system()
    machine = platform.machine().lower()
    if sysname == "Linux" and machine in ("x86_64", "amd64"):
        return "linux-x86_64"
    if sysname == "Darwin" and machine in ("arm64", "aarch64"):
        return "darwin-arm64"
    raise SetupError(f"unsupported platform: {sysname}:{machine}")


def _packaged_lock_resource() -> Any:
    return importlib.resources.files("homemaster").joinpath(
        "resources/runtime-assets.lock.json"
    )


def _load_lock(repo_root: Path | None) -> dict[str, Any]:
    """Load the assets lock: repo ``config/`` copy wins, packaged copy falls back."""

    if repo_root is not None:
        candidate = repo_root / "config" / "runtime-assets.lock.json"
        if candidate.is_file():
            return json.loads(candidate.read_text(encoding="utf-8"))
    resource = _packaged_lock_resource()
    if resource.is_file():
        return json.loads(resource.read_text(encoding="utf-8"))
    raise SetupError(
        "runtime assets lock is missing: neither "
        f"{repo_root / 'config' / 'runtime-assets.lock.json' if repo_root else '<repo>'} "
        "nor the packaged homemaster/resources copy is readable"
    )


def _safe_extract_tar(package: tarfile.TarFile, root: Path, label: str) -> None:
    members = package.getmembers()
    for member in members:
        target = (root / member.name).resolve()
        if root.resolve() not in target.parents:
            raise SetupError(f"unsafe {label} archive member: {member.name}")
    try:
        package.extractall(root, filter="data")
    except TypeError:  # Python < 3.11.4 lacks the filter kwarg
        package.extractall(root)


def _fetch_archive(asset: Mapping[str, Any], *, offline_bundle: Path | None, prefix: str) -> Path:
    archive = (offline_bundle / "assets" / Path(asset["url"]).name) if offline_bundle else None
    if archive is None or not archive.is_file():
        if offline_bundle:
            raise SetupError(f"offline asset is missing: {archive}")
        archive = Path(tempfile.mkstemp(prefix=prefix, suffix=".tar.gz")[1])
        try:
            subprocess.run(
                [
                    "curl", "--fail", "--location", "--retry", "3",
                    "--output", str(archive), asset["url"],
                ],
                check=True,
            )
        except Exception:
            archive.unlink(missing_ok=True)
            raise
    return archive


def ensure_neo4j(
    repo_root: Path | None, destination: Path, *, offline_bundle: Path | None = None
) -> Path:
    if (destination / "bin" / "neo4j").is_file() and (
        destination / "bin" / "neo4j-admin"
    ).is_file():
        return destination
    lock = _load_lock(repo_root)
    asset = lock["neo4j"]
    archive = _fetch_archive(asset, offline_bundle=offline_bundle, prefix="homemaster-neo4j-")
    try:
        actual = _sha256(archive)
        if actual != asset["sha256"]:
            raise SetupError(f"Neo4j SHA256 mismatch: expected {asset['sha256']}, got {actual}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=destination.parent) as temp:
            root = Path(temp)
            with tarfile.open(archive, "r:gz") as package:
                _safe_extract_tar(package, root, "Neo4j")
            extracted = root / f"neo4j-community-{asset['version']}"
            if not (extracted / "bin" / "neo4j").is_file():
                raise SetupError("Neo4j archive has unexpected layout")
            for name in ("data", "logs", "run"):
                shutil.rmtree(extracted / name, ignore_errors=True)
            if destination.exists():
                raise SetupError(
                    f"runtime destination already exists but is incomplete: {destination}"
                )
            os.replace(extracted, destination)
    finally:
        if not offline_bundle:
            archive.unlink(missing_ok=True)
    return destination


def ensure_java(
    repo_root: Path | None, destination: Path, *, offline_bundle: Path | None = None
) -> Path:
    if (destination / "bin" / "java").is_file():
        return destination
    lock = _load_lock(repo_root)
    asset = lock["platforms"][_current_platform()]["java"]
    archive = _fetch_archive(asset, offline_bundle=offline_bundle, prefix="homemaster-java-")
    try:
        actual = _sha256(archive)
        if actual != asset["sha256"]:
            raise SetupError(f"Java SHA256 mismatch: expected {asset['sha256']}, got {actual}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=destination.parent) as temp:
            root = Path(temp)
            with tarfile.open(archive, "r:gz") as package:
                _safe_extract_tar(package, root, "Java")
            candidates = [
                p for p in root.iterdir()
                if p.is_dir() and (p / "bin" / "java").is_file()
            ]
            if not candidates:
                candidates = [
                    p / "Contents" / "Home"
                    for p in root.iterdir()
                    if p.is_dir() and (p / "Contents" / "Home" / "bin" / "java").is_file()
                ]
            if len(candidates) != 1:
                raise SetupError("Java archive has unexpected layout")
            if destination.exists():
                raise SetupError(
                    f"runtime destination already exists but is incomplete: {destination}"
                )
            os.replace(candidates[0], destination)
    finally:
        if not offline_bundle:
            archive.unlink(missing_ok=True)
    return destination


# ---------------------------------------------------------------------------
# setup_runtime orchestrator
# ---------------------------------------------------------------------------

_PASSWORD_PLACEHOLDERS = {"", "<set-during-setup>"}


def _detect_repo_checkout(runtime_root: Path) -> Path | None:
    """Return the repo root when ``runtime_root`` is a checkout's ``.runtime``."""

    if runtime_root.name != ".runtime":
        return None
    parent = runtime_root.parent
    pyproject = parent / "pyproject.toml"
    if not (pyproject.is_file() and (parent / "uv.lock").is_file()):
        return None
    try:
        if 'name = "homemaster"' not in pyproject.read_text(encoding="utf-8"):
            return None
    except OSError:
        return None
    return parent


def _parse_version(text: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in text.strip().split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _version_satisfies(version: str, spec: str) -> bool:
    """Evaluate ``>=a.b,<c.d``-style lock ranges against a dotted version."""

    value = _parse_version(version)
    for constraint in spec.split(","):
        constraint = constraint.strip()
        for operator in (">=", "<=", "==", ">", "<"):
            if constraint.startswith(operator):
                target = _parse_version(constraint[len(operator):])
                width = max(len(value), len(target))
                lhs = value + (0,) * (width - len(value))
                rhs = target + (0,) * (width - len(target))
                if operator == ">=" and not lhs >= rhs:
                    return False
                if operator == "<=" and not lhs <= rhs:
                    return False
                if operator == ">" and not lhs > rhs:
                    return False
                if operator == "<" and not lhs < rhs:
                    return False
                if operator == "==" and not lhs == rhs:
                    return False
                break
    return True


def _find_uv(environ: Mapping[str, str], lock: Mapping[str, Any]) -> str:
    uv_bin = environ.get("HOMEMASTER_UV", "").strip() or shutil.which(
        "uv", path=environ.get("PATH") or None
    )
    if not uv_bin or not Path(uv_bin).exists():
        raise SetupError(
            "uv executable is required (supported range: "
            f"{lock.get('uv', {}).get('version_range', '>=0.11,<0.13')}); "
            "install it or set HOMEMASTER_UV"
        )
    probe = subprocess.run([uv_bin, "--version"], capture_output=True, text=True, check=False)
    version = (probe.stdout.split() or ["", ""])[1] if probe.returncode == 0 else ""
    spec = str(lock.get("uv", {}).get("version_range", ">=0.11,<0.13"))
    if not version or not _version_satisfies(version, spec):
        raise SetupError(
            f"unsupported uv version: {version or 'unknown'} (supported range: {spec})"
        )
    return uv_bin


def _verify_offline_bundle(bundle: Path) -> None:
    if not bundle.is_dir():
        raise SetupError(f"offline bundle is missing: {bundle}")
    manifest = bundle / "SHA256SUMS"
    if not manifest.is_file():
        raise SetupError(f"offline bundle manifest is missing: {manifest}")
    for line in manifest.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        expected, _, name = line.partition("  ")
        name = name.strip().lstrip("*")
        target = bundle / name
        if not target.is_file():
            raise SetupError(f"offline bundle entry is missing: {target}")
        if _sha256(target) != expected:
            raise SetupError(f"offline bundle checksum mismatch: {name}")


def _package_spec(environ: Mapping[str, str], *, full: bool, with_browser: bool) -> str:
    """Install spec for tool-mode runtime venvs.

    ``HOMEMASTER_SETUP_PACKAGE`` overrides the whole spec verbatim (the project
    is not index-published, so source/wheel installs point at a local path).
    """

    override = environ.get("HOMEMASTER_SETUP_PACKAGE", "").strip()
    if override:
        return override
    extras = [name for name, on in (("memory", full), ("browser", with_browser)) if on]
    suffix = f"[{','.join(extras)}]" if extras else ""
    origin = _installed_dist_origin()
    if origin is not None:
        return f"homemaster{suffix} @ {origin}"
    return "homemaster" + suffix


def _installed_dist_origin() -> str | None:
    """PEP 610 origin of the running homemaster dist (file:// or https://)."""

    try:
        raw = importlib.metadata.distribution("homemaster").read_text("direct_url.json")
    except Exception:
        return None
    if not raw:
        return None
    try:
        url = str(json.loads(raw).get("url") or "")
    except ValueError:
        return None
    return url if url.startswith(("file://", "http://", "https://")) else None


def _child_env(
    environ: Mapping[str, str], *, offline_bundle: Path | None, offline_only: bool
) -> dict[str, str]:
    env = dict(environ)
    if offline_bundle is not None:
        env["UV_NO_INDEX"] = "1"
        env["UV_FIND_LINKS"] = str(offline_bundle / "wheelhouse")
    if offline_only:
        env["UV_OFFLINE"] = "1"
    return env


def _run(cmd: list[str], *, env: Mapping[str, str], label: str) -> None:
    proc = subprocess.run([str(part) for part in cmd], env=dict(env), check=False)
    if proc.returncode:
        raise SetupError(
            f"{label} failed (exit {proc.returncode}): "
            + " ".join(str(p) for p in cmd)
        )


def _default_python_installer(
    *,
    uv: str,
    venv: Path,
    repo_root: Path | None,
    full: bool,
    with_browser: bool,
    environ: Mapping[str, str],
) -> Callable[[Path], int]:
    """Build ``<venv>``; repo checkouts sync the project, tool installs use pip."""

    def install(target: Path) -> int:
        env = dict(environ)
        if repo_root is not None:
            _run(
                [uv, "venv", "--allow-existing", "--python", "3.11", str(target)],
                env=env, label="uv venv",
            )
            env["UV_PROJECT_ENVIRONMENT"] = str(target)
            sync = [uv, "sync", "--frozen", "--no-editable", "--project", str(repo_root)]
            if with_browser:
                sync += ["--extra", "browser"]
            _run(sync, env=env, label="uv sync")
            return 0
        _run([uv, "venv", "--python", "3.11", str(target)], env=env, label="uv venv")
        spec = _package_spec(environ, full=full, with_browser=with_browser)
        _run(
            [uv, "pip", "install", "--python", str(target / "bin" / "python"), spec],
            env=env,
            label=f"uv pip install {spec}",
        )
        return 0

    return install


def _read_packaged_example() -> str:
    resource = importlib.resources.files("homemaster").joinpath(
        "resources/homemaster.example.yaml"
    )
    if not resource.is_file():
        raise SetupError("packaged homemaster.example.yaml resource is missing")
    return resource.read_text(encoding="utf-8")


def _file_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def _write_yaml_0600(path: Path, payload: Mapping[str, Any]) -> None:
    content = yaml.safe_dump(dict(payload), sort_keys=False, allow_unicode=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fchmod(handle.fileno(), 0o600)
    os.replace(temporary, path)


def _inject_config_keys(
    payload: dict[str, Any],
    *,
    full: bool,
    repo_root: Path | None,
    runtime_root: Path,
    hm_home: Path,
) -> dict[str, Any]:
    """Apply the managed-runtime keys onto a parsed config payload.

    Repo checkouts keep the portable ``../.runtime/…`` values that
    ``setup_memory_runtime.py`` enforces; installed deployments get absolute
    paths anchored at the runtime root / ``$HOMEMASTER_HOME``.
    """

    # The shipped example pins ``browser_gateway.start_url`` for the web demo;
    # a managed config must not silently select the browser tool environment
    # (which requires the ``browser`` extra) -- leave the section but clear it.
    browser_gateway = payload.get("browser_gateway")
    if isinstance(browser_gateway, dict):
        browser_gateway["start_url"] = None

    memory = payload.setdefault("memory", {})
    if not isinstance(memory, dict):
        return payload
    memory["mode"] = "full" if full else "files"
    neo4j = memory.get("neo4j")
    if not isinstance(neo4j, dict):
        return payload
    neo4j["mode"] = "managed_local"
    if repo_root is not None:
        memory["data_root"] = "../.runtime/memory"
        neo4j["home"] = "../.runtime/neo4j"
        neo4j["java_home"] = "../.runtime/java"
    else:
        memory["data_root"] = str(hm_home / "memory")
        neo4j["home"] = str(runtime_root / "neo4j")
        neo4j["java_home"] = str(runtime_root / "java")
    if str(neo4j.get("password") or "").strip() in _PASSWORD_PLACEHOLDERS:
        neo4j["password"] = secrets.token_urlsafe(32)
    return payload


def _patch_existing_config(
    payload: dict[str, Any],
    *,
    runtime_root: Path,
    hm_home: Path,
) -> dict[str, Any]:
    """Fill only missing managed-runtime keys on a pre-existing config."""

    memory = payload.get("memory")
    if not isinstance(memory, dict):
        return payload
    if not str(memory.get("data_root") or "").strip():
        memory["data_root"] = str(hm_home / "memory")
    neo4j = memory.get("neo4j")
    if isinstance(neo4j, dict) and neo4j.get("mode") == "managed_local":
        if neo4j.get("home") is None:
            neo4j["home"] = str(runtime_root / "neo4j")
        if neo4j.get("java_home") is None:
            neo4j["java_home"] = str(runtime_root / "java")
        if str(neo4j.get("password") or "").strip() in _PASSWORD_PLACEHOLDERS:
            neo4j["password"] = secrets.token_urlsafe(32)
    return payload


def _ensure_config(
    *,
    config_path: Path,
    full: bool,
    repo_root: Path | None,
    runtime_root: Path,
    hm_home: Path,
    echo: Callable[[str], None],
) -> None:
    if config_path.is_file():
        if _file_mode(config_path) != 0o600:
            raise SetupError(f"private config must be mode 0600: {config_path}")
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(payload, dict):
            raise SetupError(f"HomeMaster config must be a YAML mapping: {config_path}")
        if repo_root is None:
            # Installed deployments own their config: top up managed keys only.
            patched = _patch_existing_config(payload, runtime_root=runtime_root, hm_home=hm_home)
            _write_yaml_0600(config_path, patched)
        echo(f"[OK] config: {config_path} (exists, mode 0600 verified)")
        return
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = yaml.safe_load(_read_packaged_example()) or {}
    if not isinstance(payload, dict):
        raise SetupError("packaged homemaster.example.yaml is not a mapping")
    payload = _inject_config_keys(
        payload, full=full, repo_root=repo_root, runtime_root=runtime_root, hm_home=hm_home
    )
    _write_yaml_0600(config_path, payload)
    echo(f"[OK] config: {config_path} (0600)")


def _bind_repo_runtime(
    *,
    repo_root: Path,
    config_path: Path,
    venv_python: Path,
    neo4j_home: Path,
    java_home: Path,
    environ: Mapping[str, str],
) -> None:
    script = repo_root / "scripts" / "setup_memory_runtime.py"
    if not script.is_file():
        raise SetupError(f"runtime binding script is missing: {script}")
    _run(
        [
            str(venv_python),
            str(script),
            "setup",
            "--repo-root",
            str(repo_root),
            "--config",
            str(config_path),
            "--python",
            str(venv_python),
            "--neo4j-home",
            str(neo4j_home),
            "--java-home",
            str(java_home),
        ],
        env=environ,
        label="memory runtime binding",
    )


def _default_doctor_runner(
    config_path: Path,
    venv_python: Path,
    *,
    environ: Mapping[str, str],
    repo_root: Path | None,
    hm_home: Path,
) -> int:
    env = dict(environ)
    env["HOMEMASTER_REPO_ROOT"] = str(repo_root) if repo_root is not None else str(hm_home)
    if venv_python.is_file() and os.access(venv_python, os.X_OK):
        proc = subprocess.run(
            [str(venv_python), "-m", "homemaster.cli", "doctor", "--config", str(config_path)],
            env=env,
            check=False,
        )
        return proc.returncode
    from homemaster.cli.doctor import render_doctor_text, run_doctor

    report = run_doctor(live=False, config_path=config_path)
    print(render_doctor_text(report))
    return 1 if report.has_failures else 0


def setup_runtime(
    *,
    runtime_root: Path | None = None,
    config_path: Path | None = None,
    offline_bundle: Path | None = None,
    offline_only: bool = False,
    full: bool = True,
    rebuild: bool = False,
    with_browser: bool = False,
    skip_doctor: bool = False,
    python_installer: Callable[[Path], int] | None = None,
    doctor_runner: Callable[[Path, Path], int] | None = None,
    echo: Callable[[str], None] = print,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Provision the HomeMaster runtime. Returns a process exit code."""

    env: Mapping[str, str] = os.environ if environ is None else environ
    hm_home = _homemaster_home(env)

    try:
        platform_key = _current_platform()
    except RuntimeError as exc:
        detail = str(exc)
        if "Darwin" in detail:
            raise SetupError("HomeMaster setup on macOS requires Apple Silicon (arm64)") from exc
        raise SetupError("HomeMaster setup requires Linux x86_64 or macOS arm64") from exc
    echo(f"[OK] platform: {platform_key}")

    runtime_root = (runtime_root or hm_home / "runtime").expanduser().resolve()
    repo_root = _detect_repo_checkout(runtime_root)
    echo(f"[OK] runtime root: {runtime_root}" + (" (repo checkout)" if repo_root else ""))

    if offline_bundle is not None:
        _verify_offline_bundle(offline_bundle.expanduser().resolve())
        echo(f"[OK] offline bundle: {offline_bundle} (SHA256SUMS verified)")

    lock = _load_lock(repo_root)
    uv = _find_uv(env, lock)
    echo(f"[OK] uv: {uv}")

    venv = runtime_root / "venv"
    venv_python = venv / "bin" / "python"
    child_env = _child_env(env, offline_bundle=offline_bundle, offline_only=offline_only)
    if venv_python.is_file() and not rebuild:
        echo(f"[SKIP] venv: {venv} already exists (use --rebuild to recreate)")
    else:
        if rebuild and venv.exists():
            shutil.rmtree(venv)
        venv.parent.mkdir(parents=True, exist_ok=True)
        installer = python_installer or _default_python_installer(
            uv=uv,
            venv=venv,
            repo_root=repo_root,
            full=full,
            with_browser=with_browser,
            environ=child_env,
        )
        code = installer(venv)
        if code:
            raise SetupError(f"runtime venv build failed (exit {code}): {venv}")
        echo(f"[OK] venv: {venv}")

    neo4j_home = Path(env.get("HOMEMASTER_NEO4J_HOME") or runtime_root / "neo4j")
    java_home = Path(env.get("HOMEMASTER_JAVA_HOME") or runtime_root / "java")
    if full:
        missing = [
            path
            for path, probe in (
                (java_home, java_home / "bin" / "java"),
                (neo4j_home, neo4j_home / "bin" / "neo4j"),
            )
            if not probe.is_file()
        ]
        if missing and offline_only and offline_bundle is None:
            raise SetupError(
                "offline setup cannot download runtime assets "
                f"({', '.join(str(p) for p in missing)} missing); pass --offline-bundle PATH"
            )
        ensure_java(repo_root, java_home, offline_bundle=offline_bundle)
        echo(f"[OK] java: {java_home}")
        ensure_neo4j(repo_root, neo4j_home, offline_bundle=offline_bundle)
        echo(f"[OK] neo4j: {neo4j_home}")
    else:
        echo(
            "[SKIP] assets: files tier installs no Neo4j/Java "
            "(run `homemaster setup --full` later)"
        )

    target_config = config_path
    if target_config is None:
        target_config = (
            repo_root / "config" / "homemaster.yaml"
            if repo_root is not None
            else hm_home / "config.yaml"
        )
    target_config = target_config.expanduser().resolve()
    _ensure_config(
        config_path=target_config,
        full=full,
        repo_root=repo_root,
        runtime_root=runtime_root,
        hm_home=hm_home,
        echo=echo,
    )

    data_root = (
        (repo_root / ".runtime" / "memory")
        if repo_root is not None
        else (hm_home / "memory")
    )
    data_root.mkdir(parents=True, exist_ok=True)

    if repo_root is not None and full:
        _bind_repo_runtime(
            repo_root=repo_root,
            config_path=target_config,
            venv_python=venv_python,
            neo4j_home=neo4j_home,
            java_home=java_home,
            environ=child_env,
        )
        echo("[OK] runtime binding: .runtime/{memory,neo4j,java,venv}")

    state = {"browser": bool(with_browser)}
    runtime_root.mkdir(parents=True, exist_ok=True)
    (runtime_root / "setup-state.json").write_text(
        json.dumps(state, separators=(",", ":")) + "\n", encoding="utf-8"
    )

    if with_browser:
        _run(
            [str(venv_python), "-m", "playwright", "install", "chromium"],
            env=child_env,
            label="playwright install chromium",
        )
        echo("[OK] browser: playwright chromium")

    smoke = "import homemaster, mindmemos" if full else "import homemaster"
    if venv_python.is_file():
        _run([str(venv_python), "-c", smoke], env=child_env, label="runtime import smoke")
        echo(f"[OK] imports: {smoke}")

    if skip_doctor:
        echo("[SKIP] doctor: --no-doctor")
        echo(
            f"Next: edit providers in {target_config} "
            "(or `homemaster auth`), then `homemaster serve`."
        )
        return 0

    if doctor_runner is not None:
        code = doctor_runner(target_config, venv_python)
    else:
        code = _default_doctor_runner(
            target_config, venv_python, environ=env, repo_root=repo_root, hm_home=hm_home
        )
    if code:
        echo(f"[FAIL] doctor reported failures (exit {code})")
        return code
    echo(f"Next: `homemaster serve` (config: {target_config})")
    return 0


__all__ = [
    "SetupError",
    "ensure_java",
    "ensure_neo4j",
    "setup_runtime",
    "_current_platform",
    "_load_lock",
    "_sha256",
]
