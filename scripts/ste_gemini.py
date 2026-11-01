"""Rewrite STE segments with the Gemini API: live calls for a small kit, the Batch API
(half price, up to 24 hours) for a whole batch.

    python scripts/ste_gemini.py live   DIR --round N [--concurrency 8]
    python scripts/ste_gemini.py submit --name NAME --round N
    python scripts/ste_gemini.py status --name NAME --round N
    python scripts/ste_gemini.py fetch  --name NAME --round N
    common: [--model gemini-4-argon] [--thinking high] [--prompt prompt-<sha12>.json]

The key is read from the file named by GEMINI_API_KEY_FILE (in .env), or from
GEMINI_API_KEY; it is only ever put in a request header, never printed or written.

Each request is one segment, as scripts/ste_convert.py sends it to vLLM: the prompt's
system text and worked examples, then the masked text between <<<TEXT and TEXT>>>.
The system text and examples (about 14,000 tokens) go in every request, in the same
order: Gemini 4 Argon has no explicit context cache ("Explicit caching is not supported"),
so only the API's implicit prefix caching can discount them (--cache for models that
have one). A repair round sends the same conversation
plus the last rewrite and the checker's notes, as ste_convert does.

- live: DIR holds input.jsonl (round 1) or repair-<N-1>.jsonl; writes roundN.jsonl and
  usage-roundN.json there. For the 100-segment kit: a direct comparison with the
  agentic sessions.
- submit: gathers round N's records from every part of experiments/ste-batch-NAME
  (input.jsonl for round 1, else repair-<N-1>.jsonl), uploads one JSONL file and
  starts one batch job. State: var/ste/batch-NAME/gemini/roundN.json.
- status: the job's state and counts.
- fetch: downloads the results, writes each part's roundN.jsonl, then runs the checker
  as `ste_batch.py run` does and writes each part's repair-N.jsonl and part.json.
  Usage and cost estimate go to the state file.

After the last round, `scripts/ste_batch.py assemble --name NAME` builds the converted
trajectories as before.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "scripts"), str(REPO / "src")]
import ste_batch  # noqa: E402
import ste_check  # noqa: E402
import ste_experiment as exp  # noqa: E402

API = "https://generativelanguage.googleapis.com"
# USD per million tokens, Batch API, through 2026-11-30 (secondary sources; check the
# AI Studio billing page): input, cached input, output (thinking counts as output).
# The live API is twice these.
BATCH_PRICE = {"input": 1.00, "cached": 0.05, "output": 5.00}
REPAIR = ("Your rewrite breaks these rules:\n{note}\n\nWrite the complete rewrite again with these problems "
          "corrected. Keep the meaning. Output only the rewritten text.")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def key() -> str:
    path = os.environ.get("GEMINI_API_KEY_FILE")
    k = Path(path).expanduser().read_text().strip() if path else os.environ.get("GEMINI_API_KEY", "").strip()
    if not k:
        raise SystemExit("no Gemini key: set GEMINI_API_KEY_FILE (a file holding only the key) in .env")
    return k


def client() -> httpx.Client:
    return httpx.Client(base_url=API, headers={"x-goog-api-key": key()}, timeout=600)


def check(r: httpx.Response) -> dict:
    if r.status_code >= 400:
        # the error body never holds the key; the request (with its header) is not printed
        raise SystemExit(f"Gemini API {r.status_code}: {r.text[:500]}")
    return r.json() if r.content else {}


def load_prompt(name: str) -> dict:
    return json.loads((REPO / "var" / "ste" / name).read_text())


def turn(role: str, text: str) -> dict:
    return {"role": "model" if role == "assistant" else "user", "parts": [{"text": text}]}


def make_cache(c: httpx.Client, model: str, prompt: dict, ttl_hours: int) -> str:
    body = {"model": f"models/{model}", "systemInstruction": {"parts": [{"text": prompt["system"]}]},
            "contents": [turn(m["role"], m["content"]) for m in prompt["examples"]],
            "ttl": f"{ttl_hours * 3600}s", "displayName": f"ste-{prompt['meta']['prompt_sha256'][:12]}"}
    return check(c.post("/v1beta/cachedContents", json=body))["name"]


def contents(rec: dict) -> list[dict]:
    """The request turns for one record: the text, then for a repair the earlier
    rewrite and the checker's notes."""
    out = [turn("user", "<<<TEXT\n" + rec["text"] + "\nTEXT>>>")]
    if "previous" in rec:
        out += [turn("assistant", rec["previous"]), turn("user", REPAIR.format(note=rec["problems"]))]
    return out


