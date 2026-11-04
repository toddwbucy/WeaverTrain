# Loop rewrite R0 plan

Date: 2026-11-04
Status: Ratified plan from [issue 15][run-log]

This file records the [approved proposal][proposal] and [operator decision][ruling].
Later decisions below set the folded layout, shared request limit and transport bound.
It sets no new procedure values. Live status and procedure records stay in issue 15.

This loop evaluation uses WeaverAgent on a second machine.
It is different from the emitter series in issues 7 and 14.
It is also different from the 30B rewriter LoRA series in issue 21.
The operator seeds and does the loop evaluation after the reviewed code merges.
This plan gives the executor no authority to seed or deploy an agent.

## Source kit and model

The source is the frozen 100-segment kit from `nvidia/Nemotron-SWE-v1`.
It has 90 narration segments and 10 final reports, one per trajectory.
The source seed is 2030. Length bins after the mask have 37, 30 and 33 segments.
The source file SHA256 is:

`d2f4937aeef40a7b178faf19ba1790e578d2b397e965f7289d929d1c6c9c4dcd`

The reference commit in issue 15 is:

`9881da6cfbc92b08b31264883aea63b73aa7b694`

The lexicon stays at:

`ab63a918b569e5b8673dc2e78e66575ee9df1bd7`

Check saved source bytes, the selection record, final text with placeholders and hashes
before the first procedure. Each source keeps its frozen code map.
Missing outputs stay in the score denominator of 100.

The [model brief][model-brief] specifies Nemotron-3-Nano-4B Q4_K_M.
It uses the RTX PRO Blackwell laptop GPU on the second machine.
The procedure tests the development code. It uses no agent deployment.
Both procedures use the same frozen input, prompt, model and caps.
The full model SHA256 and template hash belong in the procedure record.

## Prompt and request layout

[WeaverAgent PR 448][fold-pr] gives the folded file at
`docs/crates/weaver-harness/Loops/ste-conversion-prompt.txt`.
The operator [approved its exact bytes][prompt-approval] on 2026-11-03.
Its SHA256 is:

`0fe2ee35a59f6b1e30734af4d7a72e32bd6dbc0e728abc98e32e2c609dad07ed`

The file has 47,214 UTF-8 bytes, with its final LF.
The approval comment is the SEED-CERTIFICATE review reference for this hash.
A changed file must get a new review.
The source prompt text stays outside this repository's committed plan.

The unchanged `prompt-e21370fcd140.json` is the source checker bundle.
Its bundle SHA256 and system-text SHA256 are different provenance fields:

```text
bundle
e21370fcd140a4bb5e7a0d2dd415c58d8ecead9333a9e730adb66ab96e7a851c
system_text
0a42709ebe47ef8f17d728921b8bd802e0bf1466fced8d020b0b3c6dec6aa701
```

Neither provenance hash identifies the folded file.
Each use of `ste_score.py`, with each rescore, supplies `--input` and `--prompt`
explicitly.

The [layout decision][layout-ruling] replaces exact parity with the old Python layout.
The initial request has one folded system message and one user message:

```text
"<<<TEXT\n" + masked_text + "\nTEXT>>>"
```

A repair uses one user message with the original text with placeholders, last Y and
checker notes.
It uses a new gate turn and keeps no prior conversation or offers.
The full message list, template bytes and tokens are compared with training.
The [byte decision][byte-ruling] changes only the stated example-location sentence.
The exact-path editorial exemption and its audit are recorded in PR 448.
The earlier [layout audit][layout-audit] records the rejected exact-Python layout.

## Requests, offers and transport

The [request-limit decision][request-limit] sets a limit of three gate requests per
original text.
The initial request, shape retries and language repairs share that total.
`driver_repair_pass_cap` is 2 for the two requests after the initial request.
[Workflow PR 446][shared-workflow] gives that shared count.
The operator approved the [terminal classes][terminal-classes] on 2026-11-03.
A final shape fault is a shape drop.
Valid shape with a language failure is `language_unresolved`.
`offer_pass_cap` is 1 for each external turn under clause 5.10.

The [zero-bound decision][void-bound] sets `transport_stop_bound` to 0 for each R0
procedure.
The first stopped text makes that procedure void. Its counts and record stay.
A void procedure gets no R0 score and no kill-line result.
The operator starts a new complete procedure from the first kit item with the same
values.
The void procedure and new procedure have different records.
There is no retry of a stopped text within that procedure.
No earlier candidate from that text becomes its output.
Later rounds must have their own operator bound.

The [transport decision][transport-ruling] keeps `transport_stopped` as a different
outcome.
Each stopped text counts once in the denominator.
The denominator counts texts whose first gate request was made.
Its count is reported beside conversion losses, outside their numerator.
Inputs with no gate request stay in a different part of the loss bias record.
Missing output in a nonvoid procedure is a failure for the pinned scorer.

## Fit and records

The [empty-output decision][empty-output] applies before the frozen scorer reads outputs.
Remove outer whitespace as `ste_kit_run.py` does.
An empty output, or one with only whitespace, is an absent output.
The writer does not put its ID in scorer input and keeps its raw record.
The frozen scorer then counts it as missing in the denominator of 100.
Report the count of empty outputs for each procedure.
Keep the frozen scorer unchanged.

