"""LoRA data rendering under Spec 0.3 BUILD-RENDER."""

from __future__ import annotations

import json
import fcntl
from contextlib import contextmanager


def render_messages(messages: list[dict]) -> list[dict]:
    """BUILD-RENDER: select chat fields and parse tool argument JSON without mutation."""
    selected = []
    for message in messages:
        message = {k: v for k, v in message.items()
                   if k in ("role", "content", "tool_calls", "tool_call_id", "name")}
        if message.get("tool_calls"):
            message["tool_calls"] = [
                {**call, "function": {
                    **call["function"],
                    "arguments": (json.loads(call["function"]["arguments"])
                                  if isinstance(call["function"].get("arguments"), str)
                                  else call["function"].get("arguments")),
                }} for call in message["tool_calls"]
            ]
        selected.append(message)
    return selected


def render_row(tokenizer, row: dict, *, enable_thinking: bool,
               add_generation_prompt: bool = False) -> list[int]:
    """BUILD-RENDER: render tools and the explicit thinking value in one tokenization."""
    return tokenizer.apply_chat_template(
        render_messages(row["messages"]), tools=row.get("tools"),
        enable_thinking=enable_thinking, add_generation_prompt=add_generation_prompt,
        tokenize=True, return_dict=False,
    )


class BuildError(ValueError):
    """A BUILD clause refused the input. No partial training dataset is published."""


def require_empty_output(out_dir):
    """BUILD-OUTPUT: never replace an existing build or its sibling manifest."""
    if out_dir.exists() and any(out_dir.iterdir()):
        raise BuildError("BUILD-OUTPUT: output directory must be empty")
    if out_dir.with_name(out_dir.name + ".manifest.json").exists():
        raise BuildError("BUILD-OUTPUT: output manifest already exists")


@contextmanager
def output_files(out_dir, names):
    """BUILD-OUTPUT: a failed write removes only this invocation's output files."""
    out_dir = out_dir.resolve()
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    reservation = out_dir.with_name(out_dir.name + ".build.lock")
    # Keep one persistent inode, so closing or terminating its owner releases the lock.
    descriptor = reservation.open("a")
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BuildError("BUILD-OUTPUT: output is reserved by another build") from exc
        require_empty_output(out_dir)
        existed = out_dir.exists()
        out_dir.mkdir(parents=True, exist_ok=True)
        paths = [out_dir / name for name in names]
        paths.append(out_dir / "build.json")
        paths.append(out_dir.with_name(out_dir.name + ".manifest.json"))
        try:
            yield
        except BaseException:
            for path in paths:
                path.unlink(missing_ok=True)
            if not existed:
                out_dir.rmdir()
            raise
    finally:
        descriptor.close()


