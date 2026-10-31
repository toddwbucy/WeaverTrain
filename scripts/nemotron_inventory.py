"""Inventory every dataset NVIDIA lists for the pinned Nemotron 3 Nano model.

    python scripts/nemotron_inventory.py [--out var/inventory/nemotron-datasets.json]

Sources, all recorded per dataset:

  * card-metadata   the ``datasets:`` list in the pinned model card's front matter
  * card:<section>  a dataset linked or named in the card's "Training, Testing, and
                    Evaluation Datasets" section (the table it appears in)
  * blend:<id>      a component dataset linked from a training-blend card (the
                    Nemotron 3 Nano RL blend lists its components and mixing ratios)
  * collection:<t>  membership of an NVIDIA Hugging Face collection, read today; the
                    collections are edited after release, so membership alone does not
                    mean the model was trained on a dataset

For every Hugging Face dataset id: license, gating, whether this token can read a data
file (gated repos serve README.md to anyone), total file bytes, row count (datasets-server), created/modified dates, the
revision read, and whether its own dataset card names Nemotron 3 Nano. Read-only: no
dataset files are downloaded except each dataset's README.md, read in memory.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MODEL_KEY = "nemotron-3-nano-30b-a3b"
COLLECTIONS = {
    "post-training": "nvidia/nemotron-post-training-datasets-6939b7b93382bac738eebd17",
    "pre-training": "nvidia/nemotron-pre-training-datasets-689d9de36f84279d83786b35",
    "v3-pre-training": "nvidia/nemotron-v3-pre-training-69401eecd9c3e9aae0c6e7dd",
}
BLENDS = ["nvidia/Nemotron-3-Nano-RL-Training-Blend"]
HF_DS = re.compile(r"https://huggingface\.co/datasets/([\w.-]+/[\w.-]+)")
NANO3 = re.compile(r"nemotron[\s_-]*3[\s_-]*nano|nano[\s_-]*3\b|nemotron[\s_-]*nano[\s_-]*3", re.I)


def model_card() -> tuple[Path, dict]:
    spec = tomllib.loads((REPO / "config" / "models.toml").read_text())["models"][MODEL_KEY]
    return Path(os.environ["WEAVER_TRAIN_MODEL_STORE"]) / spec["local_dir"] / "README.md", spec


def parse_card(text: str) -> tuple[list[str], dict[str, set[str]], list[dict]]:
    """(front-matter dataset ids, {hf id: {card sections}}, named non-HF rows)."""
    front = text.split("---", 2)[1]
    meta: list[str] = []
    in_ds = False
    for line in front.splitlines():
        if line.startswith("datasets:"):
            in_ds = True
            continue
        if in_ds:
            if line.startswith("- "):
                meta.append(line[2:].strip())
            elif line and not line.startswith(" "):
                in_ds = False
    start = text.index("# Training, Testing, and Evaluation Datasets")
    stop = text.index("## Training Dataset", start)
    section = "datasets (public)"
    linked: dict[str, set[str]] = {}
    named: list[dict] = []
    for line in text[start:stop].splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        if not line.startswith("|") or set(line) <= set("|:- "):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if cells[0] in ("Dataset",):
            continue
        ids = HF_DS.findall(line)
        for i in ids:
            linked.setdefault(i, set()).add(section)
        first_ids = HF_DS.findall(cells[0])
        if not first_ids:
            name = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cells[0]).replace("\\", "")
            url = re.findall(r"\]\((https?://[^)]+)\)", cells[0])
            named.append({"name": name, "section": section, "url": url[0] if url else None,
                          "cells": [re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", c).replace("\\", "")
                                    for c in cells[1:]]})
    return meta, linked, named


def collection_members() -> dict[str, list[str]]:
    from huggingface_hub import HfApi

    api = HfApi(token=False)  # public; the configured token is refused for collections
    return {k: [it.item_id for it in api.get_collection(slug).items if it.item_type == "dataset"]
            for k, slug in COLLECTIONS.items()}


def blend_card(ds_id: str, token: str | None) -> str:
    import httpx

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = httpx.get(f"https://huggingface.co/datasets/{ds_id}/resolve/main/README.md",
                  headers=headers, follow_redirects=True, timeout=60)
    r.raise_for_status()
    return r.text


def probe(ds_id: str, token: str | None) -> dict:
    import httpx
    from huggingface_hub import HfApi

    api = HfApi(token=token)
    rec: dict = {"id": ds_id, "url": f"https://huggingface.co/datasets/{ds_id}"}
    try:
        info = api.dataset_info(ds_id, files_metadata=True)
    except Exception as e:  # noqa: BLE001 - record and continue: the inventory lists failures
        rec["error"] = f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"
        return rec
    card = info.card_data.to_dict() if info.card_data else {}
    lic = card.get("license") or next((t.split(":", 1)[1] for t in info.tags or [] if t.startswith("license:")), None)
    rec.update({
        "revision": info.sha,
        "created_at": info.created_at.date().isoformat() if info.created_at else None,
        "last_modified": info.last_modified.date().isoformat() if info.last_modified else None,
        "gated": info.gated or False,
        "license": lic if not isinstance(lic, list) else ", ".join(lic),
        "license_name": card.get("license_name"),
        "files": len(info.siblings or []),
        "bytes": sum((s.size or 0) for s in info.siblings or []),
    })
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    # A gated repo serves README.md to anyone, so access is tested on a data file
    # (first byte only).
    meta_files = {"README.md", ".gitattributes", "LICENSE", "LICENSE.md"}
    data = next((s.rfilename for s in info.siblings or [] if s.rfilename not in meta_files), None)
    if data is None:
        rec["files_readable"] = None
    else:
        d = httpx.get(f"https://huggingface.co/datasets/{ds_id}/resolve/{info.sha}/{data}",
                      headers={**headers, "Range": "bytes=0-0"}, follow_redirects=True, timeout=60)
        rec["files_readable"] = d.status_code in (200, 206)
        if not rec["files_readable"]:
            rec["data_status"] = d.status_code
    r = httpx.get(f"https://huggingface.co/datasets/{ds_id}/resolve/{info.sha}/README.md",
                  headers=headers, follow_redirects=True, timeout=60)
    if r.status_code == 200:
        m = NANO3.search(r.text)
        rec["card_names_nano3"] = bool(m)
        if m:
            a, b = max(0, m.start() - 100), min(len(r.text), m.end() + 100)
            rec["nano3_context"] = " ".join(r.text[a:b].split())
    else:
        rec["readme_status"] = r.status_code
    try:
        s = httpx.get("https://datasets-server.huggingface.co/size", params={"dataset": ds_id},
                      headers=headers, timeout=60)
        if s.status_code == 200:
            rec["rows"] = s.json().get("size", {}).get("dataset", {}).get("num_rows")
    except httpx.HTTPError:
        pass
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(REPO / "var" / "inventory" / "nemotron-datasets.json"))
    a = ap.parse_args(argv)
    card_path, spec = model_card()
    meta, linked, named = parse_card(card_path.read_text(encoding="utf-8"))
    cols = collection_members()
    ids: dict[str, set[str]] = {}
    for i in meta:
        ids.setdefault(i, set()).add("card-metadata")
    for i, secs in linked.items():
        ids.setdefault(i, set()).update(f"card:{s}" for s in secs)
    for k, members in cols.items():
        for i in members:
            ids.setdefault(i, set()).add(f"collection:{k}")
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_ACCESS_TOKEN")
    for blend in BLENDS:
        for i in sorted(set(HF_DS.findall(blend_card(blend, token)))):
            if i != blend:
                ids.setdefault(i, set()).add(f"blend:{blend}")
    with ThreadPoolExecutor(8) as ex:
        recs = list(ex.map(lambda i: probe(i, token), sorted(ids)))
    for r in recs:
        r["listed_in"] = sorted(ids[r["id"]])
    out = {
        "model": {"repo_id": spec["repo_id"], "revision": spec["revision"]},
        "read_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "collections": COLLECTIONS,
        "hf_datasets": recs,
        "named_only": named,
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(f"{len(recs)} HF datasets, {len(named)} named non-HF rows -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
