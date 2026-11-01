# STE100 rewriter selection: what we are doing and where it stands (2026-11-01)

Status at 14:20Z on 2026-11-01: round 1 is done for three models and the repair round
is running. Round 1 results are in `2026-11-01-ste-rewrite-round1/`.

## Why we are doing this

The plan is to fine-tune Nemotron 3 Nano 30B-A3B on agent trajectories from
`nvidia/Nemotron-SWE-v1`, with the prose rewritten into ASD-STE100 Simplified Technical
English (STE). The goal is a model that writes STE by default. The code in each example
does not change; only the prose is rewritten.

The first plan was to let Nemotron rewrite its own training data. A 500-example pilot
on 2026-11-01 stopped that plan:

- 0 of 500 examples converted (an example converts only when every segment passes).
- 57.5% of segments passed the checker, nearly all of them short narration.
- Issue text passed 4.6% of the time, and tool-call `think` text 0.1%.
- A hand review of 50 passing segments found 8 with wrong or invented meaning.

So the question became: which foundation model can do the rewrite, how well, and at
what cost.

## What a test looks like

**The kit.** 60 segments taken from the pilot sample of Nemotron-SWE-v1:

- 30 narration segments (the agent's notes on what it does): 10 under 200
  characters, 10 of 200 to 500, and 10 over 500;
- 15 issue texts;
- 15 tool-call `think` fields.

Code, commands, paths and program output are masked as `[[Cn]]` placeholders before a
model sees the text. Each placeholder must come back exactly once. The kit is pinned
by hash (`kit.json`).

**The rules.** The rules file tells the model how to write STE:

- approved STE words with their approved meanings;
- a list of technical verbs admitted for software (candidate, not yet ratified);
- technical names;
- placeholders, Markdown and meaning;
- the 2026-11-01 capitals ruling: logical operators and SQL keywords in capitals are
  technical names.

The rules file and the checker's repair notes come from the operator's copy of the
standard, so they are not committed.

**The checker.** `scripts/ste_check.py` decides whether a segment passes. A segment
passes only when every hard rule passes:

- only approved or technical verbs;
- no -ing verb forms;
- placeholders kept;
- no contractions and no semicolons;
- sentence length within 20 words for instructions and 25 for descriptions;
- capitals used only for technical names.

The checker does not judge meaning. Meaning is reviewed separately, by hand.

**How each model runs.** Each model works in its own directory and its own opencode
session, with high reasoning. The operator starts each session with one line: "Read
MONITOR.md and do exactly what it says."

- The session then watches a `messages/` directory. Each task is a numbered file, and
  the model writes a `.done` report when it finishes.
- With this message drop, every model gets the same task at the same time, and
  several providers can run at once.
- Models must write every rewrite themselves. They may not run the checker, generate
  rewrites with a program, or search the web.

**Rounds.**

- Round 1 is blind: the model has the rules but no feedback.
- Then `repair` runs the checker for all models at once and gives each model its
  failed segments. Each failed segment comes with the original text, the model's
  rewrite, and the rules it broke.
- The model corrects them in round 2, and so on.

All the bookkeeping is in `scripts/ste_experiment.py`: kit, new, send, status, repair
and score.

## Models in this run

| Model | Reasoning | Notes |
|---|---|---|
| DeepSeek V4 Pro | high | its local run directory is named `deepseek-v4.1-flash` (named before the model was confirmed) |
| Gemini 4 Argon | high | |
| Kimi K3 | high | |
| GLM 5.3 | high | out of this round: timed out on round 1 (operator, 2026-11-01) |

## Round 1 results

| Model | Pass | issue (15) | narration (30) | think (15) | Main failing rules | Cost | Cost per passing segment |
|---|---|---|---|---|---|---|---|
| Gemini 4 Argon | **31/60** | 5 | 22 | 4 | verbs 21, length 12, -ing 2 | $1.10 | $0.035 |
| DeepSeek V4 Pro | 15/60 | 1 | 14 | 0 | verbs 41, -ing 17, length 3 | $1.44 | $0.096 |
| Kimi K3 | 13/60 | 0 | 13 | 0 | verbs 47, length 6, -ing 2, contractions 1 | $1.64 | $0.126 |

The costs are from the provider dashboards, as the operator read them. One segment can
fail more than one rule.

Narration, by input length:

| Length | Segments | DeepSeek V4 Pro | Gemini 4 Argon | Kimi K3 |
|---|---|---|---|---|
| under 200 characters | 11 | 9 | 10 | 8 |
| 200 to 500 | 9 | 5 | 7 | 4 |
| over 500 | 10 | 0 | 5 | 1 |

Across the three models, 11 segments pass for all three and 29 pass for none.

What round 1 shows:

- **Verbs are the main problem.** Every model used everyday software verbs that are
  not approved. The most frequent, over the three models, are expected (26), fix (23),
  existing (20), details (20), call (14) and fails (12), mostly the same words the
  Nemotron pilot failed on. Some flags (details, route, feature, block) can be nouns
  that the checker reads as verbs. These are not reviewed yet.
- **Self-reports do not agree with the checker.** All three `.done` reports say that
  every rewrite obeys the rules. The checker failed 29 to 47 of each model's 60. Without
  a checker, a model cannot tell which of its verbs are not approved.
- **Long and structured text is where the models differ.** On narration over 500
  characters, Gemini passed 5 of 10, Kimi 1 and DeepSeek 0. Issue and `think` text
  stay difficult for all three.
- **Gemini's main fault is sentence length** (12 segments), which a repair round
  usually corrects easily.
- **Placeholders are safe.** In all 180 rewrites, no placeholder was lost or added.
  The models' reports say so too, and the checker agrees.

Mechanical meaning check: only two rewrites differ much in length from their input
(Gemini s06 and s09, 1.6x and 1.8x). Both are short sentences that grew by a clause.
"ensure robustness", for example, became "make sure that the code operates correctly in
all conditions". Their meaning did not change. A full meaning review of the long
segments is still to do.

DeepSeek wrote a helper script to write its finished text to JSON, which the rules
allow. The script holds only DeepSeek's own rewrites as text and does not generate
rewrites.

## Earlier runs (not comparable)

Before this layout, the same 60 segments ran with an earlier prompt (`a0cbf09846ef`,
before the capitals rules) and an earlier checker:

| Run | Pass |
|---|---|
| Nemotron, reasoning off, 2 repairs | 10/60 |
| Nemotron, reasoning on | 14/60, and the reasoning runs out of the 32K context on long text |
| DeepSeek, opencode, max reasoning | 59/60, 58/60 under the current checker |
| GLM 5.3 Flash, blind | 14/60 |
| GLM 5.3 Flash, round 2 with the checker's notes | 54/60 |

The DeepSeek run is not a clean comparison: that session ran the scorer itself and
repaired against it. This is why the current layout forbids models to run the checker,
and gives every model the same feedback rounds.

## In progress and next

1. **Repair round 1** was sent at 14:14Z to DeepSeek, Gemini and Kimi: 45, 29 and 47
   segments. Each session was cleared first, so the model starts from the files only.
   When all three finish, the next steps are:
   - score round 2;
   - read the long segments for meaning;
   - record each model's repair cost.
2. **Choosing a rewriter.** Pass rate after repair, meaning faithfulness and cost per
   passing segment decide. A larger pilot through the chosen provider's API waits for
   the operator's go. Before such a run, check each provider's terms on using outputs
   for training.
3. **Rulings that the operator still owes:**
   - the computing verbs (call, return, raise, pass);
   - the class 2 and 3 SQL verbs;
   - the GRANT and REVOKE scope;
   - the sentence limit (20 for all sentences, or 20 for instructions and 25 for
     descriptions);
   - the "I" exception for the agent's own voice.

   Each verb ruling changes what the checker fails.

The scripts behind this report (`scripts/ste_experiment.py`, `scripts/ste_check.py`
changes, the STE thesaurus and usage notes) are on branch `ste/reasoning-and-kit`, not
yet merged.

## Files in `2026-11-01-ste-rewrite-round1/`

| File | What it is |
|---|---|
| `input.jsonl` | the 60 masked segments (`id`, `kind`, `text`) |
| `kit.json` | prompt hash, sentence limits and the sha256 of each kit file |
| `<model>.round1.jsonl` | the model's round 1 rewrites (`id`, `output`) |
| `<model>.score.md` | each segment: the original, the rewrite and the rules that failed |
| `<model>.round1-report.txt` | the model's own `.done` report, unchanged |