def read_jsonl(path):
    """BUILD-INPUTS: read each source row without changing its text."""
    with open(path, encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def load_inputs(paths, heldout):
    """BUILD-INPUTS, BUILD-HELDOUT: verify each file and reject row or task overlap."""
    from weaver_train import manifest as mf

    excluded = {row["uuid"] for row in read_jsonl(heldout)}
    rows, inputs, seen = [], [], set()
    for path in paths:
        record = json.loads(path.with_name("run.json").read_text())
        digest = mf.sha256_file(path)
        if digest != record.get("converted_sha256"):
            raise BuildError(f"BUILD-INPUTS: {path.name} sha256 differs from run.json")
        batch = record["batch"]
        inputs.append({"batch": batch, "file": path.name, "sha256": digest})
        for row in read_jsonl(path):
            number = row["ste"]["row"]
            if number in seen:
                raise BuildError(f"BUILD-INPUTS: duplicate dataset row {number}")
            if row["uuid"] in excluded:
                raise BuildError(f"BUILD-HELDOUT: dataset row {number} has a held-out uuid")
            seen.add(number)
            rows.append(row)
    return rows, inputs


def require_prefix(prefix, sequence, row, message, name):
    """BUILD-PREFIX: stop at the first changed token prefix, with its source location."""
    if sequence[:len(prefix)] != prefix:
        raise BuildError(f"BUILD-PREFIX: row {row}, message {message}, {name}")


def render_text(tokenizer, row, messages, *, enable_thinking, generation=False):
    """BUILD-RENDER, BUILD-OUTPUT: the same render provides STE token offsets."""
    return tokenizer.apply_chat_template(
        render_messages(messages), tools=row.get("tools"), enable_thinking=enable_thinking,
        add_generation_prompt=generation, tokenize=False,
    )


def prefix_spans(tokenizer, row, *, enable_thinking):
    """BUILD-PREFIX: check every assistant span before length filtering can remove a row."""
    messages = row["messages"]
    number = row["ste"]["row"]
    ids = render_row(tokenizer, row, enable_thinking=enable_thinking)
    spans = []
    for index, message in enumerate(messages):
        if message["role"] != "assistant":
            continue
        before = {**row, "messages": messages[:index]}
        through = {**row, "messages": messages[:index + 1]}
        prefix = render_row(tokenizer, before, enable_thinking=enable_thinking,
                            add_generation_prompt=True)
        complete = render_row(tokenizer, through, enable_thinking=enable_thinking)
        require_prefix(prefix, complete, number, index, "prompt versus assistant")
        require_prefix(complete, ids, number, index, "assistant versus full row")
        spans.append((index, len(prefix), len(complete)))
    return ids, spans


def tokenize_spans(tokenizer, row, ids, spans, *, enable_thinking):
    """BUILD-MASK, BUILD-OUTPUT: reuse verified spans for labels and retained-row counts."""
    mask = [0] * len(ids)
    for _, start, end in spans:
        mask[start:end] = [1] * (end - start)
    ste_tokens = count_ste_tokens(tokenizer, row, ids, mask, spans,
                                  enable_thinking=enable_thinking)
    return {"input_ids": ids, "assistant_masks": mask, "batch": row["ste"]["batch"],
            "trajectory": row["ste"]["trajectory"], "row": row["ste"]["row"]}, ste_tokens


def tokenize_row(tokenizer, row, *, enable_thinking):
    """BUILD-MASK, BUILD-PREFIX: derive labels only from verified template prefixes."""
    ids, spans = prefix_spans(tokenizer, row, enable_thinking=enable_thinking)
    return tokenize_spans(tokenizer, row, ids, spans, enable_thinking=enable_thinking)


def count_ste_tokens(tokenizer, row, ids, mask, spans, *, enable_thinking):
    """BUILD-OUTPUT: count assistant tokens overlapping converted narration or finish text."""
    text = render_text(tokenizer, row, row["messages"], enable_thinking=enable_thinking)
    encoded = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)
    if encoded["input_ids"] != ids:
        raise BuildError(f"BUILD-OUTPUT: row {row['ste']['row']} offset tokenization differs")
    offsets = encoded["offset_mapping"]
    selected = set()
    for index, start, end in spans:
        message = row["messages"][index]
        texts = [message.get("content") or ""]
        for call in message.get("tool_calls") or []:
            if call["function"]["name"] == "finish":
                args = call["function"]["arguments"]
                args = json.loads(args) if isinstance(args, str) else args
                texts.append(args.get("message") or "")
        lower = offsets[start][0] if start < end else 0
        upper = offsets[end - 1][1] if start < end else 0
        for segment in texts:
            segment = segment.strip()
            if not segment:
                continue
            found = text.find(segment, lower, upper)
            if found < 0:
                raise BuildError(f"BUILD-OUTPUT: row {row['ste']['row']}, message {index}, STE text absent")
            # Count a token once when two text spans share a boundary token.
            selected.update(i for i in range(start, end) if mask[i]
                            and offsets[i][0] < found + len(segment) and offsets[i][1] > found)
            lower = found + len(segment)
    return len(selected)


def template_hash(tokenizer):
    """BUILD-RENDER, RECONCILE-FIELDS: hash the exact selected chat template as UTF-8."""
    import hashlib
    return hashlib.sha256(tokenizer.get_chat_template().encode("utf-8")).hexdigest()


def build_dataset(tokenizer, paths, heldout, out_dir, *, tree_hash, max_seq_length,
                  seed, limit, enable_thinking):
    """BUILD-LENGTH, BUILD-ORDER, BUILD-OUTPUT: write intact rows and a sealed build record."""
    import random
    from weaver_train import manifest as mf

    if enable_thinking is not False:
        raise BuildError("BUILD-RENDER: Spec 0.3 requires enable_thinking=false")
    if max_seq_length <= 0 or limit is not None and limit <= 0:
        raise BuildError("BUILD-ORDER: length and limit must be positive")
    require_empty_output(out_dir)
    rows, inputs = load_inputs(paths, heldout)
    eligible, removed = [], []
    for row in rows:
        ids, spans = prefix_spans(tokenizer, row, enable_thinking=enable_thinking)
        if len(ids) > max_seq_length:
            removed.append({**row["ste"], "tokens": len(ids), "cause": "max_seq_length"})
        else:
            eligible.append((row, ids, spans))
    # BUILD-ORDER: input-file order cannot change the seeded nested subsets.
    eligible.sort(key=lambda item: item[0]["ste"]["row"])
    random.Random(seed).shuffle(eligible)
    # BUILD-OUTPUT counts selected rows after every input has passed BUILD-PREFIX.
    chosen = eligible if limit is None else eligible[:limit]
    selected = [tokenize_spans(tokenizer, row, ids, spans, enable_thinking=enable_thinking)
                for row, ids, spans in chosen]
    record = {
        "inputs": inputs, "tree_hash": tree_hash, "template_hash": template_hash(tokenizer),
        "tools": True, "enable_thinking": enable_thinking, "max_seq_length": max_seq_length,
        "seed": seed, "limit": limit, "render_only": False,
        "heldout_sha256": mf.sha256_file(heldout), "removed": removed,
        "counts": {"rows_in": len(rows), "rows_removed": len(removed), "rows_out": len(selected),
                   "all_tokens": sum(len(row["input_ids"]) for row, _ in selected),
                   "assistant_tokens": sum(sum(row["assistant_masks"]) for row, _ in selected),
                   "assistant_ste_tokens": sum(count for _, count in selected)},
    }
    with output_files(out_dir, ["train.jsonl"]):
        with (out_dir / "train.jsonl").open("w", encoding="utf-8") as stream:
            for row, _ in selected:
                stream.write(json.dumps(row) + "\n")
        write_build_record(out_dir, record)
    return record


