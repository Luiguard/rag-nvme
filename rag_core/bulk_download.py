"""Bulk-Download-Helfer für GUI und CLI."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from .bulk_sources import GIT_SOURCES, STACKEXCHANGE_DUMPS
from .gui_resources import child_env, low_priority_cmd


LogFn = Callable[[str], None]
RunningFn = Callable[[], bool]


def _run_git_clone(
    url: str,
    target: Path,
    log: LogFn,
    running: RunningFn,
    sparse: str | None = None,
) -> bool:
    if target.exists() and any(target.iterdir()):
        return True
    target.parent.mkdir(parents=True, exist_ok=True)
    if sparse:
        log(f"  Sparse-Clone ({sparse})…")
        cmds = [
            ["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse", url, str(target)],
            ["git", "-C", str(target), "sparse-checkout", "set", sparse],
        ]
        for cmd in cmds:
            if not running():
                return False
            r = subprocess.run(
                low_priority_cmd(cmd), capture_output=True, text=True, env=child_env()
            )
            if r.returncode != 0:
                log(f"  ⚠️ git: {r.stderr[:200]}")
                return False
        return True
    log(f"  git clone {url} …")
    r = subprocess.run(
        low_priority_cmd(["git", "clone", "--depth", "1", url, str(target)]),
        capture_output=True,
        text=True,
        env=child_env(),
    )
    if r.returncode != 0:
        log(f"  ⚠️ {r.stderr[:300]}")
        return False
    return True


def download_all_git(data_dir: Path, log: LogFn, running: RunningFn) -> int:
    ok = 0
    log("\n[GIT – Offizielle Docs, OWASP, Arch Wiki]")
    for src in GIT_SOURCES:
        if not running():
            break
        target = data_dir / src["subdir"]
        marker = target / src["marker"]
        if marker.exists():
            log(f"✅ {src['name']} vorhanden")
            ok += 1
            continue
        log(f"⬇️  {src['name']}…")
        if _run_git_clone(src["url"], target, log, running, src.get("sparse")):
            log(f"✅ {src['name']}")
            ok += 1
    return ok


def download_stackexchange_dump(
    data_dir: Path,
    dump: dict,
    log: LogFn,
    running: RunningFn,
) -> Path | None:
    se_dir = data_dir / "stackexchange"
    se_dir.mkdir(parents=True, exist_ok=True)
    archive = se_dir / dump["file"]
    extract_dir = se_dir / dump["site"]

    if (extract_dir / "Posts.xml").exists():
        log(f"✅ {dump['name']} bereits extrahiert")
        return extract_dir

    if not archive.exists():
        log(f"⬇️  {dump['name']} ({dump['size']}) …")
        r = subprocess.run(
            ["wget", "-c", "-q", "--show-progress", "-O", str(archive), dump["url"]],
            capture_output=False,
        )
        if r.returncode != 0 or not archive.exists():
            log(f"⚠️ Download fehlgeschlagen: {dump['name']}")
            return None

    if not running():
        return None

    log(f"📦 Extrahiere {dump['name']} (7z)…")
    extract_dir.mkdir(parents=True, exist_ok=True)
    any_tool_found = False
    r = None
    try:
        r = subprocess.run(
            ["7z", "x", str(archive), f"-o{extract_dir}", "-y"],
            capture_output=True,
            text=True,
        )
        any_tool_found = True
    except FileNotFoundError:
        pass

    if r is None or r.returncode != 0:
        try:
            r = subprocess.run(
                ["7za", "x", str(archive), f"-o{extract_dir}", "-y"],
                capture_output=True,
                text=True,
            )
            any_tool_found = True
        except FileNotFoundError:
            pass

    if r is None or r.returncode != 0:
        if any_tool_found:
            log(f"⚠️ Entpacken fehlgeschlagen für {dump['name']} (Archiv beschädigt?)")
            if r:
                log(f"   Details: {r.stderr[:200]}")
            log(f"🗑️ Lösche beschädigtes Archiv: {archive.name}")
            try:
                archive.unlink()
            except Exception:
                pass
        else:
            log(f"⚠️ 7z fehlt? sudo apt install p7zip-full")
        return None
    log(f"✅ {dump['name']} bereit unter {extract_dir}")
    return extract_dir


def download_all_stackexchange(
    data_dir: Path,
    log: LogFn,
    running: RunningFn,
    *,
    max_sites: int = 0,
) -> int:
    log("\n[STACK EXCHANGE – Archive.org Dumps]")
    done = 0
    dumps = STACKEXCHANGE_DUMPS[:max_sites] if max_sites else STACKEXCHANGE_DUMPS
    for dump in dumps:
        if not running():
            break
        if download_stackexchange_dump(data_dir, dump, log, running):
            done += 1
    return done


def _clone_git_source(src: dict, data_dir: Path, log: LogFn) -> bool:
    target = data_dir / src["subdir"]
    return _run_git_clone(
        src["url"],
        target,
        log,
        lambda: True,
        src.get("sparse"),
    )

