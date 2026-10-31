"""Start vLLM for the STE100 data conversion, from vllm-serve/serve.toml.

    vllm-serve/.venv/bin/python vllm-serve/serve.py [--set engine.max_num_seqs=8] [--print]

Runs in the vLLM environment (vllm-serve/.venv), never the training one. Before serving it
verifies the full model tree against manifests/models/<model>.json, as the loader
does, then writes the effective configuration to
$WEAVER_TRAIN_STATE_DIR/vllm/serve-<UTC>.json and replaces itself with `vllm serve`.
GPU selection comes from .env (CUDA_DEVICE_ORDER, CUDA_VISIBLE_DEVICES), which the
caller exports.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from weaver_train import manifest as mf  # noqa: E402  (stdlib only)


def coerce(value: str):
    for conv in (int, float):
        try:
            return conv(value)
        except ValueError:
            pass
    return {"true": True, "false": False}.get(value.lower(), value)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=str(REPO / "vllm-serve" / "serve.toml"))
    ap.add_argument("--set", action="append", default=[], metavar="engine.KEY=VALUE",
                    help="override one engine value for this launch (recorded)")
    ap.add_argument("--print", action="store_true", help="print the command and exit")
    a = ap.parse_args(argv)

    cfg = tomllib.loads(Path(a.config).read_text())
    for item in a.set:
        key, sep, value = item.partition("=")
        section, _, name = key.partition(".")
        if not sep or section != "engine" or not name:
            ap.error(f"--set takes engine.KEY=VALUE, got {item!r}")
        cfg["engine"][name] = coerce(value)

    spec = tomllib.loads((REPO / "config" / "models.toml").read_text())["models"][cfg["model"]]
    model_dir = Path(os.environ["WEAVER_TRAIN_MODEL_STORE"]) / spec["local_dir"]
    m = mf.load_tree(REPO / "manifests" / "models" / f"{cfg['model']}.json")
    if not a.print:
        print(f"[serve] verifying {m.name} ({len(m.files)} files) against its manifest", flush=True)
        mf.require_tree(m, model_dir)

    cmd = [str(Path(sys.executable).with_name("vllm")), "serve", str(model_dir),
           "--served-model-name", cfg["served_model_name"], "--host", cfg["host"], "--port", str(cfg["port"])]
    for key, value in cfg["engine"].items():
        flag = "--" + key.replace("_", "-")
        if value is True:
            cmd.append(flag)
        elif value is False:
            cmd.append("--no-" + key.replace("_", "-"))
        else:
            cmd += [flag, str(value)]

    record = {"started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "engine": "vllm", "model": {"name": m.name, "tree_hash": m.tree_hash,
                                          "revision": spec["revision"]},
              "config": cfg, "overrides": a.set, "command": cmd[1:],
              "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
              "commit": mf.git_commit(REPO)}
    if a.print:
        print(json.dumps(record, indent=1))
        return 0
    state = Path(os.environ.get("WEAVER_TRAIN_STATE_DIR") or REPO / "var")
    state = state if state.is_absolute() else REPO / state
    out = state / "vllm" / f"serve-{record['started_at'].replace(':', '').replace('+0000', 'Z')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=1) + "\n")
    print(f"[serve] config recorded: {out}", flush=True)
    os.execv(cmd[0], cmd)


if __name__ == "__main__":
    sys.exit(main())
