"""Mechanical STE100 checks on one rewritten segment.

A segment passes when every hard check passes:

  placeholders   each [[Cn]] of the input appears exactly once, and no other appears
  verbs          no unapproved verb (Part 2) in any form, unless the token is also an
                 approved word or a technical name; technical verbs are allowed
  ing            no "-ing" word that is not an approved word or a technical name
  contractions   no n't, 'll, 're, 've, 'm, 'd, let's
  semicolon      no ";" outside placeholders
  length         instructions (start with a verb) at most LIMIT_I words, other
                 sentences at most LIMIT_D; a placeholder or a hyphenated word is one word

Reported but not failing (vocabulary): unapproved non-verb words, and words that are
neither approved nor technical. They are a measure: the standard admits technical nouns
the checker cannot recognize. "I" is allowed for the agent (operator ruling pending).

The lexicon comes from var/ste/approved-verbs.json (scripts/ste_extract.py, local
only) plus ste/technical-verbs.toml and ste/technical-nouns.toml; tests build their own.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLACEHOLDER = re.compile(r"\[\[C\d+\]\]")
WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*")
CONTRACTION = re.compile(r"(?i)\b\w+(?:n['’]t|['’](?:ll|re|ve|m|d))\b|\blet['’]s\b")
NON_VERB_ING = {"string", "thing", "nothing", "something", "anything", "everything", "during",
                "spring", "ring", "king", "sibling", "ceiling", "morning", "evening", "bring"}


@dataclass
class Lexicon:
    approved: set[str]                 # every approved word string, lowercase
    approved_pos: dict[str, list[str]]
    unapproved: dict[str, list[str]]   # unapproved word -> parts of speech
    unapproved_verbs: dict[str, list[str]]  # verb -> alternatives the standard gives
    technical_verbs: set[str]          # every form
    technical_nouns: set[str]
    verb_bases: set[str] = field(default_factory=set)  # approved + technical verb base forms

    @classmethod
    def load(cls, verbs_json: Path | None = None) -> "Lexicon":
        d = json.loads((verbs_json or REPO / "var" / "ste" / "approved-verbs.json").read_text())
        tech = tomllib.loads((REPO / "ste" / "technical-verbs.toml").read_text())["verbs"]
        nouns = tomllib.loads((REPO / "ste" / "technical-nouns.toml").read_text())["nouns"]
        nouns = set(nouns) | set(d.get("technical_noun_examples", []))
        return cls.build(d["approved_words"], d["unapproved_words"],
                         {u["word"]: u["alternatives"] for u in d["unapproved_verbs"]},
                         list(tech), nouns, [v["key"] for v in d["verbs"]])

    @classmethod
    def build(cls, approved_pos, unapproved, unapproved_verbs, technical_verbs, technical_nouns,
              approved_verbs) -> "Lexicon":
        tforms = set()
        for v in technical_verbs:
            for w in v.split():
                tforms |= verb_forms(w)
        tnouns = set()
        for n in technical_nouns:
            tnouns |= set(n.lower().split())
            tnouns |= {p + "s" for p in n.lower().split()} | {p + "es" for p in n.lower().split()}
        bases = {v.split()[0] for v in approved_verbs} | {v.split()[0] for v in technical_verbs}
        return cls(set(approved_pos), dict(approved_pos), dict(unapproved), dict(unapproved_verbs),
                   tforms, tnouns, bases)


def verb_forms(base: str) -> set[str]:
    out = {base, base + "s", base + "es", base + "ed", base + "d", base + "ing"}
    if base.endswith("e"):
        out |= {base[:-1] + "ing", base + "d"}
    if base.endswith("y"):
        out |= {base[:-1] + "ies", base[:-1] + "ied"}
    if re.search(r"[^aeiou][aeiou][bdgklmnprt]$", base):
        out |= {base + base[-1] + "ed", base + base[-1] + "ing"}
    return out


def lemmas(tok: str) -> list[str]:
    """Candidate base forms of an inflected token (no dictionary; over-generates)."""
    out = [tok]
    for suf, add in (("ies", "y"), ("ied", "y"), ("es", ""), ("s", ""), ("ed", ""), ("ed", "e"),
                     ("d", ""), ("ing", ""), ("ing", "e")):
        if tok.endswith(suf) and len(tok) - len(suf) >= 2:
            stem = tok[: -len(suf)] + add
            out.append(stem)
            if suf in ("ed", "ing") and len(stem) > 2 and stem[-1] == stem[-2]:
                out.append(stem[:-1])  # running -> run, stopped -> stop
    return out


def looks_technical(tok: str, names: set[str]) -> bool:
    """Identifier-shaped tokens and names the example's code uses are technical names."""
    if tok in names or tok.lower() in names:
        return True
    if re.search(r"[_.\d]", tok) or re.search(r"[a-z][A-Z]", tok):
        return True
    return len(tok) >= 2 and tok.isupper()


def strip_markup(line: str) -> str:
    line = re.sub(r"^\s*(#{1,6}\s+|[-*+]\s+|\d+(\.\d+)*[.)]\s+|>\s*)", "", line)
    return line.replace("**", "").replace("__", " ").replace("`", "")