def request(rec: dict, prompt: dict, thinking: str, cache: str | None = None) -> dict:
    """With a cache, the system text and examples come from it; without one (Gemini 4
    Argon has no explicit caching) they go in every request, in the same order, so
    the API's implicit prefix caching can match them."""
    gen = {"thinkingConfig": {"thinkingLevel": thinking}, "maxOutputTokens": 16384}
    if cache:
        return {"cachedContent": cache, "contents": contents(rec), "generationConfig": gen}
    return {"systemInstruction": {"parts": [{"text": prompt["system"]}]},
            "contents": [turn(m["role"], m["content"]) for m in prompt["examples"]] + contents(rec),
            "generationConfig": gen}


def text_of(resp: dict) -> tuple[str | None, str]:
    cands = resp.get("candidates") or []
    if not cands:
        return None, (resp.get("promptFeedback") or {}).get("blockReason", "no_candidates")
    c = cands[0]
    parts = (c.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    return (text.strip() or None), c.get("finishReason", "")


def add_usage(total: dict, resp: dict) -> None:
    u = resp.get("usageMetadata") or {}
    for k in ("promptTokenCount", "cachedContentTokenCount", "candidatesTokenCount", "thoughtsTokenCount"):
        total[k] = total.get(k, 0) + u.get(k, 0)


def cost(u: dict, factor: float = 1.0) -> float:
    cached = u.get("cachedContentTokenCount", 0)
    fresh = u.get("promptTokenCount", 0) - cached
    out = u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)
    return round(factor * (fresh * BATCH_PRICE["input"] + cached * BATCH_PRICE["cached"]
                           + out * BATCH_PRICE["output"]) / 1e6, 4)


def round_records(src_dir: Path, n: int) -> list[dict]:
    return ste_batch.read_jsonl(src_dir / ("input.jsonl" if n == 1 else f"repair-{n - 1}.jsonl"))


# ---------------------------------------------------------------- live

def cmd_live(a) -> int:
    d = Path(a.dir)
    recs = round_records(d, a.round)
    if (d / f"round{a.round}.jsonl").exists():
        raise SystemExit(f"{d / f'round{a.round}.jsonl'} exists")
    prompt = load_prompt(a.prompt)
    usage: dict = {}
    cache = None
    if a.cache:
        with client() as c:
            cache = make_cache(c, a.model, prompt, ttl_hours=2)
    results: dict[str, str] = {}
    problems: list[str] = []

    async def go():
        sem = asyncio.Semaphore(a.concurrency)
        async with httpx.AsyncClient(base_url=API, headers={"x-goog-api-key": key()}, timeout=600) as ac:
            async def one(rec):
                async with sem:
                    for attempt in range(4):
                        r = await ac.post(f"/v1beta/models/{a.model}:generateContent",
                                          json=request(rec, prompt, a.thinking, cache))
                        if r.status_code in (429, 500, 503) and attempt < 3:
                            await asyncio.sleep(10 * (attempt + 1))
                            continue
                        break
                if r.status_code >= 400:
                    problems.append(f"{rec['id']}: HTTP {r.status_code} {r.text[:200]}")
                    return
                j = r.json()
                add_usage(usage, j)
                text, why = text_of(j)
                if text is None or why not in ("STOP", ""):
                    problems.append(f"{rec['id']}: {why}")
                if text is not None:
                    results[rec["id"]] = text
            await asyncio.gather(*(one(r) for r in recs))

    t0 = time.time()
    asyncio.run(go())
    if cache:
        with client() as c:
            c.delete(f"/v1beta/{cache}")
    with open(d / f"round{a.round}.jsonl", "w", encoding="utf-8") as f:
        for r in recs:
            if r["id"] in results:
                f.write(json.dumps({"id": r["id"], "output": results[r["id"]]}) + "\n")
    rec = {"round": a.round, "model": a.model, "thinking": a.thinking, "prompt": a.prompt, "records": len(recs),
           "written": len(results), "seconds": round(time.time() - t0), "usage": usage,
           "cost_usd_live": cost(usage, 2.0), "problems": problems}
    (d / f"usage-round{a.round}.json").write_text(json.dumps(rec, indent=1) + "\n")
    print(json.dumps({k: v for k, v in rec.items() if k != "problems"} | {"problems": len(problems)}, indent=1))
    return 0


