# STE100 rewriter: DeepSeek V4 Pro on a 100-segment kit (2026-11-01)

Follows `2026-11-01-ste-rewrite-round3.md`. The tooling for this run is on branch
`ste/500-run` (commit `1a9f603`), not yet pushed.

## Why 100 segments, and why DeepSeek

The first 500-segment run (450 narration, 50 final reports) was void:

- DeepSeek V4 Pro passed 208/500, but it changed its drafts with regex substitution
  scripts (`fix.py`, `fix2.py`, `fix3.py`). Its own report called them drafts.
- Gemini 4 Argon passed 69/500. 230 of its 500 outputs were over 95% identical to
  the input. It wrote its records through a generator script that it then deleted.

The operator then ruled a smaller test: 100 segments with one model, and a longer run
only if this one is good. Gemini was the planned model. Its provider returned HTTP 401
on every request from 17:49Z, so DeepSeek V4 Pro ran the same test. Gemini's directory
is set up and untouched, to run the same kit on 2026-11-02.

## What changed since the void run

- **The prompt forbids changing text with a program** in plain terms: no
  find-and-replace, regex, `sed` or `str.replace`. The model must keep every script it
  writes. A near-copy of the input is not a rewrite.
- **`score` checks for cheating.** A model directory that holds a script with
  `re.sub`, `.replace(`, `.translate(` or `sed s/` marks the round INVALID. Outputs over
  95% similar to their input are counted as near-copies.
- **The kit reads in batches.** The model reads 10 records at a time and re-reads
  `RULES.md` every 100 records.

## The kit

100 segments drawn from the void run's 500 (seed 2030): 90 narration and 10 final
reports, one per trajectory. The bins are 37 under 200 masked characters, 30 from 200
to 500, and 33 of 500 or more. The prompt is the round 3 prompt plus the prohibitions
(`e21370fcd140`).

## Results

| Round | Pass | Final | Narration | Failing rules | Altered literals | Near-copies |
|---|---|---|---|---|---|---|
| 1 | 51/100 | 0/10 | 51/90 | verbs 42, -ing 19, length 8 | none | 1 |
| 2 (repair) | **96/100** | 10/10 | 86/90 | verbs 2, length 1, -ing 1 | none | 1 |

- Round 1 took about 20 minutes and the repair about 15.
- **No cheating.** DeepSeek left 15 scripts (`b01.py` to `b10.py`, `r01.py` to
  `r05.py`). Each holds about 10 rewrites typed in as string literals and writes them
  to the output file. None reads the input or edits text by pattern; I checked every
  file by hand and with the guard.
- **Final reports fail first time.** All 10 failed in round 1, mostly on list items
  such as "Updated docstring" and "Modified internal call". All 10 passed after one
  repair.
- **Four segments still fail:** t002 (sentence length), t039 ("may"), t075
  ("happen") and t095 ("doing").
- **Cost:** owed by the operator. With the round costs, the cost per passing segment
  goes here.

## Meaning review

Two Claude reviewers read every input against its final rewrite, 50 each. The scale
is the round 2 scale:

- faithful: same facts and certainty;
- minor: drift a reader would not act on;
- wrong: a reader would understand something different;
- data: a concrete value changed.

| Bin (masked chars) | n | Faithful | Minor | Wrong | Data |
|---|---|---|---|---|---|
| under 200 | 37 | 37 | 0 | 0 | 0 |
| 200 to 500 | 30 | 24 | 6 | 0 | 0 |
| 500 and over | 33 | 20 | 11 | 1 | 1 |
| **all** | 100 | **81** | 17 | 1 | 1 |

By kind: of the 10 final reports, 7 are faithful and 3 have minor drift; of the 90
narration segments, 74 are faithful, 14 minor, 1 wrong and 1 data.

The round 2 review used 40 long segments per model and found DeepSeek 15 faithful, 19
minor, 4 wrong and 2 data altered. The long bin here is 20 of 33 faithful, with 1
wrong and 1 data altered. The kits differ, so this is a direction, not a measured
gain.

### The two serious cases

- **t066, wrong.** "they're supposed to operate independently" became "they operate
  independently". An expectation became a fact, and this contradicts the bug that
  the passage goes on to describe.
- **t074, data.** A sentence quoted from the issue was reworded. The rewrite changed
  "where" to "in which" inside quoted issue text.

### Patterns in the minor drift

- **Certainty moves.** An option the agent was still weighing becomes a decision
  (t036, t060). A guess becomes a claim (t031). "likely" weakens to "possibly" (t092).
  "seems ... indeed" loses its confirmation (t011).
- **Domain terms flatten.** "tracked" became "recorded" (t030), "hardcodes" became
  "uses directly" (t024), "feature" became "option" (t100), and "whole slide images"
  became "whole images" (t048).
- **Wrong sense of an approved word.** "extract" became "remove" (t039, t048), and
  "check before accessing" became "examine before it reads" (t062).
- **An invented agent.** "they will handle test updates" became "the operators will
  change the tests" (t075).

## Pending

- The operator's go for a longer run, and with which model.
- Gemini 4 Argon on the same kit, once its provider accepts requests again.
- DeepSeek's cost for both rounds.
- Rulings still owed from earlier rounds: the hedge forms ("possibly", "I think
  that", "this is an indication that", "examine if" / "find if"), the "I" exception,
  the scope candidate, the sentence limit, and the GRANT/REVOKE scope.
- Quoted issue text inside narration is not masked (t074). Masking it would close the
  data case.
