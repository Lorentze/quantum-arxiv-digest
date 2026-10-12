"""Cheap recall-oriented screening; keywords never stand in for AI judgment."""

from __future__ import annotations

import re
import unicodedata


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    text = text.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", text)


def contains(text: str, phrase: str) -> bool:
    # Boundaries matter for terms such as SPAM and T-count; do not let
    # 'unitary' or 'magic' accidentally match unrelated words.
    words = normalize(phrase).strip()
    if not words:
        return False
    pattern = re.escape(words).replace(r"\ ", r"\s+")
    return re.search(r"(?<!\w)" + pattern + r"(?!\w)", text) is not None


def screen_paper(paper: dict, profile: dict) -> dict:
    text = normalize(paper.get("title", "") + " " + paper.get("abstract", ""))
    matches = {}
    score = 0
    for topic in profile.get("topics", []):
        hits = [word for word in topic["keywords"] if contains(text, word)]
        if hits:
            matches[topic["id"]] = hits
            # Cap synonyms within each topic; papers with several distinct
            # connections rank above papers repeating a single buzzword.
            score += topic.get("weight", 3) + min(len(hits) - 1, 3)
    return {"keyword_score": score, "topics": list(matches), "keyword_matches": matches}
