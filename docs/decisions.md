# Decisions

Operator rulings that set this project's direction, newest first. Each entry gives the
date, the decision and its reasoning as stated.

## 2026-11-03: the first adapter rewrites text with the full loop prompt

The operator's [ruling on issue 17][rewriter-ruling],
[answers for the new Spec][rewriter-answers] and
[later folded-layout ruling][rewriter-folded]
make the first adapter a rewriter. It converts English text into STE100 with approved
verbs. The loop makes a corpus for a later emitter fine-tune. This supersedes the
2026-11-01 reason against a rewriter and the 2026-11-02 choice of an emitter first.
The loop offers zero tools. Its registered-name list does not elect a tool.

The 387 converted trajectories, emitter Spec 0.3, code, R0/R1 records and deposits
stay unchanged. The new series uses narration and final-report pairs from the same
two accepted batches. No new Gemini text is allowed. Original masked inputs pair
with their final accepted targets, and each pair records its target round.
Only checker-passed targets qualify. They have not had meaning review.
Both held-out kits exclude all pairs with their trajectory UUIDs.

Use the full production prompt with all four examples inside one system message,
one user message with the wrapped masked input, then the target.
There are no separate example turns. The folded prompt has its own sha256.
The WeaverAgent workflow defines the fold, markers and exact repair user text.
The operator reviews the folded file before the seed.
WeaverTrain pins that file as data, with no code dependency on WeaverAgent.
Loss applies only to the final target span.
All examples are system text and get no loss.
The operator accepts the prompt cost. Do not shorten it for cost.
Read the system bytes from the seeded save point and check the complete message list.
Record the grouped target-generation instruction, single-text bundle and system hashes
as provenance, separately from the folded render identity.
A different saved prompt requires an operator decision.
Measure the folded prompt costs when the file is supplied. The old layout costs are
superseded for procedure sizing.

Balance source lengths to the kit mix: 37 below 200 characters, 30 from 200 through
499, and 33 at 500 or more. The new Spec gives exact counts and the selection seed.
The executor proposes R1 and R2 sizes from measured token costs for operator approval.
Keep all repair conversations separately. Whether R1 also trains on them remains
open until the new R0 report, and the operator can move that work to R2.

The order is a new Spec draft PR, new pair code after its merge, then rewriter R0
twice and R1, followed by a stop and report. R2 waits for the operator.
Use the frozen 100-segment rewrite kit and `ste_score.py` for first-attempt scores.
Use the approved rewrite meaning-review prompt from issue 15. Review text is for
evaluation only. The emitter and rewriter series are separate records.

The freeze lifts for new files and this decision entry only. Until issue 15's run
completes, keep its five reference scripts, `ste/`, kit and prompt unchanged.
`docs/lora-rewriter-Spec.md` is the new draft. It does not authorize code before merge.
The matching workflow changes remain in WeaverAgent. No organ amendment is required.

[rewriter-ruling]: https://github.com/toddwbucy/WeaverTrain/issues/17#issuecomment-6605286044
[rewriter-answers]: https://github.com/toddwbucy/WeaverTrain/issues/17#issuecomment-6608618052
[rewriter-folded]: https://github.com/toddwbucy/WeaverTrain/issues/17#issuecomment-6610000291

## 2026-11-03 UTC: authorize R1 after the storage hold