Before procedure A, the plan records fit for the complete prompt and examples.
It also covers the longest segment, reply budget and bounded repair and offer contexts.
The record names the GPU, GPU capability, driver, CUDA version and precision.
It names the decoder engine, model digest, template hash and both repository commits.
Fixture token equality alone does not show whole-kit fit or memory use.

Each procedure keeps all requests, model outputs and trace references.
Different files hold first model emissions, first gate answers and final outputs.
The first score uses the first model output, before repair or shape retry by the
driver. That output has index 0.
Shape drops and language failures have different counts and a combined loss rate.
The report keeps the count of texts with a gate request, missing outputs and loss bias.
It also keeps the review of the sense and the score difference between both procedures.

The report records two differences from `scripts/ste_kit_run.py`:

- Examples are inside the system message, with the approved location wording.
- Repairs use a new user message instead of a continued conversation.

It also records internal offer costs.
Gate requests and recorded generation outcomes stay different counts.
Incomplete trace sources give incomplete counts under PR 446.
Equal gate-request counts do not show equal model-call budgets.
No third-request output file is selected.

R0 loss rates are measurements only. They do not cause a stop or keep/drop verdict.
R0 acceptance is completion through the loop, with no kill-line judgment.
Round 1 calibration is also unscored under clause 7.03.
Later rounds receive their own caps at their start.

## Review of the sense

The approved reviewer is `claude-opus-5-5`.
The [approved prompt][review-prompt] is committed as
`config/ste-rewrite-review-prompt.md`, with SHA256:

`c18b9dea736f81fea6225d1e096e54a1d6d8840805085fb542c62b4a32f9e112`

The prompt and this plan must merge before review of the sense starts.
Select 30 source IDs with seed 2031 before R0.
Put available final rewrites from both loop procedures in one sequence.
Shuffle once with seed 2032.
Give the results opaque review IDs and keep the procedure map outside the reviewer
input.
Record the exact sequence and its hash.
The LoRA series in issue 21 reviews first rewrites under REWRITE-REVIEW.

The reviewer reads the original source and final rewrite with code spans put back.
Only that source's frozen code map can put those spans back.
The pair with placeholders and its map stay in the deposit, outside the blind
reviewer input.
Neither text is normalized, repaired or replaced for review.
A failed placeholder check gives no restored pair or replacement sample.
Placeholder and literal checks still apply under workflow clauses 6.02 and 6.06.

The verdicts are `faithful`, `minor drift`, `wrong` and `data altered`.
The report gives all four counts and the reviewed denominator.
It gives distinct defects per 100 reviewed segments that the checker accepted.
It also gives the count and rate of segments with defects.
A zero denominator gives an undefined rate.
Each original verdict stays beside any operator override, its operator and
absolute date.
There is no correct-request metric because Q7 uses no tools.
Review verdicts and text are evaluation records only. They never enter training data.

## Record handoff

Issue 15 holds the procedure's plan, scores, deposit identity and report links.
The deposit includes saved inputs, outputs, trace references, review records
and logs.
It includes a README and `SHA256SUMS`.
It names the WeaverAgent commit that ran the loop.
It also names the WeaverTrain commit that holds the kit, plan and scorer.
This document claims no completed R0 procedure or production result.

[run-log]: https://github.com/toddwbucy/WeaverTrain/issues/15
[empty-output]:
  https://github.com/toddwbucy/WeaverTrain/issues/21#issuecomment-6633945372
[proposal]:
  https://github.com/toddwbucy/WeaverAgent/issues/437#issuecomment-6595108630
[ruling]:
  https://github.com/toddwbucy/WeaverAgent/issues/437#issuecomment-6595641498
[model-brief]:
  https://github.com/toddwbucy/WeaverAgent/issues/437#issuecomment-6618477114
[fold-pr]: https://github.com/toddwbucy/WeaverAgent/pull/448
[prompt-approval]:
  https://github.com/toddwbucy/WeaverAgent/pull/448#issuecomment-6614454936
[layout-ruling]:
  https://github.com/toddwbucy/WeaverAgent/issues/437#issuecomment-6609999828
[byte-ruling]:
  https://github.com/toddwbucy/WeaverAgent/issues/437#issuecomment-6611242178
[layout-audit]:
  https://github.com/toddwbucy/WeaverAgent/issues/437#issuecomment-6608899409
[request-limit]:
  https://github.com/toddwbucy/WeaverAgent/issues/437#issuecomment-6604111261
[shared-workflow]: https://github.com/toddwbucy/WeaverAgent/pull/446
[terminal-classes]:
  https://github.com/toddwbucy/WeaverAgent/pull/446#issuecomment-6606804550
[transport-ruling]:
  https://github.com/toddwbucy/WeaverAgent/issues/447#issuecomment-6608754538
[void-bound]:
  https://github.com/toddwbucy/WeaverAgent/issues/447#issuecomment-6614455356
[review-prompt]:
  https://github.com/toddwbucy/WeaverAgent/issues/437#issuecomment-6595240746
