"""Mechanical STE100 checks on one rewritten segment.

A segment passes when every hard check passes:

  placeholders   each [[Cn]] of the input appears exactly once, and no other appears
  verbs          no unapproved verb (Part 2) in any form, unless the token is also an
                 approved word or a technical name; technical verbs are allowed
  ing            no "-ing" word that is not an approved word or a technical name
  contractions   no n't, 'll, 're, 've, 'm, 'd, let's
  semicolon      no ";" outside placeholders
  capitals       no dictionary word in capitals ("tests GO", "OPERATE") unless it is a
                 listed technical name (ste/technical-names.toml: logical operators, SQL
                 keywords), or the input itself writes dictionary words in capitals (a
                 heading "FIX ANALYSIS", emphasis "the EXPECTED behavior"), which a
                 rewrite may keep in its own words; an acronym that is not a dictionary
                 word (PPM, AWS) is a technical name
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
CONTRACTION = re.compile(r"(?i)\b\w+(?:n['’]t|['’](?:ll|re|ve|m|d))\b"
                         r"|\b(?:let|it|that|there|here|what|who|where|how|he|she)['’]s\b")
# A word right after one of these is a noun or an adjective there, not a verb:
# "a fixed value", "in order", "a design choice", "the progress". Words that can also
# be a subject are left out ("This fails", "Some return None", "that fails"), and so is
# "to" ("to fix").
NOMINAL_BEFORE = frozenset(
    "a an the my our your its their his her no "
    "in on of for with without by from at into onto about under over between per".split())
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
    phrases: list[re.Pattern] = field(default_factory=list)  # multi-word technical verbs
    caps_names: set[str] = field(default_factory=set)  # technical names written in capitals
    fixed: dict[str, str] = field(default_factory=dict)  # operator's fixed replacement per verb

    @classmethod
    def load(cls, verbs_json: Path | None = None) -> "Lexicon":
        d = json.loads((verbs_json or REPO / "var" / "ste" / "approved-verbs.json").read_text())
        tech = tomllib.loads((REPO / "ste" / "technical-verbs.toml").read_text())["verbs"]
        nouns = tomllib.loads((REPO / "ste" / "technical-nouns.toml").read_text())["nouns"]
        nouns = set(nouns) | set(d.get("technical_noun_examples", []))
        caps = tomllib.loads((REPO / "ste" / "technical-names.toml").read_text())
        lex = cls.build(d["approved_words"], d["unapproved_words"],
                        {u["word"]: u["alternatives"] for u in d["unapproved_verbs"]},
                        list(tech), nouns, [v["key"] for v in d["verbs"]],
                        caps_names=[w for ws in caps.values() for w in ws])
        lex.fixed = tomllib.loads((REPO / "ste" / "fixed-mappings.toml").read_text())["mappings"]
        return lex

    @classmethod
    def build(cls, approved_pos, unapproved, unapproved_verbs, technical_verbs, technical_nouns,
              approved_verbs, caps_names=()) -> "Lexicon":
        # A multi-word verb ("roll back", "drag and drop") admits its head word's forms
        # alone; its other words count only inside the phrase, so a bare "drop" is checked.
        tforms, phrases = set(), []
        for v in technical_verbs:
            head, *rest = v.split()
            tforms |= verb_forms(head)
            if rest:
                forms = "|".join(sorted(verb_forms(head), key=len, reverse=True))
                tail = r"\s+".join(map(re.escape, rest))
                phrases.append(re.compile(rf"(?i)\b({forms})\s+{tail}\b"))
        tnouns = set()
        for n in technical_nouns:
            tnouns |= set(n.lower().split())
            tnouns |= {p + "s" for p in n.lower().split()} | {p + "es" for p in n.lower().split()}
        bases = {v.split()[0] for v in approved_verbs} | {v.split()[0] for v in technical_verbs}
        return cls(set(approved_pos), dict(approved_pos), dict(unapproved), dict(unapproved_verbs),
                   tforms, tnouns, bases, phrases, set(caps_names))

    def is_dictionary_word(self, low: str) -> bool:
        """An STE word (approved or unapproved) or a technical verb, in any listed form."""
        if low in self.approved or low in self.unapproved or low in self.technical_verbs:
            return True
        return any(c in self.unapproved_verbs or c in self.approved for c in lemmas(low)[1:])


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


def looks_technical(tok: str, names: set[str], lex: "Lexicon | None" = None,
                    input_caps: frozenset[str] = frozenset()) -> bool:
    """Identifier-shaped tokens and names the example's code uses are technical names.
    An all-capitals token is one only if it is a listed technical name, is not a
    dictionary word (an acronym), or the input has it in capitals too."""
    if tok in names or tok.lower() in names:
        return True
    if re.search(r"[_.\d]", tok) or re.search(r"[a-z][A-Z]", tok):
        return True
    if not (len(tok) >= 2 and tok.isupper()):
        return False
    if lex is None or tok in lex.caps_names or tok in input_caps:
        return True
    return not lex.is_dictionary_word(tok.lower())


MARKUP = re.compile(r"^\s*(#{1,6}\s+|[-*+]\s+|\d+(\.\d+)*[.)]\s+|>\s*)")


def strip_markup(line: str) -> str:
    line = MARKUP.sub("", line)
    return line.replace("**", "").replace("__", " ").replace("`", "")


def blocks(text: str) -> list[str]:
    """Join soft-wrapped lines: a sentence wrapped over several lines is one sentence.
    A blank line, a heading, a list item or a quote starts a new block; a heading is
    a block of its own."""
    out, cur = [], []
    for raw in text.splitlines():
        if not raw.strip() or MARKUP.match(raw):
            if cur:
                out.append(" ".join(cur))
            cur = []
        if raw.strip():
            cur.append(strip_markup(raw).strip())
            if re.match(r"^\s*#{1,6}\s", raw):
                out.append(" ".join(cur))
                cur = []
    if cur:
        out.append(" ".join(cur))
    return out


def sentences(text: str) -> list[str]:
    out = []
    for line in blocks(text):
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
    if CONTRACTION.search(prose):
        hard["contractions"] = sorted({m.group(0) for m in CONTRACTION.finditer(prose)})

    words = prose
    for ph in lex.phrases:  # keep the head word, drop the rest of the phrase
        words = ph.sub(lambda m: m.group(1), words)
    input_caps = frozenset(t for t in WORD.findall(PLACEHOLDER.sub(" ", masked_input))
                           if len(t) >= 2 and t.isupper())
    # the source uses capitals for headings or emphasis: the rewrite may keep the style
    caps_style = any(t not in lex.caps_names and (lex.is_dictionary_word(t.lower()) or len(t) >= 5)
                     for t in input_caps)
    verbs, ing, unapproved_words, unknown, caps = set(), set(), set(), set(), set()
    prev, prev_end = "", 0
    for m in WORD.finditer(words):
        raw = m.group(0)
        # the word before counts only when nothing but spaces separates the two
        nominal = prev in NOMINAL_BEFORE and words[prev_end:m.start()].isspace()
        tok = raw.strip("'’-")
        prev, prev_end = tok.lower(), m.end()
        low = tok.lower()
        if not low or low == "i" or looks_technical(tok, names, lex, input_caps):
            continue
        if len(tok) >= 2 and tok.isupper() and not caps_style:
            caps.add(tok)  # a dictionary word in capitals: report it, then check it as a word
        if low in lex.approved or low in lex.technical_verbs:
            continue
        tech_noun = low in lex.technical_nouns
        cands = lemmas(low)
        verb_lemma = next((c for c in cands if c in lex.unapproved_verbs), None)
        inflected = verb_lemma is not None and verb_lemma != low
        noun_ok = verb_lemma is not None and (verb_lemma in lex.approved or verb_lemma in lex.technical_nouns)
        if verb_lemma and not tech_noun and nominal and not low.endswith("ing"):
            # a noun or an adjective here: judge it as one, not as a verb
            pos = set(lex.unapproved.get(low, [])) | set(lex.unapproved.get(verb_lemma, []))
            if pos & {"n", "adj"}:
                unapproved_words.add(low)
            continue
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
    if caps:
        hard["capitals"] = sorted(caps)
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
        if lemma in lex.fixed:
            lines.append(f'"{v}" is not an approved verb. Write "{lex.fixed[lemma]}" for it (a fixed rule).')
            continue
        alts = ", ".join(lex.unapproved_verbs.get(lemma) or []) or "none; write the sentence in a different way"
        lines.append(f'"{v}" is not an approved verb. Alternatives the standard gives: {alts}.')
    for w in h.get("ing", []):
        lines.append(f'"{w}" is an -ing form. Write the sentence in a different way.')
    for w in h.get("capitals", []):
        lines.append(f'"{w}" is a normal word written in capitals. Write it in lowercase, '
                     f'or write the sentence in a different way.')
    if "contractions" in h:
        lines.append("Do not use contractions: " + ", ".join(h["contractions"]))
    if "semicolon" in h:
        lines.append("Do not use the semicolon (;).")
    for s in h.get("length", []):
        lines.append(f'This sentence has {s["words"]} words; the maximum is {s["limit"]}. '
                     f'Divide it: "{s["sentence"]}"')
    if h.get("invented_markup"):
        lines.append("Your rewrite has markup that is not in the text: "
                     + ", ".join(h["invented_markup"]) + ". Remove it.")
    if h.get("altered_literals"):
        lines.append("Your rewrite changed text that is joined to code or a name. Keep this text "
                     "exactly as it is in the input: " + ", ".join(h["altered_literals"]) + ".")
    return "\n".join(f"- {x}" for x in lines)
