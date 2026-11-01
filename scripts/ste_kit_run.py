"""Run a vLLM-served model over an STE test kit (input.jsonl of masked segments).

    python scripts/ste_kit_run.py KIT_DIR NAME [--thinking] [--repairs 2]

Same prompt, checks, guards and repair notes as scripts/ste_convert.py, on a fixed
segment set, so a setting (reasoning on or off) or a model can be compared with
scripts/ste_score.py. With --thinking the reply holds the reasoning, then "</think>",
then the rewrite; only the rewrite is checked and kept, and a reply with no
"</think>" (the reasoning ran out of tokens) is a failed attempt.

Writes KIT_DIR/runs/NAME/: output.jsonl (last attempt per segment), attempts.jsonl
(every attempt with its reasoning length and check), run.json (engine vllm).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "scripts"), str(REPO / "src")]
import ste_check  # noqa: E402
import ste_convert  # noqa: E402

MAX_MODEL_LEN = 32768


def split_reasoning(content: str, thinking: bool) -> tuple[str | None, int]:
    """(rewrite or None, reasoning chars)."""
    if not thinking:
        return content.strip(), 0
    head, sep, tail = content.rpartition("</think>")
    if not sep:
        return None, len(content)
    return tail.strip(), len(head)


async def one(client, a, prompt, lex, fake, sem, rec) -> dict:
    limits = (prompt["meta"]["limit_instruction"], prompt["meta"]["limit_description"])
    msgs = [{"role": "system", "content": prompt["system"]}, *prompt["examples"],
            {"role": "user", "content": "<<<TEXT\n" + rec["text"] + "\nTEXT>>>"}]
    attempts = []
    for n in range(a.repairs + 1):
        # budget: the prompt is about 14K tokens; leave the rest of the window to the reply
        prompt_chars = sum(len(m["content"]) for m in msgs)
        max_tokens = max(1024, MAX_MODEL_LEN - prompt_chars // 3 - 512) if a.thinking \
            else min(8192, len(rec["text"]) // 2 + 256)
        t0 = time.monotonic()
        try:
            async with sem:
                r = await client.post(f"{a.server}/v1/chat/completions", timeout=3600, json={
                    "model": a.model, "temperature": 0, "max_tokens": max_tokens,
                    "chat_template_kwargs": {"enable_thinking": a.thinking}, "messages": msgs})
            r.raise_for_status()
            j = r.json()
        except Exception as e:  # noqa: BLE001 - one failed request must not end the run
            attempts.append({"error": f"{type(e).__name__}: {e}"[:500]})
            break
        ch = j["choices"][0]
        out, rchars = split_reasoning(ch["message"]["content"] or "", a.thinking)
        usage = j.get("usage") or {}
        if out is None:
            res = {"ok": False, "hard": {"no_rewrite": ["reasoning did not finish"]}, "soft": {}}
        else:
            res = ste_check.check(out, rec["text"], lex, *limits)
            ste_convert.Converter.guard(fake, res, out, rec["text"])
            if ch["finish_reason"] == "length":
                res["ok"] = False
                res["hard"]["truncated"] = [max_tokens]
        attempts.append({"output": out, "finish": ch["finish_reason"], "reasoning_chars": rchars,
                         "completion_tokens": usage.get("completion_tokens"),
                         "seconds": round(time.monotonic() - t0, 1), "check": res})
        if res["ok"] or out is None:
            break
        note = ste_check.repair_note(res, lex)
        if "copied_example" in res["hard"]:
            note += "\n- You copied text from an example. Rewrite only the text between <<<TEXT and TEXT>>>."
        if "word_ratio" in res["hard"]:
            note += ("\n- Your rewrite is much longer or much shorter than the text. Keep all of the "
                     "information and add nothing.")
        msgs = msgs + [
            {"role": "assistant", "content": out},
            {"role": "user", "content": "Your rewrite breaks these rules:\n" + (note.strip() or "- Obey the rules.")
             + "\n\nWrite the complete rewrite again with these problems corrected. Keep the meaning. "
               "Output only the rewritten text."}]
    last = next((x for x in reversed(attempts) if x.get("output") is not None), {})
    ok = bool(attempts) and attempts[-1].get("check", {}).get("ok", False)
    print(f"{rec['id']} {rec['kind']}: {'pass' if ok else 'fail'} after {len(attempts)} attempt(s)", flush=True)
    return {"id": rec["id"], "kind": rec["kind"], "ok": ok, "output": last.get("output") or "",
            "attempts": attempts}


async def main_async(a) -> int:
    import httpx

    kit = Path(a.kit)
    out = kit / "runs" / a.name
    out.mkdir(parents=True, exist_ok=False)
    prompt_path = Path(a.prompt) if a.prompt else ste_score_newest()
    prompt = json.loads(prompt_path.read_text())
    lex = ste_check.Lexicon.load()
    fake = SimpleNamespace(example_lines={ln.strip() for m in prompt["examples"] if m["role"] == "assistant"
                                          for ln in m["content"].splitlines() if len(ln.strip()) >= 20})
    recs = [json.loads(line) for line in open(kit / "input.jsonl", encoding="utf-8") if line.strip()]
    run = {"kind": "ste-kit-run", "engine": "vllm", "model": a.model, "thinking": a.thinking,
           "repairs": a.repairs, "prompt": prompt_path.name, "prompt_sha256": prompt["meta"]["prompt_sha256"],
           "segments": len(recs), "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (out / "run.json").write_text(json.dumps(run, indent=1))
    t0 = time.monotonic()
    sem = asyncio.Semaphore(a.concurrency)
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*(one(client, a, prompt, lex, fake, sem, r) for r in recs))
    with open(out / "output.jsonl", "w") as f, open(out / "attempts.jsonl", "w") as g:
        for r in results:
            f.write(json.dumps({"id": r["id"], "output": r["output"]}) + "\n")
            g.write(json.dumps(r) + "\n")
    run.update(finished_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
               seconds=round(time.monotonic() - t0), passed=sum(r["ok"] for r in results))
    (out / "run.json").write_text(json.dumps(run, indent=1))
    print(f"{run['passed']}/{len(recs)} pass in {run['seconds']} s: {out}")
    return 0


def ste_score_newest() -> Path:
    return max((REPO / "var" / "ste").glob("prompt-*.json"), key=lambda p: p.stat().st_mtime)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("kit")
    ap.add_argument("name")
    ap.add_argument("--thinking", action="store_true")
    ap.add_argument("--repairs", type=int, default=2)
    ap.add_argument("--concurrency", type=int, default=16)
    ap.add_argument("--server", default="http://127.0.0.1:8100")
    ap.add_argument("--model", default="nemotron-3-nano-30b-a3b")
    ap.add_argument("--prompt", default=None, help="var/ste/prompt-<sha12>.json (default: newest)")
    return asyncio.run(main_async(ap.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
