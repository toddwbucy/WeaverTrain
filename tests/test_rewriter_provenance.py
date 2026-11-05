"""REWRITE-R1-E4: imported code and producer commits must name the launch checkout."""

import copy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rewriter_provenance as provenance
import rewriter_r1_e4 as e4
from tests.test_rewriter_run_command import commit_fixture


@pytest.fixture
def imports(tmp_path):
    """REWRITE-R1-E4: use tracked synthetic modules with real Git identities."""
    repo = tmp_path / "imports"
    modules = {}
    for name, relative in (("weaver_train", "src/weaver_train/__init__.py"),
            ("weaver_train.manifest", "src/weaver_train/manifest.py"),
            ("rewrite_fixture", "scripts/rewrite_fixture.py")):
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Synthetic module identity.\n")
        modules[name] = SimpleNamespace(__file__=str(path))
    commit_fixture(repo)
    state = provenance.capture(repo, modules=modules)
    return SimpleNamespace(repo=repo, modules=modules, state=state)


def test_import_capture_and_recheck_preserve_all_loaded_sources(imports):
    value = imports
    assert set(value.state["modules"]) == set(value.modules)
    assert value.state["weaver_train_file"] == value.modules["weaver_train"].__file__
    assert provenance.capture(value.repo, modules=value.modules, previous=value.state) == value.state
    provenance.verify_pair({"imports_before": value.state, "imports_after": value.state},
                           value.repo, value.state["commit"])


@pytest.mark.parametrize("name", ["weaver_train", "weaver_train.manifest", "rewrite_fixture"])
def test_other_checkout_import_is_rejected(imports, tmp_path, name):
    value = imports
    other = tmp_path / "other.py"
    other.write_text("# Synthetic module identity.\n")
    value.modules[name].__file__ = str(other)
    with pytest.raises(e4.score.ScoreError, match="repository import comes from another checkout"):
        provenance.capture(value.repo, modules=value.modules)


def test_module_without_location_is_rejected(imports):
    value = imports
    value.modules["rewrite_fixture"].__file__ = None
    with pytest.raises(e4.score.ScoreError, match="repository module has no source location"):
        provenance.capture(value.repo, modules=value.modules)


def test_missing_required_import_is_rejected(imports):
    value = imports
    value.modules.pop("weaver_train.manifest")
    with pytest.raises(e4.score.ScoreError, match="required repository imports are absent"):
        provenance.capture(value.repo, modules=value.modules)


def test_changed_module_bytes_are_rejected_against_the_launch_blob(imports):
    value = imports
    entry = value.state["modules"]["rewrite_fixture"]
    Path(entry["path"]).write_text("# Changed source bytes.\n")
    with pytest.raises(e4.score.ScoreError, match="repository module hash differs from launch commit"):
        provenance.checked_file(value.repo, value.state["commit"]["rev"], "rewrite_fixture",
                                entry["path"], entry["relative"])


def test_new_clean_commit_during_procedure_is_rejected(imports):
    value = imports
    Path(value.modules["rewrite_fixture"].__file__).write_text("# Next committed source.\n")
    commit_fixture(value.repo)
    with pytest.raises(e4.score.ScoreError, match="repository imports changed during the procedure"):
        provenance.capture(value.repo, modules=value.modules, previous=value.state)


@pytest.mark.parametrize("change", ["dirty", "absent", "different", "unknown"])
def test_training_producer_must_name_the_clean_launch(imports, change):
    commit = imports.state["commit"]
    altered = copy.deepcopy(commit)
    if change == "absent":
        altered = None
    elif change == "dirty":
        altered["dirty"] = True
    elif change == "unknown":
        altered["rev"] = None
    else:
        altered["rev"] = "f" * 40
    with pytest.raises(e4.score.ScoreError, match="training producer commit differs from clean launch"):
        e4.producer_commit(SimpleNamespace(commit=altered), commit)


@pytest.mark.parametrize("change", ["dirty", "unknown"])
def test_launch_identity_must_be_known_and_clean(imports, monkeypatch, change):
    commit = copy.deepcopy(imports.state["commit"])
    commit["dirty" if change == "dirty" else "rev"] = True if change == "dirty" else None
    monkeypatch.setattr(provenance.mf, "git_commit", lambda repo: commit)
    with pytest.raises(e4.score.ScoreError, match="a known clean launch commit is required"):
        provenance.clean_commit(imports.repo)


@pytest.mark.parametrize("change, reason", [
    (lambda state: state["commit"].update(rev="f" * 40), "import record launch commit differs"),
    (lambda state: state["modules"].pop("weaver_train.manifest"), "import record lacks required"),
    (lambda state: state["modules"]["rewrite_fixture"].update(relative="scripts/absent.py"),
     "import record names an untracked module"),
    (lambda state: state["modules"]["rewrite_fixture"].update(sha256="f" * 64),
     "retained module hash differs"),
    (lambda state: state.update(weaver_train_file="wrong"), "retained package location differs")])
def test_retained_import_proof_is_verified(imports, change, reason):
    state = copy.deepcopy(imports.state)
    change(state)
    with pytest.raises(e4.score.ScoreError, match=reason):
        provenance.verify_record(state, imports.repo, imports.state["commit"])


def test_retained_after_snapshot_cannot_drop_an_import(imports):
    before = imports.state
    after = copy.deepcopy(before)
    after["modules"].pop("rewrite_fixture")
    with pytest.raises(e4.score.ScoreError, match="retained imports changed during the procedure"):
        provenance.verify_pair({"imports_before": before, "imports_after": after},
                               imports.repo, before["commit"])
