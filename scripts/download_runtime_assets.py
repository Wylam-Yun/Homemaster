#!/usr/bin/env python3
"""Fetch and install locked HomeMaster runtime assets."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_neo4j(repo_root: Path, destination: Path, *, offline_bundle: Path | None = None) -> Path:
    if (destination / "bin" / "neo4j").is_file() and (destination / "bin" / "neo4j-admin").is_file():
        return destination
    lock = json.loads((repo_root / "config" / "runtime-assets.lock.json").read_text(encoding="utf-8"))
    asset = lock["neo4j"]
    archive = (offline_bundle / "assets" / Path(asset["url"]).name) if offline_bundle else None
    if archive is None or not archive.is_file():
        if offline_bundle:
            raise RuntimeError(f"offline Neo4j asset is missing: {archive}")
        archive = Path(tempfile.mkstemp(prefix="homemaster-neo4j-", suffix=".tar.gz")[1])
        try:
            subprocess.run(["curl", "--fail", "--location", "--retry", "3", "--output", str(archive), asset["url"]], check=True)
        except Exception:
            archive.unlink(missing_ok=True)
            raise
    try:
        actual = _sha256(archive)
        if actual != asset["sha256"]:
            raise RuntimeError(f"Neo4j SHA256 mismatch: expected {asset['sha256']}, got {actual}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=destination.parent) as temp:
            root = Path(temp)
            with tarfile.open(archive, "r:gz") as package:
                members = package.getmembers()
                for member in members:
                    target = (root / member.name).resolve()
                    if root.resolve() not in target.parents:
                        raise RuntimeError(f"unsafe Neo4j archive member: {member.name}")
                package.extractall(root)
            extracted = root / f"neo4j-community-{asset['version']}"
            if not (extracted / "bin" / "neo4j").is_file():
                raise RuntimeError("Neo4j archive has unexpected layout")
            for name in ("data", "logs", "run"):
                shutil.rmtree(extracted / name, ignore_errors=True)
            if destination.exists():
                raise RuntimeError(f"runtime destination already exists but is incomplete: {destination}")
            os.replace(extracted, destination)
    finally:
        if not offline_bundle:
            archive.unlink(missing_ok=True)
    return destination


def ensure_java(repo_root: Path, destination: Path, *, offline_bundle: Path | None = None) -> Path:
    if (destination / "bin" / "java").is_file():
        return destination
    lock = json.loads((repo_root / "config" / "runtime-assets.lock.json").read_text(encoding="utf-8"))
    asset = lock["java"]
    archive = (offline_bundle / "assets" / Path(asset["url"]).name) if offline_bundle else None
    if archive is None or not archive.is_file():
        if offline_bundle:
            raise RuntimeError(f"offline Java asset is missing: {archive}")
        archive = Path(tempfile.mkstemp(prefix="homemaster-java-", suffix=".tar.gz")[1])
        try:
            subprocess.run(["curl", "--fail", "--location", "--retry", "3", "--output", str(archive), asset["url"]], check=True)
        except Exception:
            archive.unlink(missing_ok=True)
            raise
    try:
        actual = _sha256(archive)
        if actual != asset["sha256"]:
            raise RuntimeError(f"Java SHA256 mismatch: expected {asset['sha256']}, got {actual}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=destination.parent) as temp:
            root = Path(temp)
            with tarfile.open(archive, "r:gz") as package:
                members = package.getmembers()
                for member in members:
                    target = (root / member.name).resolve()
                    if root.resolve() not in target.parents:
                        raise RuntimeError(f"unsafe Java archive member: {member.name}")
                package.extractall(root)
            candidates = [p for p in root.iterdir() if p.is_dir() and (p / "bin" / "java").is_file()]
            if len(candidates) != 1:
                raise RuntimeError("Java archive has unexpected layout")
            if destination.exists():
                raise RuntimeError(f"runtime destination already exists but is incomplete: {destination}")
            os.replace(candidates[0], destination)
    finally:
        if not offline_bundle:
            archive.unlink(missing_ok=True)
    return destination


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--java-destination", type=Path)
    parser.add_argument("--offline-bundle", type=Path)
    args = parser.parse_args()
    if args.java_destination:
        ensure_java(args.repo_root.resolve(), args.java_destination.resolve(), offline_bundle=args.offline_bundle)
    ensure_neo4j(args.repo_root.resolve(), args.destination.resolve(), offline_bundle=args.offline_bundle)
