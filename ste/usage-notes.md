# STE100 usage notes for software text

Operator clarifications on how to write common software verbs that STE100 does not
approve. Candidate, unratified. Written in our own words; the approval status of each
word is from the operator's local copy of the standard, and no text of the standard is
copied here. The technical verbs we admit are in `technical-verbs.toml`.

## analyze (2026-11-01)

"analyze" is not an approved verb, and the standard gives no alternative: write the
sentence in a different way. Choose the approved word by what the analysis is for.

- **examine**: for analyzing code, an issue, a traceback or results, meaning looking at
  something carefully to understand it.
  - "Let me analyze the issue" -> "I will examine the issue."
  - "Analyzing the traceback shows..." -> "The traceback shows...", or "I examined the
    traceback. It shows..."
- **measure**: only for quantities such as time, memory or size.
  - "Analyze the performance" -> "Measure the time that the function uses."
- **find** and **identify** are approved and often fit the purpose of the analysis
  better.
  - "Analyze why the test fails" -> "Find the cause of the test failure."
- **inspection** (noun) is approved, so "do an inspection of the module" also works.
  "inspect" as a verb is not approved.

## Hedges, advice and tests of a condition (candidate, 2026-11-01)

The meaning review of 2026-11-01 found that rewriters changed guesses into facts
("suggests" to "shows"), advice into requirements ("should" to "must"), and tests into
actions ("check if" to "make sure that"). The forms below keep the meaning. Each was
looked up in the operator's copy of the standard; "needs a ruling" means the words are
in the dictionary but the sense that we use is not plainly the approved one.

| Source | Form | Status |
|---|---|---|
| might, may, maybe, perhaps | possibly; it is possible that | approved (POSSIBLY adv, POSSIBLE adj) |
| should (expectation), probably, likely | I think that | needs a ruling: THINK (v) is approved, but the standard has no word for this degree of certainty, and "I" is the agent exception |
| suggests, indicates | this is an indication that | needs a ruling: INDICATION (n) is approved, its approved sense may be narrower |
| seems, appears | possibly; I think that | approved / needs a ruling (as above) |
| should (advice) | I recommend that | approved (RECOMMEND v), with the "I" exception |
| check if, check whether, see if | examine if; find if | needs a ruling: EXAMINE and FIND (v) and IF (conj) are approved, but IF in an indirect question ("find if X") may be outside its approved sense; WHETHER is not approved |
| check if (alternative) | do a check to find if | as above; CHECK is approved as a noun |

Not approved, not in the dictionary: probably, probable (unapproved adj), likely,
perhaps, maybe, might, suggest, seem, certain (unapproved adj), whether (unapproved
conj). "make sure that" stays the form for "ensure" and "verify" (cause or confirm a
result), never for a test.
