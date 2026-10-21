"""Documents: download, per-page text, light checks, risk-section locator."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from urllib.parse import unquote, urljoin

import httpx2
import pymupdf
from rapidfuzz import fuzz

from .verify import normalize

# Some investor-relations hosts refuse requests that don't look like a browser.
BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf,*/*;q=0.8",
    "Accept-Language": "en-SG,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}

Fetcher = Callable[[str], bytes]


class DownloadError(Exception):
    pass


class NotPdfError(DownloadError):
    pass


def http_fetch(url: str, max_mb: int = 80) -> bytes:
    """Download a URL with a browser-like user agent, refusing anything over max_mb."""
    limit = max_mb * 1024 * 1024
    try:
        with httpx2.Client(follow_redirects=True, timeout=60, headers=BROWSER_HEADERS) as c:
            with c.stream("GET", url) as response:
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > limit:
                        raise DownloadError(f"larger than {max_mb} MB")
                    chunks.append(chunk)
    except httpx2.HTTPStatusError as e:
        raise DownloadError(f"HTTP {e.response.status_code}") from e
    except httpx2.HTTPError as e:
        raise DownloadError(f"{type(e).__name__}: {e}") from e
    return b"".join(chunks)


@dataclass
class StoredPdf:
    sha256: str
    path: Path


def store_pdf(data: bytes, sources_dir: Path) -> StoredPdf:
    if b"%PDF" not in data[:1024]:
        raise NotPdfError("the link did not return a PDF (it may be a web page listing reports)")
    sha = hashlib.sha256(data).hexdigest()
    path = sources_dir / f"{sha}.pdf"
    if not path.exists():
        path.write_bytes(data)
    return StoredPdf(sha256=sha, path=path)


# ---------------------------------------------------------------------------
# Announcement pages (e.g. SGX) that link to the PDF instead of being the PDF

_ATTACHMENT_HINTS = {
    "annual_report": ["annual_report", "annual report", "annualreport", "_ar_", "ar20"],
    "sustainability_report": ["sustainability"],
    "corporate_governance_report": ["governance"],
    "other": [],
}
_NOT_THE_REPORT = ["letter", "notice", "proxy", "circular", "addendum", "form", "presentation"]


def pdf_links(html: str, base_url: str) -> list[str]:
    """PDF attachment links on an HTML page, resolved to absolute URLs, in page order."""
    links = re.findall(r'href\s*=\s*["\']([^"\']+\.pdf[^"\']*)["\']', html, re.I)
    return list(dict.fromkeys(urljoin(base_url, unescape(link)) for link in links))


def rank_attachments(links: list[str], doc_type: str) -> list[str]:
    """Most likely attachment first: filename matches the document type, and isn't a
    letter, notice or proxy form."""
    hints = _ATTACHMENT_HINTS.get(doc_type, [])

    def score(link: str) -> tuple[int, int]:
        name = unquote(link.rsplit("/", 1)[-1]).lower()
        return (-sum(h in name for h in hints), sum(w in name for w in _NOT_THE_REPORT))

    return sorted(links, key=score)


# ---------------------------------------------------------------------------
# Page text


def extract_pages(pdf_path: Path) -> list[str]:
    with pymupdf.open(pdf_path) as doc:
        return [page.get_text("text") for page in doc]


def cached_pages(pdf_path: Path, pages_dir: Path) -> list[str]:
    """Per-page text, cached by file hash (the PDF is named by its hash)."""
    cache = pages_dir / f"{pdf_path.stem}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    pages = extract_pages(pdf_path)
    cache.write_text(json.dumps(pages, ensure_ascii=False))
    return pages


def read_toc(pdf_path: Path) -> list[tuple[int, str, int]]:
    """PDF bookmarks as (level, title, 1-indexed page)."""
    with pymupdf.open(pdf_path) as doc:
        return [(lvl, title, page) for lvl, title, page in doc.get_toc(simple=True)]


def low_text_pages(pages: list[str], min_chars: int = 50) -> int:
    """Pages with almost no text layer: likely scanned images that need OCR."""
    return sum(1 for p in pages if len(p.strip()) < min_chars)


# ---------------------------------------------------------------------------
# Light checks

_NAME_SUFFIXES = re.compile(
    r"\b(limited|ltd|holdings?|group|inc|incorporated|plc|corporation|corp|berhad|bhd|pte|co)\b\.?"
)


def core_name(name: str) -> str:
    """'City Developments Limited (SGX: C09)' -> 'city developments'."""
    core = _NAME_SUFFIXES.sub(" ", normalize(re.sub(r"\(.*?\)", " ", name)))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9& ]+", " ", core)).strip()


def company_matches(pages: list[str], names: list[str], first_n: int = 4) -> bool:
    text = normalize(" ".join(pages[:first_n]))
    text = re.sub(r"[^a-z0-9& ]+", " ", text)
    for name in names:
        core = re.sub(r"\s+", " ", core_name(name))
        if len(core) < 3:
            continue
        if core in text or fuzz.partial_ratio(core, text) >= 90:
            return True
    return False


_YEAR_PATTERNS = [
    r"annual report\s+(20\d\d)",
    r"(20\d\d)\s+annual report",
    r"\bfy\s?(20\d\d)\b",
    r"financial year ended\s+\d{1,2}\s+\w+\s+(20\d\d)",
    r"year ended\s+\d{1,2}\s+\w+\s+(20\d\d)",
    r"sustainability report\s+(20\d\d)",
    r"(20\d\d)\s+sustainability report",
]


def find_report_year(pages: list[str], first_n: int = 4) -> int | None:
    text = normalize(" ".join(pages[:first_n]))
    hits = Counter()
    for pattern in _YEAR_PATTERNS:
        for m in re.finditer(pattern, text):
            hits[int(m.group(1))] += 1
    if hits:
        return hits.most_common(1)[0][0]
    years = Counter(int(y) for y in re.findall(r"\b(20[1-3]\d)\b", text))
    return years.most_common(1)[0][0] if years else None


def year_matches(found: int | None, wanted: int | None) -> bool | None:
    """A report covering FY N is often titled N, or N+1 for non-calendar years."""
    if found is None or wanted is None:
        return None
    return found in (wanted, wanted + 1)


# ---------------------------------------------------------------------------
# Risk-section locator


@dataclass
class RiskSections:
    pages: list[int]          # 1-indexed, sorted
    has_section: bool         # found a risk heading (ToC or page heading), not just keywords
    method: str


def _heading_lines(page_text: str, max_lines: int = 10) -> list[str]:
    lines = [ln.strip() for ln in page_text.splitlines() if ln.strip()]
    return [normalize(ln) for ln in lines[:max_lines] if len(ln) <= 90]


def _heading_candidates(page_text: str, max_lines: int = 10) -> list[str]:
    """Short lines near the top of the page, without leading bullets or numbering, plus each
    pair of adjacent lines joined (headings set as 'RISK' over 'MANAGEMENT')."""
    lines = [re.sub(r"^[\W\d_]+", "", ln) for ln in _heading_lines(page_text, max_lines)]
    joined = [f"{a} {b}" for a, b in zip(lines, lines[1:], strict=False)]
    return [ln for ln in lines + joined if 0 < len(ln) <= 60]


MIN_RISK_MENTIONS = 6   # a page that says "risk" this often still reads as risk discussion


def _is_contents_page(page_text: str) -> bool:
    """A contents page lists section titles; its 'Risk Management' line is not a heading."""
    return any(re.match(r"(table of )?contents\b", line) for line in _heading_lines(page_text, 5))


def locate_risk_sections(pages: list[str], toc: list[tuple[int, str, int]], config: dict,
                         max_pages: int = 40) -> RiskSections:
    headings = [re.compile(h) for h in config["headings"]]
    keywords = [normalize(k) for k in config.get("keywords", [])]
    downweight = [normalize(d) for d in config.get("downweight", [])]
    n = len(pages)
    norm = [normalize(p) for p in pages]

    def is_heading(text: str) -> bool:
        t = normalize(text)
        return any(h.search(t) for h in headings) and not any(d in t for d in downweight)

    def starts_heading(line: str) -> bool:
        """A page heading starts its line: 'Risk Management', not a sentence or a committee's
        name ('internal controls, risk management, ...', 'Audit and Risk Committee')."""
        return any(h.match(line) for h in headings) and not any(d in line for d in downweight)

    def is_whole_heading(line: str) -> bool:
        """The line is nothing but a heading ('Risk Management'), not a wrapped sentence."""
        return any(h.fullmatch(line.strip(" :.-")) for h in headings)

    # 1. Bookmarks: a matching entry covers its pages up to the next entry at the same or
    #    a higher level.
    toc_pages: set[int] = set()
    for i, (level, title, start) in enumerate(toc):
        if start < 1 or not is_heading(title):
            continue
        end = n
        for later_level, _, later_start in toc[i + 1:]:
            if later_level <= level and later_start > start:
                end = later_start - 1
                break
        toc_pages.update(range(start, min(end, start + 24) + 1))

    # 2. Page scores: headings near the top of the page, plus keyword hits.
    scores = [0.0] * n
    heading_pages: set[int] = set()
    for i, text in enumerate(norm):
        in_notes = any(d in text for d in downweight)   # e.g. a financial-statement note
        keyword_hits = sum(text.count(k) for k in keywords)
        # A heading on a page that barely discusses risk is body text that wrapped onto a new
        # line ('risk management systems and ...'), not the start of a risk section.
        discusses_risk = text.count("risk") >= MIN_RISK_MENTIONS or keyword_hits >= 2
        candidates = [ln for ln in _heading_candidates(pages[i]) if starts_heading(ln)]
        if not in_notes and not _is_contents_page(pages[i]) and candidates and (
                discusses_risk or any(is_whole_heading(ln) for ln in candidates)):
            heading_pages.add(i + 1)
            scores[i] += 5
        scores[i] += keyword_hits
        scores[i] += sum(1 for h in headings if h.search(text))
        if any(d in text for d in downweight):
            scores[i] *= 0.3

    def reads_as_risk(p: int) -> bool:
        return scores[p - 1] >= 2 or norm[p - 1].count("risk") >= MIN_RISK_MENTIONS

    # A heading page pulls in following pages while they still look like risk discussion: risk
    # keywords, or "risk" said often (so a page that also cites a financial-risk note, and is
    # scored down for it, doesn't end the section early).
    heading_runs: set[int] = set()
    for start in sorted(heading_pages):
        heading_runs.add(start)
        for p in range(start + 1, min(start + 10, n) + 1):
            if not reads_as_risk(p):
                break
            heading_runs.add(p)

    selected = toc_pages | heading_runs
    method = "bookmarks" if toc_pages else ("headings" if heading_runs else "keywords")
    if not selected:
        selected = {i + 1 for i, s in enumerate(scores) if s >= 4}

    if len(selected) > max_pages:
        ranked = sorted(selected, key=lambda p: (-(scores[p - 1] + (3 if p in toc_pages else 0)), p))
        selected = set(ranked[:max_pages])

    return RiskSections(pages=sorted(selected), has_section=bool(toc_pages or heading_runs),
                        method=method)


def page_tagged_text(pages: list[str], page_numbers: list[int]) -> str:
    return "\n\n".join(f'<page n="{p}">\n{pages[p - 1].strip()}\n</page>' for p in page_numbers)
