"""Lexical term matching: verbatim evidence checks and entity names without embeddings."""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections.abc import Iterable
from functools import cache

STOPWORDS = frozenset(
    ["a", "an", "and", "as", "at", "based", "by", "for", "from", "in", "into", "of", "on", "or", "the", "to", "using", "via", "with", "within"]
)
_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")
_QUOTES = {
    **dict.fromkeys(map(ord, "‘’‚‛′"), "'"),
    **dict.fromkeys(map(ord, "“”„‟″"), '"'),
}
_TOKEN = re.compile(r"[^\W_]+(?:[+#]+)?")
_ACRONYM = re.compile(r"^[A-Za-z][A-Za-z0-9&-]{1,11}$")
_PAREN = re.compile(r"^(?P<outer>[^()]+?)\s*\((?P<inner>[^()]+)\)\s*$")
_DEFINITION = re.compile(r"\(([A-Z][A-Za-z0-9&-]{1,11})\)")


def fold(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).translate(_DASHES).translate(_QUOTES)
    return " ".join(text.casefold().split())


def contains_verbatim(haystack: str, needle: str) -> bool:
    """True when needle appears in haystack, ignoring case, spacing, dash and quote style."""
    target = fold(needle)
    return bool(target) and target in fold(haystack)


def _stem(token: str, original: str) -> str:
    if len(original) > 2 and original[-1] == "s" and original[:-1].isupper():
        return token[:-1]  # LLMs, EHRs
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith("s") and not token.endswith(("ss", "us", "is", "ys")):
        return token[:-1]
    return token


def key_tokens(value: str) -> list[str]:
    text = unicodedata.normalize("NFKC", value).translate(_DASHES).translate(_QUOTES)
    text = re.sub(r"[-/_]", " ", text)
    return [_stem(match.group().casefold(), match.group()) for match in _TOKEN.finditer(text)]


def name_key(value: str) -> str:
    return " ".join(key_tokens(value))


def content_tokens(key: str) -> list[str]:
    return [token for token in key.split() if token not in STOPWORDS]


def _acronym_letters(acronym: str) -> str:
    letters = re.sub(r"[^A-Za-z0-9]", "", acronym)
    if len(letters) > 2 and letters.endswith("s") and letters[:-1].isupper():
        letters = letters[:-1]
    return letters.casefold()


def looks_like_acronym(value: str) -> bool:
    value = value.strip()
    return bool(_ACRONYM.fullmatch(value)) and sum(c.isupper() for c in value) >= 2


def acronym_matches(acronym: str, expansion: str) -> bool:
    """Acronym letters are an ordered run of leading letters of the expansion's words."""
    letters = _acronym_letters(acronym)
    words = key_tokens(expansion)
    if len(letters) < 2 or not words or len(words) > len(letters) + 4:
        return False

    @cache
    def match(i: int, j: int) -> bool:
        if j == len(words):
            return i == len(letters)
        word = words[j]
        if word in STOPWORDS and match(i, j + 1):
            return True
        k = 1
        while k <= len(word) and i + k <= len(letters) and word[:k] == letters[i : i + k]:
            if match(i + k, j + 1):
                return True
            k += 1
        return False

    return match(0, 0)


def split_acronym(name: str) -> tuple[str, str] | None:
    """Split 'large language model (LLM)' or 'LLM (large language model)'."""
    found = _PAREN.fullmatch(name.strip())
    if not found:
        return None
    outer, inner = found["outer"].strip(), found["inner"].strip()
    if looks_like_acronym(inner) and acronym_matches(inner, outer):
        return outer, inner
    if looks_like_acronym(outer) and acronym_matches(outer, inner):
        return inner, outer
    return None


def find_acronym_definitions(text: str) -> dict[str, str]:
    """Map acronym keys to their expansions for 'expansion (ACR)' patterns in text."""
    found: dict[str, str] = {}
    for match in _DEFINITION.finditer(text):
        acronym = match.group(1)
        if not looks_like_acronym(acronym):
            continue
        letters = _acronym_letters(acronym)
        before = re.findall(r"[^\s()]+", text[max(0, match.start() - 200) : match.start()])
        before = before[-(len(letters) + 4) :]
        for start in range(len(before) - 1, -1, -1):
            candidate = " ".join(before[start:]).strip(",;:")
            words = key_tokens(candidate)
            if words and words[0][0] == letters[0] and acronym_matches(acronym, candidate):
                found.setdefault(name_key(acronym), candidate)
                break
    return found


def initials(key: str) -> str:
    return "".join(token[0] for token in content_tokens(key))


def trigrams(key: str) -> set[str]:
    padded = f"  {key} "
    return {padded[i : i + 3] for i in range(len(padded) - 2)}


def similarity(a: str, b: str) -> tuple[float, list[str]]:
    """Score two name keys; returns (score, reasons)."""
    if a == b:
        return 1.0, ["same key"]
    ta, tb = set(content_tokens(a)), set(content_tokens(b))
    reasons: list[str] = []
    score = 0.0
    if ta and tb:
        jaccard = len(ta & tb) / len(ta | tb)
        if jaccard > score:
            score, reasons = jaccard, [f"shared words {jaccard:.2f}"]
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    if ratio > score:
        score, reasons = ratio, [f"spelling {ratio:.2f}"]
    for short, long in ((a, b), (b, a)):
        if " " not in short and " " in long and short == initials(long):
            score, reasons = max(score, 0.9), ["acronym of the other name"]
    if ta and tb and (ta < tb or tb < ta) and abs(len(ta) - len(tb)) <= 2:
        score = max(score, 0.75)
        reasons.append("one name contains the other (check specificity)")
    return round(score, 3), reasons


def candidates(
    key: str, pool: Iterable[tuple[int, str]], *, threshold: float = 0.72, limit: int = 3
) -> list[tuple[int, float, list[str]]]:
    """Best matches of key against (entity_id, alias_key) pairs, one entry per entity."""
    grams = trigrams(key)
    tokens = set(content_tokens(key))
    best: dict[int, tuple[float, list[str]]] = {}
    for entity_id, other in pool:
        other_grams = trigrams(other)
        overlap = len(grams & other_grams) / max(1, len(grams | other_grams))
        if (
            overlap < 0.25
            and not tokens & set(content_tokens(other))
            and key != initials(other)
            and other != initials(key)
        ):
            continue
        score, reasons = similarity(key, other)
        if score >= threshold and score > best.get(entity_id, (0.0, []))[0]:
            best[entity_id] = (score, reasons)
    ranked = sorted(best.items(), key=lambda item: -item[1][0])[:limit]
    return [(entity_id, score, reasons) for entity_id, (score, reasons) in ranked]


class AliasScanner:
    """Find known entity aliases inside a page by n-gram lookup over name-key tokens."""

    def __init__(self, aliases: Iterable[tuple[str, int]], *, max_words: int = 6) -> None:
        self.index: dict[tuple[str, ...], int] = {}
        self.max_words = max_words
        for alias_key, entity_id in aliases:
            tokens = tuple(alias_key.split())
            if 0 < len(tokens) <= max_words and not (len(tokens) == 1 and len(tokens[0]) < 2):
                self.index.setdefault(tokens, entity_id)

    def scan(self, text: str) -> dict[int, int]:
        """Return {entity_id: occurrences}."""
        tokens = key_tokens(text)
        hits: dict[int, int] = {}
        for start in range(len(tokens)):
            for size in range(1, min(self.max_words, len(tokens) - start) + 1):
                entity_id = self.index.get(tuple(tokens[start : start + size]))
                if entity_id is not None:
                    hits[entity_id] = hits.get(entity_id, 0) + 1
        return hits