def write_build_record(out_dir, record):
    """BUILD-OUTPUT: manifest the build before adding the manifest itself."""
    from weaver_train import manifest as mf

    (out_dir / "build.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    mf.build_tree(out_dir, "dataset", "lora-build").save(
        out_dir.with_name(out_dir.name + ".manifest.json"))


def build_render_only(tokenizer, prompts, out_dir, *, tree_hash, max_seq_length, enable_thinking):
    """BUILD-RENDER-ONLY, RECONCILE-RENDER: save the two prompts and their token IDs."""
    if enable_thinking is not False:
        raise BuildError("BUILD-RENDER: Spec 0.3 requires enable_thinking=false")
    require_empty_output(out_dir)
    if set(prompts) != {"trajectory", "loop"} or prompts["loop"].get("tools"):
        raise BuildError("RECONCILE-RENDER: supply trajectory and loop prompts, loop without tools")
    sequences = {name: render_row(tokenizer, row, enable_thinking=enable_thinking,
                                  add_generation_prompt=True) for name, row in prompts.items()}
    record = {"tree_hash": tree_hash, "template_hash": template_hash(tokenizer),
              "tools": True, "enable_thinking": enable_thinking, "max_seq_length": max_seq_length,
              "render_only": True}
    with output_files(out_dir, ["render.json", "prompts.json"]):
        (out_dir / "render.json").write_text(json.dumps(sequences) + "\n", encoding="utf-8")
        (out_dir / "prompts.json").write_text(json.dumps(prompts) + "\n", encoding="utf-8")
        write_build_record(out_dir, record)
    return record


def main(argv=None):
    """BUILD-INPUTS, BUILD-RENDER-ONLY: verify a configured local model before tokenizer use."""
    import argparse
    import os
    import sys
    import tomllib
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo / "src"))
    from weaver_train import loader, manifest as mf

    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    command = sub.add_parser("build")
    command.add_argument("--converted", type=Path, action="append", default=[])
    command.add_argument("--heldout", type=Path)
    command.add_argument("--model", default="nemotron-3-nano-30b-a3b")
    command.add_argument("--out", type=Path, required=True)
    command.add_argument("--enable-thinking", required=True, choices=["false"])
    command.add_argument("--max-seq-length", type=int, default=36864)
    command.add_argument("--seed", type=int, default=0)
    command.add_argument("--limit", type=int)
    command.add_argument("--render-only", action="store_true")
    command.add_argument("--trajectory-prompt", type=Path)
    command.add_argument("--loop-prompt", type=Path)
    args = ap.parse_args(argv)
    try:
        state_dir = os.environ.get("WEAVER_TRAIN_STATE_DIR")
        if not state_dir:
            raise BuildError("BUILD-OUTPUT: WEAVER_TRAIN_STATE_DIR is not set")
        state = Path(state_dir).resolve()
        if state not in args.out.resolve().parents:
            raise BuildError("BUILD-OUTPUT: output must be inside WEAVER_TRAIN_STATE_DIR")
        models = tomllib.loads((repo / "config/models.toml").read_text())["models"]
        if args.model not in models:
            raise BuildError("BUILD-RENDER-ONLY: model is not in config/models.toml")
        if not args.render_only and args.model != "nemotron-3-nano-30b-a3b":
            raise BuildError("BUILD-RENDER: training data requires the 30B A3B model")
        if args.render_only:
            if not args.trajectory_prompt or not args.loop_prompt:
                raise BuildError("RECONCILE-RENDER: both prompt files are required")
        elif not args.converted or args.heldout is None:
            raise BuildError("BUILD-INPUTS: converted files and held-out kit are required")
        model_manifest = mf.load_tree(loader.manifest_path_for(args.model))
        model_dir = loader.resolve_dir(model_manifest)
        model_hash = mf.require_tree(model_manifest, model_dir)
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True, trust_remote_code=False)
        options = dict(tree_hash=model_hash, max_seq_length=args.max_seq_length, enable_thinking=False)
        if args.render_only:
            prompts = {"trajectory": json.loads(args.trajectory_prompt.read_text()),
                       "loop": json.loads(args.loop_prompt.read_text())}
            record = build_render_only(tokenizer, prompts, args.out, **options)
        else:
            record = build_dataset(tokenizer, args.converted, args.heldout, args.out,
                                   seed=args.seed, limit=args.limit, **options)
    except (BuildError, mf.ManifestError, OSError, KeyError, ValueError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(record, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
