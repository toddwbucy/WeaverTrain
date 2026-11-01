# STE100 training data: plan to convert 200 whole trajectories with Gemini (2026-11-01)

Follows `2026-11-01-ste-rewrite-100.md`. The plan is ready to launch and waits on the
operator's go. The tooling is on branch `ste/500-run` (commit `dfcfb04`), not pushed.

## The decision this carries out

The 2026-10-31 pipeline ruling has the model rewrite its own training data to STE100.
Base Nemotron cannot do this: it passed 10/60 on the comparison kit. Keeping that
ruling would mean training a rewriter LoRA first: a model trained to make the data to
train the model. The operator asked instead for a plan to have Gemini 4 Argon convert
whole trajectories directly, so that the first question is answered cheaply: does STE
output help the loop work? The self-rewrite step stays open and is not replaced until
ratified. The entry is in `docs/decisions.md`.

## What gets converted

- **Unit: one whole trajectory.** Every agent narration message and the final report
  (`finish`) are converted. The issue text, tool calls, tool output and code stay
  byte-identical. Code inside narration is masked as `[[Cn]]` placeholders and
  restored afterwards. Quoted text (Markdown blockquotes) is now masked whole.
- **Source:** `nvidia/Nemotron-SWE-v1` (0 contamination hits), seed 2031. Trajectories
  used in the rewriter tests are excluded.
- **Length limit: 32,768 tokens per trajectory**, measured with Nemotron's own chat
  template. This is the length the smoke test verified for a LoRA step. Most rows are
  longer: on a first sample of 200 rows the median was 46K tokens and 184 of 200 were
  over 32K. Only about 1 row in 10 fits (1,817 of 2,051 rows read were over), so
  **this sample favors shorter tasks.** Longer rows would need a longer-context memory
  test or splitting.
- **Batch:** 200 trajectories, 6,931 segments (median about 35 per trajectory), in 60
  parts of 100 to 137 records. Each part holds whole trajectories.
- uuid is not a unique key in this dataset: 4,000 uuids repeat with different rows. A
  trajectory is named by its row number, and only one row per uuid is used.

## How it runs (`scripts/ste_batch.py`)

1. **One fresh headless opencode session per round per part.** The command is
   `opencode run -m opencode/gemini-4-argon#high --auto`, with the same rules, prompt
   and prohibitions as the 100-segment test. No one has to start sessions by hand.
2. **The checker judges each round, and repair rounds follow**, up to 2. An incomplete
   round gets one session to finish; if it is still short, the part is marked failed
   and the run goes on.
3. **Stop and report:** any script that changes text by pattern, or more than 5% of a
   round's failing records returned near-copied, stops the whole run.
4. **Spending cap:** before every session, the batch's spend is read from opencode's
   own session costs. At the cap the run pauses; a paused part resumes on the next
   run. With 4 parts at once, the cap can be passed by up to 4 sessions already
   running (about $10).
5. **Assemble:** a trajectory converts only if every segment passes the checker and
   keeps its literals. The output is `converted.jsonl` (same schema as the dataset,
   plus an `ste` field) and `run.json` (counts, cost, prompt and dataset hashes).

**Permissions.** Headless sessions run with `--auto`, which approves every opencode
permission that is not explicitly denied. Each part directory therefore holds an
`opencode.json` that denies shell, web fetch, web search and directories outside the
part; `opencode debug config` confirms opencode loads it. A session does not start
unless that file holds exactly this policy. With no shell, a session cannot run a
script, so scripted rewrites are ruled out, not only detected. Gemini wrote its
100-segment test this way, with its file tools.

## Cost and time estimate

| | Basis | Estimate |
|---|---|---|
| Cost | the 100-segment test: $2.05 for 100 records and one repair round | $120 to $180 for 60 parts |
| Time | about 20 minutes per part, 4 parts at once | about 5 hours |
| Converted trajectories | 99% segment pass after repair, about 35 segments each | 140 to 170 of 200 |

Costs read from opencode's session records for the earlier rounds:

| Run | Gemini 4 Argon | DeepSeek V4 Pro |
|---|---|---|
| 100-segment test (2 rounds) | $2.05 | $3.23 |
| void 500 run | $2.59 (the directory's session was DeepSeek) | $4.14 |

The 401 errors came from sessions on the `github-copilot` provider. The working
sessions use the `opencode` provider.

## Launch (on the operator's go)

```bash
set -a; . ./.env; set +a
# 1. pilot: 2 parts, cap $10
.venv/bin/python scripts/ste_batch.py run --name 200 --model 'opencode/gemini-4-argon#high' \
    --budget 10 --limit 2 --jobs 2
# review: status, the logs, a meaning sample, a trial assemble
# 2. the rest, cap $200
.venv/bin/python scripts/ste_batch.py run --name 200 --model 'opencode/gemini-4-argon#high' \
    --budget 200 --jobs 4
.venv/bin/python scripts/ste_batch.py assemble --name 200
```

## After the batch (not part of this launch)

- A meaning review on a sample of converted trajectories.
- Turning `converted.jsonl` into the trainer's input. The trainer takes a text field,
  so the chat template is applied with the tool calls. The loss would cover the
  assistant turns only; this is still to be built.
- A LoRA run on the converted trajectories, then the benchmark, untrained against
  trained.

## Pending from the operator

- The go to launch the pilot, and then the rest.
- The spending cap (proposed: $10 pilot, $200 total).
- Whether to keep the 32K limit (it favors short tasks) or to test memory at a longer
  length first.
- The open rulings: hedge forms, the "I" exception, the scope candidate, the sentence
  limit.
