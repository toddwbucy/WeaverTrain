"""scripts/ste_kit_sample.py: one segment per trajectory uuid, even where the dataset
repeats a uuid on several rows."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("ste_kit_sample", SCRIPTS / "ste_kit_sample.py")
ks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ks)


def row(uuid, text):
    return {"uuid": uuid, "messages": [
        {"role": "user", "content": "bug"},
        {"role": "assistant", "content": text, "tool_calls": []},
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "finish", "arguments": json.dumps({"message": "Done."})}}]}]}


def test_repeated_uuid_gives_one_segment(tmp_path, monkeypatch):
    rows = [row("u1", f"Let me look at the parser, part {i}.") for i in range(5)] + [row("u2", "Let me look.")]
    (tmp_path / "d.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    monkeypatch.setenv("WEAVER_TRAIN_DATA_STORE", str(tmp_path))
    monkeypatch.setattr(ks.tomllib, "loads", lambda s: {"datasets": {"ds": {"local_dir": "."}}})
    monkeypatch.setattr(ks.mf, "load_tree", lambda p: SimpleNamespace(files=[SimpleNamespace(path="d.jsonl")]))
    out = tmp_path / "kit.jsonl"
    assert ks.main(["--dataset", "ds", "--out", str(out), "--n", "2", "--final", "0", "--mix", "1,0,0"]) == 0
    got = [json.loads(line)["uuid"] for line in open(out)]
    assert sorted(got) == ["u1", "u2"]
