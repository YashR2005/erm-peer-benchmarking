from __future__ import annotations

import pymupdf
import pytest

from erm_bench.standard_setup import StandardSetup


def make_pdf(pages: list[str], toc: list[list] | None = None) -> bytes:
    """A small text PDF, one string per page, with optional bookmarks [level, title, page]."""
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(50, 50, 550, 800), text, fontsize=10)
    if toc:
        doc.set_toc(toc)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture(scope="session")
def setup() -> StandardSetup:
    return StandardSetup.load()
