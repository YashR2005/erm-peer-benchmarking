"""Quote verifier: a quote counts only if it appears on the PDF page it cites.

Deterministic, so a model-invented quote never reaches the matrix or the
report. Matching is fuzzy only to absorb PDF text-extraction noise (line
breaks, hyphenation, curly quotes, ligatures), not paraphrase.
"""

from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

from .schemas import Verification

_REPLACEMENTS = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"',
    "–": "-", "—": "-", "−": "-",
    " ": " ", "­": "",
    "•": " ", "●": " ", "▪": " ",
}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)  # also expands ligatures such as "ﬁ"
    for old, new in _REPLACEMENTS.items():
        text = text.replace(old, new)
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)  # re-join words hyphenated across lines
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def _score(quote: str, page_text: str) -> float:
    if not page_text:
        return 0.0
    if quote in page_text:
        return 100.0
    return fuzz.partial_ratio(quote, page_text)


def normalize_pages(pages: list[str]) -> list[str]:
    return [normalize(p) for p in pages]


def verify_quote(quote: str, norm_pages: list[str], page: int, min_score: float = 90,
                 min_chars: int = 15) -> Verification:
    """Check `quote` against the 1-indexed `page`, then the page break after it, then the
    whole document. `norm_pages` must come from normalize_pages().

    A quote found on a different page is verified with the page corrected, and
    the note says so.
    """
    q = normalize(quote)
    if len(q) < min_chars:
        return Verification(verified=False, score=0, note="quote too short to verify")
    n = len(norm_pages)

    if 1 <= page <= n:
        s = _score(q, norm_pages[page - 1])
        if s >= min_score:
            return Verification(verified=True, score=s, found_page=page)
        if page < n:
            s_next = _score(q, norm_pages[page])
            if s_next >= min_score:
                return Verification(verified=True, score=s_next, found_page=page + 1,
                                    note=f"found on page {page + 1}, not the cited page {page}")
            # The quote may run across the page break.
            joined = norm_pages[page - 1] + " " + norm_pages[page]
            s = _score(q, joined)
            if s >= min_score:
                return Verification(verified=True, score=s, found_page=page,
                                    note=f"runs across pages {page} and {page + 1}")

    best_score, best_page = 0.0, None
    for i, text in enumerate(norm_pages, start=1):
        s = _score(q, text)
        if s > best_score:
            best_score, best_page = s, i
            if s == 100.0:
                break
    if best_score >= min_score:
        return Verification(verified=True, score=best_score, found_page=best_page,
                            note=f"found on page {best_page}, not the cited page {page}")
    return Verification(verified=False, score=best_score,
                        note="quote not found in the document text")
