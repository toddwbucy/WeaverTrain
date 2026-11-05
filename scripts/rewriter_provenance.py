"""REWRITE-R1-E4: bind imported repository code to the clean launch checkout."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from weaver_train import manifest as mf

import rewriter_score as score


def clean_commit(repo):
    """REWRITE-R1-E4: require a known and clean launch identity."""
    commit = mf.git_commit(repo)
    score.require(isinstance(commit.get("rev"), str) and bool(commit["rev"]) and
                  commit.get("dirty") is False,
                  "REWRITE-R1-E4: a known clean launch commit is required")
    return commit


def module_paths(repo):
    """REWRITE-R1-E4: derive import names from tracked Python source files."""
    names = subprocess.check_output(
        ["git", "-C", str(repo), "ls-files", "-z", "src", "scripts"]).split(b"\0")
    paths = {}
    for raw in names:
        name = raw.decode()
        if not name.endswith(".py"):
            continue
        path = Path(name)
        if path.parts[0] == "src":
            parts = list(path.with_suffix("").parts[1:])
            if parts[-1] == "__init__":
                parts.pop()
            module = ".".join(parts)
        else:
            module = path.stem
        paths[module] = name
    return paths


def checked_file(repo, rev, name, path, relative):
    """REWRITE-R1-E4: check an import location and its committed source bytes."""
    root = Path(repo).resolve()
    expected = root / relative
    score.require(Path(path).resolve() == expected,
                  "REWRITE-R1-E4: repository import comes from another checkout")
    raw = subprocess.check_output(["git", "-C", str(root), "show", f"{rev}:{relative}"])
    digest = mf.sha256_file(expected)
    score.require(digest == score.build.digest(raw),
                  "REWRITE-R1-E4: repository module hash differs from launch commit")
    return {"path": str(expected), "relative": relative, "sha256": digest}


def capture(repo, *, previous=None, modules=None):
    """REWRITE-R1-E4: capture all loaded repository modules before and after work."""
    commit = clean_commit(repo)
    paths = module_paths(repo)
    loaded = dict(sys.modules if modules is None else modules)
    entries = {}
    for name, module in loaded.items():
        path = getattr(module, "__file__", None)
        relative = paths.get(name)
        if name == "__main__" and path:
            relative = next((value for value in paths.values()
                             if Path(path).resolve() == Path(repo).resolve() / value), None)
        if relative is not None:
            score.require(isinstance(path, str),
                          "REWRITE-R1-E4: repository module has no source location")
            entries[name] = checked_file(repo, commit["rev"], name, path, relative)
    score.require({"weaver_train", "weaver_train.manifest"} <= entries.keys(),
                  "REWRITE-R1-E4: required repository imports are absent")
    state = {"commit": commit, "modules": entries,
             "weaver_train_file": entries["weaver_train"]["path"]}
    if previous is not None:
        score.require(previous["commit"] == commit and
                      all(entries.get(name) == item
                          for name, item in previous["modules"].items()),
                      "REWRITE-R1-E4: repository imports changed during the procedure")
    return state


def verify_record(state, repo, commit):
    """REWRITE-R1-E4: check a retained import proof without importing its modules."""
    score.require(state.get("commit") == commit and commit.get("dirty") is False and
                  isinstance(commit.get("rev"), str) and bool(commit["rev"]),
                  "REWRITE-R1-E4: import record launch commit differs")
    entries = state.get("modules", {})
    paths = module_paths(repo)
    score.require({"weaver_train", "weaver_train.manifest"} <= entries.keys(),
                  "REWRITE-R1-E4: import record lacks required repository modules")
    for name, item in entries.items():
        relative = item["relative"]
        score.require(paths.get(name) == relative or
                      (name == "__main__" and relative in paths.values()),
                      "REWRITE-R1-E4: import record names an untracked module")
        expected = checked_file(repo, commit["rev"], name, item["path"], relative)
        score.require(item == expected,
                      "REWRITE-R1-E4: retained module hash differs")
    score.require(state.get("weaver_train_file") == entries["weaver_train"]["path"],
                  "REWRITE-R1-E4: retained package location differs")


def verify_pair(record, repo, commit):
    """REWRITE-R1-E4: verify both import snapshots and their unchanged overlap."""
    before, after = record["imports_before"], record["imports_after"]
    verify_record(before, repo, commit)
    verify_record(after, repo, commit)
    score.require(all(after["modules"].get(name) == value
                      for name, value in before["modules"].items()),
                  "REWRITE-R1-E4: retained imports changed during the procedure")
