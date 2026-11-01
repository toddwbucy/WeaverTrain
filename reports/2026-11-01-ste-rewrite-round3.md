# STE100 rewriter selection: round 3, narration only, with corrected masking and rules (2026-11-01)

Follows `2026-11-01-ste-rewrite-round2.md`. Result files are in
`2026-11-01-ste-rewrite-round3/`. The work behind this report is on branch
`ste/reasoning-and-kit`, not yet merged.

## What changed since round 2

1. **Masking** (`scripts/ste_convert.py`). These are now placeholders before the
   model or the checker sees them:
   - HTML tags;
   - "name : value" program output listings, where 3 or more lines pad the name to a
     column (the pandas INSTALLED VERSIONS block);
   - an exception name and its message, to the end of the line.

   A quoted error message inside a longer quotation now masks on its own. Tests show
   that in s32, s38, s52 and s55, "details", "Stepping" and the quoted errors no longer
   reach the checker. Those tests fail on the old masking.
2. **Rules and thesaurus** (candidate).
   - "should" is split into advice ("I recommend that") and expectation ("I think
     that"). Neither maps to "must".
   - New guidance for "suggests", "seems", "might" and "probably", so the doubt
     stays: "possibly", "this is an indication that", "I think that".
   - A test of a condition ("check if") becomes "examine if" or "find if", never "make
     sure that".
   - `ste/usage-notes.md` gives each form's status against the operator's copy of the
     standard: approved, or needs a ruling.
3. **Scope** (candidate, `docs/decisions.md`). Only narration and agent output are
   converted. Issue text and `think` fields stay as they are.
4. **Literal check** (`scripts/ste_experiment.py score`). Each token glued to a
   placeholder in the input, such as `145.[[C8]]` or `[[C1]]=[[C2]]`, must be in the
   rewrite unchanged.
   - Run on round 2 output, it finds every literal the meaning reviewers found, plus
     two in Gemini's s56 that they missed.
   - `score` also marks a round that answers only part of its repair file.

## The kit

The kit holds the 30 narration segments of the earlier kit, masked again from their
raw source with the new masking.

- By length of the masked text: 11 under 200 characters, 9 of 200 to 500, and 10
  over 500. (The round 1 report said 10, 10 and 10; it is now corrected.)
- The new masking did not change any of the 30 narration texts: each is byte-identical
  to the round 1 kit. So the only difference from rounds 1 and 2 is the rules.
- Pinned by hash in `kit.json`: input sha256 `5997ec5e...`, rules prompt
  `bf8d74c7c10b`.

Protocol: the same as before.

- One fresh opencode session per model, with high reasoning.
- A blind round 1, then one repair round with the checker's notes.
- The models write every rewrite themselves and may not run the checker.
- Models: DeepSeek V4 Pro and Gemini 4 Argon. Kimi K3 is dropped.

## Results

| Model | Round 1 | After repair | Altered literals | Cost, whole run | Cost per passing segment |
|---|---|---|---|---|---|
| Gemini 4 Argon | 20/30 | **30/30** | none | $0.67 | **$0.022** |
| DeepSeek V4 Pro | 12/30 | **30/30** | none | $0.97 | $0.032 |

The costs are for the whole run as the operator read them from the dashboards: round 1, the
repair round, and for DeepSeek the completion message. They are for 30 narration segments
only, so they are not comparable with the round 1 and 2 costs for 60 segments of all kinds
($1.90 for Gemini and $2.74 for DeepSeek).

For comparison, the same 30 narration segments under the old rules (rounds 1 and 2):

| Model | Round 1 | After repair |
|---|---|---|
| Gemini 4 Argon | 22/30 | 30/30 |
| DeepSeek V4 Pro | 14/30 | 30/30 |

Failing rules in round 1:

- Gemini: unapproved verbs, 10 segments.
- DeepSeek: unapproved verbs, 16 segments; -ing forms, 6.
- No segment failed on sentence length. Before, most of the length failures were in
  the long issue text, which is no longer in the kit.

Most frequent failing words in round 1, over both models:

- call (5), fixed (4), order (3), fix (2), join (2), progress (2), design (2),
  separate (2), block, ready.
- Several of these are nouns or adjectives that the checker reads as verbs ("a fixed
  value", "in order", "a design choice").
- The models then rewrote them into approved words ("a constant", "sequence"). The
  meaning stayed, but the checker forced a change that STE did not need. This is a
  checker fault to correct next.

## Hedges and tests of a condition

9 of the 30 segments contain should, suggests, seems, might, probably or "check if".
In the final rewrites of those 9, I counted these phrases:

| Phrase | DeepSeek, old rules | DeepSeek, new rules | Gemini, old rules | Gemini, new rules |
|---|---|---|---|---|
| "must" | 8 | 2 | 8 | 5 |
| "make sure" | 10 | 4 | 7 | 2 |
| hedge forms ("possibly", "I think that", "indication", "recommend") | 1 | 13 | 3 | 9 |
| "examine if" / "find if" | 2 | 7 | 3 | 7 |

These are counts of words, not a meaning review. A "must" or "shows" can be correct
where the source is certain. But both models clearly moved toward keeping doubt and
tests as they are in the source, which is what the rules asked for.

## Data alteration

There is no altered literal in either model's final output. Every token glued to a
placeholder in the input is in the rewrite unchanged.

This kit is narration only. The data damage of round 2 (s32, s38, s42, s50, s52, s55)
was in issue and `think` text, which the candidate scope leaves unconverted, and which
the new masking now protects.

## Process events

- **Sessions in the wrong directory.** The first start of round 3 used the old
  sessions in the earlier experiment's directories. They had no task waiting, so they
  did nothing for about 50 minutes. The opencode log showed no session in the new
  directories. Fresh sessions were then started there, and the round ran normally.
- **DeepSeek's repair was incomplete.** Its `.done` report listed corrections for all
  18 failed segments, but `round2.jsonl` held only the first 10.
  - A completion message named the 8 missing records and asked DeepSeek to append
    them. It added no new feedback.
  - DeepSeek reported that a second copy of the 8 records was also written at the
    same time, probably by its earlier write that ended late. It removed the
    duplicates.
  - Its final file has 18 records. Its round 1 file and the first 10 lines of round 2
    are byte-identical to before.
  - Lesson: a model's own report of what it wrote is not evidence. `score` now marks
    an incomplete round.

## What this means

- Under the corrected rules, both models reach 30/30 on narration after one repair
  round. Round 1 (blind) is lower for DeepSeek (12) than for Gemini (20).
- Gemini costs about 0.7 times what DeepSeek costs per passing segment ($0.022 against
  $0.032). At that rate, 1,000 narration segments cost about $22 with Gemini and $32 with
  DeepSeek. This is an estimate from 30 segments and does not cover a meaning review.
- Neither model altered a literal.
- The hedge rules changed the wording the way they were meant to. A meaning review of
  round 3 was not part of this round. It is the measure that would separate the two
  models now, because the checker no longer does.

## Pending

- The operator's rulings on the forms that "need a ruling": "I think that", "this is
  an indication that", "examine if" and "find if". Also the scope candidate.
- Checker: words used as nouns or adjectives are read as verbs ("fixed", "order",
  "design", "progress").
- A meaning review of round 3, or the Jev judge, when the operator gives the go.
- No 500-segment run without the operator's go.

## Files in `2026-11-01-ste-rewrite-round3/`

| File | What it is |
|---|---|
| `input.jsonl`, `kit.json` | the 30 masked narration segments, and the kit's hashes |
| `SCORES.md` | the scores after each round, with the failing rules and the altered literals |
| `<model>.round1.jsonl`, `<model>.round2.jsonl` | the rewrites |
| `<model>.score.md` | each segment: the original, the final rewrite and its verdict |
| `<model>.*-report.txt` | the models' own `.done` reports, unchanged |
