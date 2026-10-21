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


_load = StandardSetup.load.__func__


def _without_peer_cap(cls, *args, **kwargs) -> StandardSetup:
    s = _load(cls, *args, **kwargs)
    s.method["peers"].pop("max_count", None)
    return s


@pytest.fixture(autouse=True)
def no_trial_peer_cap(request, monkeypatch):
    """The pipeline tests cover the general case (three or more peer candidates), so they run
    without the trial's peer cap (peers.max_count). Tests that use trial_setup keep it."""
    if "trial_setup" not in request.fixturenames:
        monkeypatch.setattr(StandardSetup, "load", classmethod(_without_peer_cap))


@pytest.fixture(scope="session")
def setup() -> StandardSetup:
    return _without_peer_cap(StandardSetup)


@pytest.fixture
def trial_setup() -> StandardSetup:
    return StandardSetup.load()
