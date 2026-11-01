"""Rewrite the prose of training examples into STE100 with the served model, and check it.

    python scripts/ste_convert.py --dataset nemotron-swe-v1 --sample 500 [--seed 0]
        [--prompt var/ste/prompt-<sha12>.json] [--repairs 2] [--concurrency 32]

Per example, the prose segments are: the issue text inside <issue_description> in the
first user message, every assistant message's text, and the `thought` of each `think`
tool call. The system prompt, the rest of the user template, tool calls' other arguments
and tool output stay byte-identical.

Per segment: code-like spans (fenced blocks, inline code, quoted strings, URLs, paths,
calls and literals with brackets, dotted and snake_case names) become [[Cn]]
placeholders; the model rewrites the prose (scripts/ste_prompt.py output); the checker
(scripts/ste_check.py) tests it; on failure the model gets the checker's list and
repairs, up to --repairs times; the placeholders are then replaced by the original spans.
An example converts only if every segment passes.

Writes var/ste/pilot-<UTC>/: run.json (engine "vllm", prompt and dataset hashes, sample,
settings, counts), segments.jsonl (every attempt and check), converted.jsonl (the
examples that passed, same schema as the source), failed.jsonl (uuid and reasons).
Needs the vLLM server (vllm-serve/serve.py). Read-only on the dataset.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import subprocess
import sys
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

from weaver_train import manifest as mf  # noqa: E402
import ste_check  # noqa: E402

MAX_SEGMENT_CHARS = 30000
SPAN_PATTERNS = [
    re.compile(r"```.*?```", re.S),                       # fenced code
    re.compile(r"`[^`\n]+`"),                             # inline code
    re.compile(r"https?://\S+"),                          # URLs
    re.compile(r'"[^"\n]{1,300}"'),                       # double-quoted strings (messages, literals)
    re.compile(r"(?<![\w/])(?:\.{0,2}/)?(?:[\w.-]+/)+[\w.-]*"),  # paths with a slash
    re.compile(r"\b[\w-]+\.(?:py|pyx|pyi|txt|md|rst|toml|json|ya?ml|cfg|ini|sh|c|h|cpp|rs|js|ts|csv)\b"),
]
IDENT = re.compile(r"\b(?:[A-Za-z_]\w*\.)+[A-Za-z_]\w*\b|\b\w*_\w*\b")  # dotted or snake_case


def mask_brackets(text: str, codes: list[str]) -> str:
    """Mask a word followed by a bracketed span on one line: f(x, y), x[0], {'a': 1}."""
    out, i = [], 0
    opener = re.compile(r"[\w.]*[(\[{]")
    while True:
        m = opener.search(text, i)
        if not m:
            out.append(text[i:])
            return "".join(out)
        ph = ste_check.PLACEHOLDER.match(text, m.end() - 1)
        if ph and m.start() == m.end() - 1:  # an earlier placeholder: copy it whole
            out.append(text[i:ph.end()])
            i = ph.end()
            continue
        depth, j = 0, m.end() - 1
        pairs = {"(": ")", "[": "]", "{": "}"}
        stack, quote = [], None
        while j < len(text) and text[j] != "\n":
            c = text[j]
            if quote:  # inside a string literal, brackets are content
                if c == "\\":
                    j += 2
                    continue
                if c == quote:
                    quote = None
            elif c in "'\"" and stack:
                quote = c
            elif c in pairs:
                stack.append(pairs[c])
            elif stack and c == stack[-1]:
                stack.pop()
                if not stack:
                    break
            j += 1
        if stack or j >= len(text) or text[j] == "\n":
            out.append(text[i:m.end()])
            i = m.end()
            continue
        span = text[m.start():j + 1]
        if re.fullmatch(r"\(\w+( \w+){0,3}\)", span):  # prose aside "(nested list)": keep
            out.append(text[i:m.end()])
            i = m.end()
            continue
        codes.append(span)
        out.append(text[i:m.start()] + f"[[C{len(codes)}]]")
        i = j + 1


def mask(text: str) -> tuple[str, list[str]]:
    codes: list[str] = []

    def sub(m):
        codes.append(m.group(0))
        return f"[[C{len(codes)}]]"

    for pat in SPAN_PATTERNS:
        text = pat.sub(lambda m: m.group(0) if m.group(0).startswith("[[C") else sub(m), text)
    text = mask_brackets(text, codes)
    text = IDENT.sub(lambda m: sub(m) if not re.fullmatch(r"\d+(\.\d+)*", m.group(0)) else m.group(0), text)
    return text, codes


def unmask(text: str, codes: list[str]) -> str:
    # restore in reverse so [[C1]] is not matched inside [[C12]]; nested placeholders
    # (a span masked inside an earlier one cannot happen: earlier spans are replaced first)
    for n in range(len(codes), 0, -1):
        text = text.replace(f"[[C{n}]]", codes[n - 1])
    return text


def segments(row: dict):
    """Yield (locator, text) for each prose segment of an example."""
    msgs = row["messages"]
    for i, msg in enumerate(msgs):
        if msg["role"] == "user" and i == 1:
            m = re.search(r"<issue_description>\n?(.*?)\n?</issue_description>", msg.get("content") or "", re.S)
            if m and m.group(1).strip():
                yield {"msg": i, "field": "issue", "span": [m.start(1), m.end(1)]}, m.group(1)
        if msg["role"] != "assistant":
            continue
        if (msg.get("content") or "").strip():
            yield {"msg": i, "field": "content"}, msg["content"]
        for k, tc in enumerate(msg.get("tool_calls") or []):
            if tc["function"]["name"] == "think":
                args = json.loads(tc["function"]["arguments"])
                if (args.get("thought") or "").strip():
                    yield {"msg": i, "field": "think", "call": k}, args["thought"]


def apply(row: dict, loc: dict, new: str) -> None:
    msg = row["messages"][loc["msg"]]
    if loc["field"] == "issue":
        a, b = loc["span"]
        msg["content"] = msg["content"][:a] + new + msg["content"][b:]
    elif loc["field"] == "content":
        msg["content"] = new
    else:
        tc = msg["tool_calls"][loc["call"]]
        args = json.loads(tc["function"]["arguments"])
        args["thought"] = new
        tc["function"]["arguments"] = json.dumps(args)


def split_ws(text: str) -> tuple[str, str, str]:
    m = re.match(r"(\s*)(.*?)(\s*)$", text, re.S)
    return m.group(1), m.group(2), m.group(3)


class Converter:
    def __init__(self, a, prompt: dict, lex: ste_check.Lexicon):
        self.a, self.prompt, self.lex = a, prompt, lex
        self.sem = asyncio.Semaphore(a.concurrency)
        self.limits = (prompt["meta"]["limit_instruction"], prompt["meta"]["limit_description"])
        # lines of the worked examples' outputs: one in an output (and not in the input)
        # means the model copied an example instead of rewriting the text
        self.example_lines = {ln.strip() for m in prompt["examples"] if m["role"] == "assistant"
                              for ln in m["content"].splitlines() if len(ln.strip()) >= 20}
        self.stats = {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0}

    async def ask(self, client, messages: list[dict], max_tokens: int) -> tuple[str, str]:
        async with self.sem:
            r = await client.post(f"{self.a.server}/v1/chat/completions", timeout=900, json={
                "model": self.a.model, "temperature": 0, "max_tokens": max_tokens,
                "chat_template_kwargs": {"enable_thinking": False}, "messages": messages})
        r.raise_for_status()
        j = r.json()
        u = j.get("usage") or {}
        self.stats["requests"] += 1
        self.stats["prompt_tokens"] += u.get("prompt_tokens", 0)
        self.stats["completion_tokens"] += u.get("completion_tokens", 0)
        self.stats["cached_tokens"] += (u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        ch = j["choices"][0]
        return (ch["message"]["content"] or "").strip(), ch["finish_reason"]

    def guard(self, res: dict, out: str, masked: str) -> None:
        """Meaning guards the word checks cannot give: no copied example, no large
        growth or loss (an invented plan, a dropped half)."""
        copied = sorted(ln for ln in self.example_lines if ln in out and ln not in masked)
        if copied:
            res["hard"]["copied_example"] = copied
        n_in, n_out = ste_check.count_words(masked), ste_check.count_words(out)
        ratio = n_out / max(n_in, 1)
        res["word_ratio"] = round(ratio, 2)
        if n_in >= 8 and not 0.5 <= ratio <= 2.5:
            res["hard"]["word_ratio"] = [round(ratio, 2)]
        res["ok"] = not res["hard"]

    async def segment(self, client, uuid: str, loc: dict, text: str) -> dict:
        lead, body, trail = split_ws(text)
        rec = {"uuid": uuid, "loc": loc, "chars": len(body)}
        if "[[C" in body:
            return {**rec, "ok": False, "reason": "marker_collision"}
        masked, codes = mask(body)
        rec.update(masked=masked, codes=len(codes))
        if len(masked) > MAX_SEGMENT_CHARS:
            return {**rec, "ok": False, "reason": "too_long"}
        if not re.search(r"[A-Za-z]{2,}", ste_check.PLACEHOLDER.sub("", masked)):
            return {**rec, "ok": True, "reason": "no_prose", "attempts": [], "output": text}
        names = {c for c in codes if re.fullmatch(r"[\w.]+", c)}
        user = "<<<TEXT\n" + masked + "\nTEXT>>>"
        messages = [{"role": "system", "content": self.prompt["system"]}, *self.prompt["examples"],
                    {"role": "user", "content": user}]
        max_tokens = min(8192, len(masked) // 2 + 256)
        attempts = []
        for n in range(self.a.repairs + 1):
            try:
                out, finish = await self.ask(client, messages, max_tokens)
            except Exception as e:  # noqa: BLE001 - one failed request must not end the run
                self.stats["request_errors"] = self.stats.get("request_errors", 0) + 1
                return {**rec, "ok": False, "reason": "request_error",
                        "error": f"{type(e).__name__}: {e}"[:500], "attempts": attempts, "repairs": n}
            res = ste_check.check(out, masked, self.lex, *self.limits, names=names)
            self.guard(res, out, masked)
            if finish == "length":
                res["ok"] = False
                res["hard"]["truncated"] = [max_tokens]
            attempts.append({"output": out, "finish": finish, "check": res})
            if res["ok"]:
                restored = unmask(out, codes)
                return {**rec, "ok": True, "attempts": attempts, "repairs": n, "output": lead + restored + trail}
            note = ste_check.repair_note(res, self.lex)
            if "copied_example" in res["hard"]:
                note += "\n- You copied text from an example. Rewrite only the text between <<<TEXT and TEXT>>>."
            if "word_ratio" in res["hard"]:
                note += ("\n- Your rewrite is much longer or much shorter than the text. Keep all of the "
                         "information and add nothing.")
            note = note.strip() or "- Obey the rules."
            messages = messages + [
                {"role": "assistant", "content": out},
                {"role": "user", "content": "Your rewrite breaks these rules:\n" + note +
                 "\n\nWrite the complete rewrite again with these problems corrected. Keep the meaning. "
                 "Output only the rewritten text."}]
        return {**rec, "ok": False, "reason": "check_failed", "attempts": attempts, "repairs": self.a.repairs}

    async def example(self, client, row: dict, out_seg, out_conv, out_fail) -> dict:
        segs = list(segments(row))
        results = await asyncio.gather(*(self.segment(client, row["uuid"], loc, t) for loc, t in segs))
        for r in results:
            out_seg.write(json.dumps(r) + "\n")
        ok = all(r["ok"] for r in results)
        if ok:
            new = json.loads(json.dumps(row))
            # apply in reverse message order so issue spans keep their offsets
            for (loc, _), r in sorted(zip(segs, results), key=lambda x: -x[0][0]["msg"]):
                apply(new, loc, r["output"])
            new["ste"] = {"pilot": self.a.run_id, "prompt_sha256": self.prompt["meta"]["prompt_sha256"]}
            out_conv.write(json.dumps(new) + "\n")
        else:
            out_fail.write(json.dumps({"uuid": row["uuid"], "segments": len(results),
                                       "failed": [{"loc": r["loc"], "reason": r.get("reason")}
                                                  for r in results if not r["ok"]]}) + "\n")
        return {"ok": ok, "segments": len(results), "segments_ok": sum(r["ok"] for r in results)}


def sample_rows(path: Path, n: int, seed: int) -> list[dict]:
    total = sum(1 for _ in open(path))
    want = set(random.Random(seed).sample(range(total), n))
    rows = []
    with open(path) as f:
        for i, line in enumerate(f):
            if i in want:
                rows.append(json.loads(line))
    return rows


async def main_async(a) -> int:
    import httpx

    prompt = json.loads(Path(a.prompt).read_text())
    lex = ste_check.Lexicon.load()
    spec = tomllib.loads((REPO / "config" / "datasets.toml").read_text())["datasets"][a.dataset]
    m = mf.load_tree(REPO / "manifests" / "datasets" / f"{a.dataset}.json")
    root = Path(os.environ["WEAVER_TRAIN_DATA_STORE"]) / spec["local_dir"]
    if a.rehash:
        mf.require_tree(m, root)
    data = [f.path for f in m.files if f.path.endswith(".jsonl")]
    if len(data) != 1:
        raise SystemExit(f"expected one jsonl file in {a.dataset}, found {data}")
    rows = sample_rows(root / data[0], a.sample, a.seed)

    out = Path(a.out) / a.run_id
    out.mkdir(parents=True, exist_ok=False)
    serve = sorted((REPO / "var" / "vllm").glob("serve-*.json"))
    run = {
        "kind": "ste-conversion", "engine": "vllm", "run_id": a.run_id,
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "commit": subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True,
                                 text=True).stdout.strip(),
        "dataset": {"name": a.dataset, "tree_hash": m.tree_hash, "file": data[0]},
        "sample": {"n": a.sample, "seed": a.seed, "uuids": [r["uuid"] for r in rows]},
        "prompt": prompt["meta"], "serve_record": serve[-1].name if serve else None,
        "settings": {"repairs": a.repairs, "concurrency": a.concurrency, "temperature": 0,
                     "enable_thinking": False, "max_segment_chars": MAX_SEGMENT_CHARS},
    }
    (out / "run.json").write_text(json.dumps(run, indent=1) + "\n")

    conv = Converter(a, prompt, lex)
    t0 = time.time()
    done = []
    async with httpx.AsyncClient() as client:
        with open(out / "segments.jsonl", "w") as fs, open(out / "converted.jsonl", "w") as fc, \
                open(out / "failed.jsonl", "w") as ff:
            async def one(row):
                r = await conv.example(client, row, fs, fc, ff)
                done.append(r)
                if len(done) % 25 == 0:
                    print(f"{len(done)}/{len(rows)} examples, {sum(d['ok'] for d in done)} converted, "
                          f"{time.time() - t0:.0f}s", flush=True)
                return r
            ex_sem = asyncio.Semaphore(max(2, a.concurrency // 4))

            async def bounded(row):
                async with ex_sem:
                    return await one(row)
            await asyncio.gather(*(bounded(r) for r in rows))
    run["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    run["seconds"] = round(time.time() - t0, 1)
    run["counts"] = {"examples": len(done), "examples_converted": sum(d["ok"] for d in done),
                     "segments": sum(d["segments"] for d in done),
                     "segments_ok": sum(d["segments_ok"] for d in done), **conv.stats}
    (out / "run.json").write_text(json.dumps(run, indent=1) + "\n")
    print(json.dumps(run["counts"], indent=1))
    print(f"run: {out}", file=sys.stderr)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dataset", default="nemotron-swe-v1")
    ap.add_argument("--sample", type=int, default=500)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--prompt", default=None, help="var/ste/prompt-<sha12>.json (default: newest)")
    ap.add_argument("--repairs", type=int, default=2)
    ap.add_argument("--concurrency", type=int, default=32)
    ap.add_argument("--server", default="http://127.0.0.1:8100")
    ap.add_argument("--model", default="nemotron-3-nano-30b-a3b")
    ap.add_argument("--out", default=str(REPO / "var" / "ste"))
    ap.add_argument("--rehash", action="store_true", help="re-verify the dataset tree first (11 GB)")
    a = ap.parse_args(argv)
    if a.prompt is None:
        prompts = sorted((REPO / "var" / "ste").glob("prompt-*.json"), key=lambda p: p.stat().st_mtime)
        a.prompt = str(prompts[-1])
    a.run_id = "pilot-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return asyncio.run(main_async(a))


if __name__ == "__main__":
    sys.exit(main())