# ---------------------------------------------------------------- batch

def state_path(name: str, n: int) -> Path:
    return ste_batch.var_dir(name) / "gemini" / f"round{n}.json"


def parts_of(name: str) -> list[Path]:
    return sorted(p for p in ste_batch.exp_dir(name).glob("p[0-9][0-9][0-9]") if p.is_dir())


def cmd_submit(a) -> int:
    sp = state_path(a.name, a.round)
    if sp.exists():
        raise SystemExit(f"{sp} exists: round {a.round} was submitted")
    lines = []
    for d in parts_of(a.name):
        if a.round > 1 and not (d / f"repair-{a.round - 1}.jsonl").exists():
            raise SystemExit(f"{d.name}: no repair-{a.round - 1}.jsonl; fetch round {a.round - 1} first")
        if (d / f"round{a.round}.jsonl").exists():
            raise SystemExit(f"{d.name}: round{a.round}.jsonl exists")
        lines += [(d.name, r) for r in round_records(d, a.round)]
    if not lines:
        print("nothing to submit: every record passes")
        return 0
    prompt = load_prompt(a.prompt)
    sp.parent.mkdir(parents=True, exist_ok=True)
    jl = sp.with_suffix(".jsonl")
    with client() as c:
        cache = make_cache(c, a.model, prompt, ttl_hours=a.cache_hours) if a.cache else None
        with open(jl, "w", encoding="utf-8") as f:
            for part, r in lines:
                f.write(json.dumps({"key": f"{part}/{r['id']}", "request": request(r, prompt, a.thinking, cache)}) + "\n")
        data = jl.read_bytes()
        start = c.post("/upload/v1beta/files", headers={
            "X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(len(data)),
            "X-Goog-Upload-Header-Content-Type": "application/jsonl", "Content-Type": "application/json"},
            json={"file": {"display_name": jl.name}})
        if start.status_code >= 400:
            check(start)
        url = start.headers["x-goog-upload-url"]
        up = check(c.post(url, headers={"X-Goog-Upload-Command": "upload, finalize", "X-Goog-Upload-Offset": "0"},
                          content=data))
        job = check(c.post(f"/v1beta/models/{a.model}:batchGenerateContent", json={"batch": {
            "display_name": f"ste-{a.name}-round{a.round}", "input_config": {"file_name": up["file"]["name"]}}}))
    state = {"submitted_at": now(), "name": a.name, "round": a.round, "model": a.model, "thinking": a.thinking,
             "prompt": a.prompt, "cache": cache, "cache_hours": a.cache_hours, "requests": len(lines),
             "input_file": up["file"]["name"], "input_sha256": exp.sha256(jl), "job": job["name"]}
    sp.write_text(json.dumps(state, indent=1) + "\n")
    print(json.dumps(state, indent=1))
    return 0


def job_state(j: dict) -> str:
    return j.get("state") or (j.get("metadata") or {}).get("state") or "?"


def cmd_status(a) -> int:
    state = json.loads(state_path(a.name, a.round).read_text())
    with client() as c:
        j = check(c.get(f"/v1beta/{state['job']}"))
    meta = j.get("metadata") or j
    print(json.dumps({"job": state["job"], "state": job_state(j), "requests": state["requests"],
                      "stats": meta.get("batchStats"), "created": meta.get("createTime"),
                      "updated": meta.get("updateTime")}, indent=1))
    return 0