The operator directed R1 to start despite the reported storage condition.
The ruling is recorded on [run-log issue 7](https://github.com/toddwbucy/WeaverTrain/issues/7#issuecomment-6594815748).
The operator will handle drive maintenance and has a hot spare ready.
This clears the specific storage hold recorded before R1.
The existing failure stops and frozen training values still apply.
R1 remains 100 rows and 2 epochs. R2 and R3 remain with the operator.

## 2026-11-02: increase context to retain more held-out segments

The operator ruling is recorded in [run-log issue 7](https://github.com/toddwbucy/WeaverTrain/issues/7#issuecomment-6590725001).
Run tokenizer-only plans at 43,008 and 49,152 tokens, then measure one LoRA step at
49,152. Try 43,008 only if 49,152 does not fit. If neither fits, retain 36,864 and
report the failure. All 387 training rows already fit at 36,864.

- At 43,008, 69 segments qualify: 67 narration and 2 finish. There are 31 prefix exclusions.
- At 49,152, 80 segments qualify: 75 narration and 5 finish. There are 20 prefix exclusions.
- The 49,152-token step passed. Peak allocated memory was 36.55 GiB and 38.80 GiB on the 2 GPUs.
  Step time was 43.5 seconds. Full command wall time was 100.3584 seconds.
- Use 49,152 for the new R1 build, score plan and RECONCILE-FIELDS value.
  The training rows stay byte-identical. The SPU must serve at least this context length.
- Update RUN-CONFIG, RUN-MEMORY and the steps figure in a separate Spec PR.
  R0 waits for the context records. R1 still stops after 100 rows and 2 epochs.

The measurements are in `lora-context-probe-2026-11-02-3a7db9e` and
`lora-memory-2026-11-02-49152-3a7db9e` on the shared bulk store.
Their hashes are recorded on issue 7. The old 36,864-token records are preserved.

## 2026-11-02: approve the review prompt and use saved state for RECONCILE

The [operator ruling](https://github.com/toddwbucy/WeaverTrain/issues/7#issuecomment-6590790386)
approves `config/lora-review-prompt.md` and the SCORE-REVIEW path correction on PR 11.
The text proposed in comment 6587935968 is the starting prompt. The `ste/` tree stays unchanged.
Review verdicts remain evaluation records and never enter training data.

For RECONCILE-RENDER, read the loop's exact system-message bytes from the seeded save point.
Do not read them from the rules-prompt file on disk. Record their sha256 beside the template hash.
This follows the agent's state-loading rule in WeaverAgent issue 437, C5.
It adds evidence to RECONCILE-RECORD, not a sixth field to RECONCILE-FIELDS.
The joint check waits for the WeaverAgent loop.

The operator also confirmed that stopping the forgotten agent-test server was authorized.
For a later process that the executor did not start, stop it only on the operator's word.

## 2026-11-02: no more Gemini runs; the third STE batch is cancelled

Decision of the operator, given through the review seat on PR #5 and recorded in issue #6.
Do not use Gemini again. Do not spend more on Gemini. The third batch of 200
trajectories (seed 2033) is cancelled,
not paused. Its round 1 ran (5,985 of 6,930 segments passing, $19.23). Round 2 was
never submitted, because the submit hit Gemini's spend limit. Nothing from it is
training data. The Google API key is revoked: do not ask for it or use it.

- The 387 converted trajectories of the first two batches (196 + 191, $48.83) are the
  data for the first adapter (`docs/lora-training-Spec.md`).
- The next time the conversion loop runs, the operator's own model with the LoRA
  adapter replaces Gemini in stage 5.
- `scripts/ste_gemini.py` stays in the repository as the record of how the two batches
  were made. It is not to be run again.

## 2026-11-01: training data rewritten by Gemini, whole trajectories (first batch run)

The operator asked for a plan to convert whole trajectories with Gemini 4 Argon, in
place of the model rewriting its own data. Reason as stated in the discussion: a
rewriter LoRA would mean training a model to make the data to train the model. The
direct path answers first whether STE output helps the loop work.

- Base Nemotron cannot do the rewrite (10/60 on the comparison kit). On the
  100-segment kit, Gemini 4 Argon passed 99/100 after one repair, with 79 faithful,
  20 minor, 0 wrong and 1 data altered in meaning review, for $2.05.
- The unit is a whole trajectory: every narration and final segment converted, the
  issue text and tool output unchanged. Only rows of at most 32,768 tokens are used
  (the trainer's verified length); about 1 row in 10 qualifies.
- First batch: 200 trajectories (6,931 segments). On the operator's instruction it ran
  through the Gemini Batch API (half price) instead of opencode sessions, after a live
  check on the 100-segment kit (100/100 after one repair). Result: 196 of 200
  converted, $24.64 (`reports/2026-11-01-ste-batch-200.md`).
- This keeps the "rewritten to STE100 by the model itself" step of the 2026-10-31
  pipeline ruling open, for later or never; it is not replaced until ratified.

## 2026-11-01: "call" is not admitted; "should" has three senses

- **call** is not admitted as a technical verb. The mapping is fixed: call -> use
  ("the function calls [[C1]]" -> "the function uses [[C1]]"). It is in the rewriter
  rules, the thesaurus and `ste/fixed-mappings.toml`, and the checker's repair note
  gives "use" instead of the standard's alternatives.
- **should** has three senses:
  - advice -> "I recommend that" ("You should add a regression test." -> "I recommend
    that you add a test for the regression.");
  - expectation -> "I think that" ("This should fix the error." -> "I think that this
    change will repair the error.");
  - a requirement or a correct value ("X should be Y", "no error should be raised") ->
    "must" ("No error should be raised." -> "The code must not give an error.").
- **Still owed:** the four hedge forms ("possibly", "I think that", "this is an
  indication that", "examine if" / "find if") and the "I" exception stay candidates.
  They are used in the 500-segment run and listed as owed (`ste/usage-notes.md`).

## 2026-11-01: STE scope: narration and agent output only (candidate, pending ratification)

Only the agent's narration and output are converted to STE100. Issue text and
tool-call `think` fields stay exactly as they are. Reason as stated: the model reasons
freely and writes its instructions and output in STE; issue text is user input and
stays natural.

Effects, until ratified:

- The STE rewriter kit holds narration segments only. Issue and `think` segments
  are not scored.
- In a converted example, issue text and `think` fields stay byte-identical, like
  tool output already does. `scripts/ste_convert.py` still segments them; it changes
  when this is ratified.
- The 2026-11-01 rounds 1 and 2 scored issue and `think` segments too. Their results
  for those kinds are kept as a record and are not part of the rewriter choice.

## 2026-11-01: logical operators and SQL keywords in capitals are technical names

In prose, a logical operator or an SQL keyword is written in capitals and is a technical
name: AND, OR, NOT, XOR, TRUE, FALSE, NULL; SELECT, JOIN, DROP TABLE and the other
keywords in `ste/technical-names.toml`. The lowercase words keep their approved STE100
meanings, so the 2026-10-31 boolean ruling stands for the conjunctions: "If the file is
open and the lock is free" is plain prose, while "The filter uses AND" names the
operator. A technical name is not a verb ("Use SELECT to get the rows", not "select the
rows"), so the SQL verb rulings are needed only where text uses those words as verbs.
GRANT and REVOKE wait on the permission-scope ruling.

Capitals mean a technical name only for a listed word, or for a word that is not in
the dictionary (an acronym such as PPM or AWS), or for capitals copied from the source
text. Any other dictionary word in capitals fails the checker: the pilot passed "All
tests GO" and "OPERATE" because every all-capitals word counted as a technical name.

## 2026-10-31: keep Unsloth; its mamba-ssm file patch is a known modification

Importing Unsloth rewrites one installed mamba-ssm file (`docs/environment.md`, Known
gaps). The operator keeps Unsloth: the trainer is built on it, and the 32K LoRA step
fits with its gradient checkpointing. The modification is listed in
`config/known-modified-files.toml`, and the lock stage now hashes every installed file
against its RECORD entry, so any other change to a library file fails setup.

## 2026-10-31: STE100 technical verbs, SQL verbs and boolean names (candidate)

Rulings for the candidate scheme file `ste/ste100-scheme.candidate.json`
(`ste100-scheme/1` shape). Every entry stays candidate and unratified. The base is
WeaverAgent's verb dictionary v0.6, which this repository reads and does not modify.

Technical verbs:

- **run**: provisional. Sense: to execute a program, a test, or a command. Condition:
  execute must not already be an approved STE100 verb. If it is, stop and report, and do
  not admit run.
- **commit**: admitted. One sense: to make a set of changes permanent in a repository or
  a database. It covers both Git and SQL, since Git is a store of changes.
- **merge**: to combine branches, or to combine changes into a store. Whether SQL MERGE
  is the same act under this sense is to be checked and reported.
- **push**: to send changes to a remote repository.

SQL verbs, candidate set: select, insert, update, delete, join, create, drop, alter,
rollback. Add grant and revoke only if permission work is in scope; ask first.

Boolean terms get no special handling. and, or, not keep their approved STE100
meanings in prose. Inside code they are operators and are not checked. Deferred to the
conditions grammar under O02-1.

Synonym check before anything is admitted. For each new verb, search the STE100
approved-word list (the operator's local copy, path in `.env` as
`WEAVER_TRAIN_STE100_SOURCE`, nothing committed) and the existing dictionary for a word
with the same meaning. Report each verb as one of:

1. no overlap: admit;
2. an exact approved match exists: use the approved word, do not admit;
3. a near-synonym or shared meaning: list it, and the operator rules.

Also flag any new verb that is itself an approved everyday word, and say whether its
approved meaning fits the SQL sense. Flag rollback against git revert.

Owed by the operator, not decided here: the sentence limit (20 for all emissions, or 20
for instructions and 25 for descriptions).

## 2026-10-31: the data pipeline is the selection evaluation

The pipeline the operator laid out decides which training data is used: candidates
from NVIDIA's own sets are filtered against the benchmark snapshots
(`scripts/contamination.py`), rewritten to STE100 by the model itself, and checked. A
set enters training only through that evaluation.

Access and download limits, as ruled:

- `nvidia/Nemotron-SWE-v1` is downloaded first (done; 0 contamination hits against both
  snapshots).
- Gated pretraining sets: hold. Do not request access.
- The 2026 SFT sets (SFT-SWE-v2, SFT-SWE-v3, SFT-OpenCode-v1) are candidates:
  inventoried, not downloaded.
- Competitive-programming and math sets wait.
- Stop and ask before any download over 500 GB.
- The STE pilot stops after its first run and reports; no scale-up without the
  operator's go.

## 2026-10-31: vLLM in its own environment

The rewriting step serves the model with vLLM from a separate environment
(`vllm-serve/`, own `pyproject.toml`, `uv.lock` and `.venv`), so vLLM's torch and
transformers pins never touch the training environment's verified builds.

## 2026-10-31: Nemotron 3 Nano 30B only; Granite dropped

weaver-train now targets `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16` only.
`ibm-granite/granite-4.0-h-small` is dropped from active configs and tests. Its bf16
weights and their manifest stay on disk, unchanged.

Reasoning:

- **Provenance.** NVIDIA publishes Nemotron's training data sources
  (`docs/nemotron-provenance.md`). That gives a basis for contamination checks and for
  judging what extra training the model needs.
- **Established methods only.** Standard LoRA on attention plus Mamba `in_proj`, the
  configuration the smoke test passed at 32K. No expert adapters and no
  `target_parameters` experiments.
- **Training data stays close to NVIDIA's own sets**, since the model was built on them.

This also settles the open Granite items: its Mamba LoRA coverage under Unsloth and its
32K memory limit no longer need a decision.

## 2026-10-31: the Rust snapshot is delta-only

`multi-swe-bench-rust-2026-10-31` predates the model's cutoffs, so its absolute scores
are not evidence of capability. Only the trained-minus-untrained difference is read,
since both runs share the same contamination (`docs/benchmarks.md`).

## 2026-10-31: per-layer device pin for split models

Split models get `pin_layer_devices`, a forward pre-hook per decoder layer, rather
than a library patch. It works around one unguarded mamba-ssm Triton launch
(`docs/environment.md`, Known gaps).