def sentences(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        line = strip_markup(line).strip()
        if not line:
            continue
        for s in re.split(r"(?<=[.!?:])\s+(?=[A-Z\[\"(])", line):
            if s.strip():
                out.append(s.strip())
    return out


def count_words(sentence: str) -> int:
    s = PLACEHOLDER.sub(" PH ", sentence)
    return len([t for t in re.split(r"\s+", s) if re.search(r"[A-Za-z0-9]", t)])


def is_instruction(sentence: str, lex: Lexicon) -> bool:
    """An imperative: a verb first, or first after a leading condition ("If ..., do ...")."""
    s = PLACEHOLDER.sub("PH", sentence)
    m = re.match(r"(?:(?:If|When|Before|After|Then|First|Next)\b[^,]*,\s*)?([A-Za-z]+)", s)
    return bool(m) and m.group(1).lower() in lex.verb_bases


def check(output: str, masked_input: str, lex: Lexicon, limit_instruction: int = 20,
          limit_description: int = 25, names: set[str] | None = None) -> dict:
    names = names or set()
    hard: dict[str, list] = {}
    soft: dict[str, list] = {}

    want, got = sorted(PLACEHOLDER.findall(masked_input)), sorted(PLACEHOLDER.findall(output))
    if want != got:
        missing = sorted(set(want) - set(got))
        extra = sorted({p for p in got if got.count(p) > want.count(p)})
        hard["placeholders"] = [{"missing": missing, "extra_or_repeated": extra}]

    prose = PLACEHOLDER.sub(" ", output)
    if ";" in prose:
        hard["semicolon"] = [";"]
    contractions = CONTRACTION.findall(prose)
    if contractions or re.search(r"(?i)\blet['’]s\b", prose):
        hard["contractions"] = sorted({m.group(0) for m in CONTRACTION.finditer(prose)})

    verbs, ing, unapproved_words, unknown = set(), set(), set(), set()
    for raw in WORD.findall(prose):
        tok = raw.strip("'’-")
        low = tok.lower()
        if not low or low == "i" or looks_technical(tok, names):
            continue
        if low in lex.approved or low in lex.technical_verbs:
            continue
        tech_noun = low in lex.technical_nouns
        cands = lemmas(low)
        verb_lemma = next((c for c in cands if c in lex.unapproved_verbs), None)
        inflected = verb_lemma is not None and verb_lemma != low
        noun_ok = verb_lemma is not None and (verb_lemma in lex.approved or verb_lemma in lex.technical_nouns)
        if verb_lemma and not tech_noun and (inflected and low.endswith(("ed", "ing")) or not noun_ok):
            verbs.add(low)
            continue
        if low.endswith("ing") and len(low) > 4 and not tech_noun and low not in NON_VERB_ING:
            ing.add(low)
            continue
        if tech_noun or noun_ok:
            continue
        if low in lex.unapproved or any(c in lex.unapproved for c in cands[1:]):
            unapproved_words.add(low)
        elif not any(c in lex.approved for c in cands):
            unknown.add(low)
    if verbs:
        hard["verbs"] = sorted(verbs)
    if ing:
        hard["ing"] = sorted(ing)

    long = []
    for s in sentences(output):
        n = count_words(s)
        limit = limit_instruction if is_instruction(s, lex) else limit_description
        if n > limit:
            long.append({"sentence": s[:200], "words": n, "limit": limit})
    if long:
        hard["length"] = long
    over20 = sum(1 for s in sentences(output) if count_words(s) > 20)

    if unapproved_words:
        soft["unapproved_words"] = sorted(unapproved_words)
    if unknown:
        soft["unknown_words"] = sorted(unknown)
    return {"ok": not hard, "hard": hard, "soft": soft, "sentences": len(sentences(output)),
            "sentences_over_20": over20}


def repair_note(result: dict, lex: Lexicon) -> str:
    """Tell the model exactly what to correct, in plain words."""
    lines = []
    h = result["hard"]
    if "placeholders" in h:
        p = h["placeholders"][0]
        if p["missing"]:
            lines.append("These placeholders are missing. Put each one back exactly once: " + ", ".join(p["missing"]))
        if p["extra_or_repeated"]:
            lines.append("These placeholders occur too many times or are not in the text. Use each one only once: "
                         + ", ".join(p["extra_or_repeated"]))
    for v in h.get("verbs", []):
        lemma = next((c for c in lemmas(v) if c in lex.unapproved_verbs), v)
        alts = ", ".join(lex.unapproved_verbs.get(lemma) or []) or "none; write the sentence in a different way"
        lines.append(f'"{v}" is not an approved verb. Alternatives the standard gives: {alts}.')
    for w in h.get("ing", []):
        lines.append(f'"{w}" is an -ing form. Write the sentence in a different way.')
    if "contractions" in h:
        lines.append("Do not use contractions: " + ", ".join(h["contractions"]))
    if "semicolon" in h:
        lines.append("Do not use the semicolon (;).")
    for s in h.get("length", []):
        lines.append(f'This sentence has {s["words"]} words; the maximum is {s["limit"]}. '
                     f'Divide it: "{s["sentence"]}"')
    return "\n".join(f"- {x}" for x in lines)
