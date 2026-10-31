import hashlib
import json

import pytest

from weaver_train import manifest as mf


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "tree"
    (root / "sub").mkdir(parents=True)
    (root / "a.txt").write_text("alpha\n")
    (root / "sub" / "b.bin").write_bytes(bytes(range(256)) * 100)
    (root / ".cache").mkdir()
    (root / ".cache" / "ignored").write_text("download bookkeeping")
    return root


def test_build_lists_files_and_ignores_cache(tree):
    m = mf.build_tree(tree, "model", "t")
    assert [f.path for f in m.files] == ["a.txt", "sub/b.bin"]
    assert m.files[0].sha256 == hashlib.sha256(b"alpha\n").hexdigest()
    assert m.total_bytes == 6 + 25600


def test_tree_hash_is_sha256sum_style(tree):
    m = mf.build_tree(tree, "model", "t")
    lines = "".join(f"{f.sha256}  {f.path}\n" for f in sorted(m.files, key=lambda f: f.path))
    assert m.tree_hash == hashlib.sha256(lines.encode()).hexdigest()


def test_roundtrip_and_verify_clean(tree, tmp_path):
    m = mf.build_tree(tree, "model", "t", source={"repo_id": "x/y", "revision": "abc"})
    p = tmp_path / "m.json"
    m.save(p)
    loaded = mf.load_tree(p)
    assert loaded.tree_hash == m.tree_hash and loaded.source["revision"] == "abc"
    assert mf.verify_tree(loaded, tree) == []
    assert mf.require_tree(loaded, tree) == m.tree_hash


@pytest.mark.parametrize("damage, expect", [
    (lambda r: (r / "a.txt").write_text("alphA\n"), "sha256 mismatch: a.txt"),
    (lambda r: (r / "a.txt").write_text("longer\n"), "size mismatch: a.txt"),
    (lambda r: (r / "a.txt").unlink(), "missing: a.txt"),
    (lambda r: (r / "extra").write_text("x"), "unexpected: extra"),
])
def test_verify_detects_damage(tree, damage, expect):
    m = mf.build_tree(tree, "model", "t")
    damage(tree)
    problems = mf.verify_tree(m, tree)
    assert any(expect in p for p in problems), problems
    with pytest.raises(mf.ManifestError):
        mf.require_tree(m, tree)


def test_tampered_manifest_rejected(tree, tmp_path):
    m = mf.build_tree(tree, "model", "t")
    d = json.loads(m.to_json())
    d["files"][0]["sha256"] = "0" * 64  # file list edited, stored tree_hash left alone
    p = tmp_path / "m.json"
    p.write_text(json.dumps(d))
    with pytest.raises(mf.ManifestError, match="tree_hash"):
        mf.load_tree(p)


def test_symlink_refused(tree):
    (tree / "link").symlink_to(tree / "a.txt")
    with pytest.raises(mf.ManifestError, match="symlink"):
        mf.build_tree(tree, "model", "t")


def _rec(**kw):
    base = dict(kind="bench", model={"name": "m", "tree_hash": "h" * 64},
                snapshot={"name": "s", "tree_hash": "s" * 64}, seed=1)
    base.update(kw)
    return mf.RunRecord(**base)


def test_run_record_roundtrip(tmp_path):
    r = _rec(sampler={"temperature": 0.0}, repetition=2)
    p = tmp_path / "run.json"
    r.save(p)
    back = mf.load_run(p)
    assert back == r
    assert back.engine == "python-hf"
    assert back.commit.keys() == {"rev", "dirty"}


@pytest.mark.parametrize("kw, msg", [
    ({"engine": "weaver"}, "engine"),
    ({"precision": "fp16"}, "precision"),
    ({"kind": "eval"}, "kind"),
    ({"model": {"name": "m"}}, "model.tree_hash"),
    ({"snapshot": None}, "snapshot.tree_hash"),
    ({"seed": "1"}, "seed"),
])
def test_run_record_validation(kw, msg):
    with pytest.raises(mf.ManifestError, match=msg):
        _rec(**kw).to_json()


def test_train_record_needs_no_snapshot():
    mf.RunRecord(kind="train", model={"name": "m", "tree_hash": "h"}, seed=0).validate()


def test_template_is_python_hf():
    t = mf.run_template()
    assert t["engine"] == "python-hf" and t["precision"] == "bf16"
    assert {"model", "snapshot", "seed", "sampler", "commit"} <= t.keys()


def test_cli_build_verify(tree, tmp_path, capsys):
    out = tmp_path / "m.json"
    assert mf.main(["build", str(tree), "--kind", "snapshot", "--name", "s", "--out", str(out)]) == 0
    assert mf.main(["verify", str(out), str(tree)]) == 0
    (tree / "a.txt").write_text("changed\n")
    assert mf.main(["verify", str(out), str(tree)]) == 1