def cmd_fetch(a) -> int:
    sp = state_path(a.name, a.round)
    state = json.loads(sp.read_text())
    with client() as c:
        j = check(c.get(f"/v1beta/{state['job']}"))
        if job_state(j) != "JOB_STATE_SUCCEEDED":
            raise SystemExit(f"job is {job_state(j)}")
        resp = j.get("response") or (j.get("metadata") or {}).get("output") or {}
        fname = resp.get("responsesFile") or (resp.get("dest") or {}).get("fileName") \
            or ((j.get("metadata") or {}).get("output") or {}).get("responsesFile")
        if not fname:
            raise SystemExit(f"no results file in the job: {json.dumps(j)[:500]}")
        raw = c.get(f"/download/v1beta/{fname}:download", params={"alt": "media"})
        check(raw)
        if state.get("cache"):
            try:
                c.delete(f"/v1beta/{state['cache']}")
            except Exception:  # noqa: BLE001 - an expired cache is already gone
                pass
    out_path = sp.with_name(f"round{a.round}-results.jsonl")
    out_path.write_bytes(raw.content)
    by_part: dict[str, dict[str, str]] = {}
    usage: dict = {}
    problems = []
    for line in raw.text.splitlines():
        if not line.strip():
            continue
        x = json.loads(line)
        part, rid = x["key"].split("/", 1)
        if "error" in x:
            problems.append(f"{x['key']}: {x['error']}")
            continue
        add_usage(usage, x["response"])
        text, why = text_of(x["response"])
        if text is None or why not in ("STOP", ""):
            problems.append(f"{x['key']}: {why}")
        if text is not None:
            by_part.setdefault(part, {})[rid] = text
    judge = exp.Judge(state["prompt"])
    summary = []
    for d in parts_of(a.name):
        recs = round_records(d, a.round)
        if not recs:
            continue
        got = by_part.get(d.name, {})
        with open(d / f"round{a.round}.jsonl", "w", encoding="utf-8") as f:
            for r in recs:
                if r["id"] in got:
                    f.write(json.dumps({"id": r["id"], "output": got[r["id"]]}) + "\n")
        inputs = ste_batch.read_jsonl(d / "input.jsonl")
        ok, fails = ste_batch.judge_round(d, inputs, judge)
        with open(d / f"repair-{a.round}.jsonl", "w", encoding="utf-8") as f:
            for x in fails:
                f.write(json.dumps(x) + "\n")
        meta = json.loads((d / "part.json").read_text())
        meta["rounds"][str(a.round)] = {"pass": ok, "via": "gemini-batch", "job": state["job"]}
        meta["state"] = "done" if not fails or a.round >= a.last_round else "running"
        (d / "part.json").write_text(json.dumps(meta, indent=1) + "\n")
        summary.append((d.name, ok, len(inputs)))
    state.update(fetched_at=now(), usage=usage, cost_usd_batch=cost(usage), problems=len(problems),
                 written=sum(len(v) for v in by_part.values()))
    sp.write_text(json.dumps(state, indent=1) + "\n")
    (sp.with_name(f"round{a.round}-problems.txt")).write_text("\n".join(problems) + "\n")
    passed = sum(ok for _, ok, _ in summary)
    total = sum(n for _, _, n in summary)
    print(json.dumps({"round": a.round, "written": state["written"], "requests": state["requests"],
                      "problems": len(problems), "pass_after_round": f"{passed}/{total}",
                      "usage": usage, "cost_usd_batch": state["cost_usd_batch"]}, indent=1))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    lv = sub.add_parser("live")
    lv.add_argument("dir")
    lv.add_argument("--concurrency", type=int, default=8)
    s = sub.add_parser("submit")
    s.add_argument("--cache-hours", type=int, default=48, help="context cache lifetime: covers the batch window")
    sub.add_parser("status")
    f = sub.add_parser("fetch")
    f.add_argument("--last-round", type=int, default=3, help="a part is done after this round")
    for x in sub.choices.values():
        x.add_argument("--round", type=int, required=True)
        x.add_argument("--model", default="gemini-4-argon")
        x.add_argument("--thinking", default="high")
        x.add_argument("--prompt", default=ste_batch.DEFAULT_PROMPT)
        x.add_argument("--cache", action="store_true", help="an explicit context cache (not for Gemini 4 Argon)")
        if x is not lv:
            x.add_argument("--name", required=True)
    a = ap.parse_args(argv)
    return {"live": cmd_live, "submit": cmd_submit, "status": cmd_status, "fetch": cmd_fetch}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
