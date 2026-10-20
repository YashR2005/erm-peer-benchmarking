"""The versioned standard setup: method settings, taxonomy, sector guides, prompts."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from string import Template

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SETUP_ROOT = REPO_ROOT / "setup"
DEFAULT_VERSION = "v1"


@dataclass(frozen=True)
class Bucket:
    key: str
    label: str
    min: float | None = None
    max: float | None = None

    def contains(self, value: float) -> bool:
        return (self.min is None or value >= self.min) and (self.max is None or value < self.max)

    def describe(self, currency: str) -> str:
        if self.min is not None and self.max is not None:
            return f"{self.label}: {currency} {self.min:,.0f}M to {self.max:,.0f}M market cap"
        if self.min is not None:
            return f"{self.label}: {currency} {self.min:,.0f}M market cap or more"
        return f"{self.label}: below {currency} {self.max:,.0f}M market cap"


@dataclass
class SectorGuide:
    key: str
    name: str
    match: list[str]
    where_to_look: list[str]
    peer_hints: list[str]
    typical_risks: list[str]


@dataclass
class StandardSetup:
    version: str
    root: Path
    method: dict = field(repr=False)

    @classmethod
    def load(cls, version: str = DEFAULT_VERSION, root: Path = SETUP_ROOT) -> StandardSetup:
        base = root / version
        method = yaml.safe_load((base / "method.yaml").read_text())
        return cls(version=version, root=base, method=method)

    @property
    def name(self) -> str:
        return self.method["name"]

    @property
    def provider(self) -> str:
        return os.environ.get("ERM_LLM_PROVIDER") or self.method["llm"]["provider"]

    @property
    def model(self) -> str:
        model = os.environ.get("ERM_LLM_MODEL") or self.method["llm"]["models"][self.provider]
        allowed = self.method["llm"].get("allowed_models")
        if allowed and model not in allowed:
            raise ValueError(f"Model '{model}' ({self.provider}) is not allowed. Use one of "
                             f"{', '.join(allowed)} (llm.allowed_models in setup/{self.version}/"
                             "method.yaml; ERM_LLM_PROVIDER / ERM_LLM_MODEL in .env).")
        return model

    def provider_settings(self) -> dict:
        return self.method["llm"].get(self.provider, {})

    def effort(self, kind: str) -> str:
        return self.method["effort"][kind]

    def research_budget(self, kind: str) -> dict:
        """Web searches and page fetches allowed for one research call."""
        budget = self.method.get("research", {}).get("budgets", {}).get(kind, {})
        return {"max_searches": budget.get("searches", 5), "max_fetches": budget.get("fetches", 2)}

    def time_limit(self, kind: str) -> float | None:
        """Seconds allowed for a stage ('research', 'analysis') or one model call ('per_call')."""
        return self.method.get("time_limits", {}).get(kind)

    @property
    def max_fetch_tokens(self) -> int:
        return self.method.get("research", {}).get("max_fetch_tokens", 6000)

    @cached_property
    def taxonomy(self) -> list[dict]:
        return yaml.safe_load((self.root / "taxonomy.yaml").read_text())["categories"]

    @property
    def taxonomy_keys(self) -> list[str]:
        return [c["key"] for c in self.taxonomy]

    def taxonomy_name(self, key: str) -> str:
        return next((c["name"] for c in self.taxonomy if c["key"] == key), "Other")

    @cached_property
    def filing_sections(self) -> dict:
        return yaml.safe_load((self.root / "filing_sections.yaml").read_text())

    @cached_property
    def sector_guides(self) -> list[SectorGuide]:
        guides = []
        for path in sorted((self.root / "sector_guides").glob("*.yaml")):
            guides.append(SectorGuide(**yaml.safe_load(path.read_text())))
        return guides

    def sector_guide_for(self, *texts: str | None) -> SectorGuide:
        haystack = " ".join(t for t in texts if t).lower()
        best, best_hits = None, 0
        for guide in self.sector_guides:
            hits = sum(1 for word in guide.match if re.search(rf"\b{re.escape(word)}", haystack))
            if hits > best_hits:
                best, best_hits = guide, hits
        return best or next(g for g in self.sector_guides if g.key == "general")

    # Market-cap buckets

    @property
    def bucket_currency(self) -> str:
        return self.method["market_cap_buckets"]["currency"]

    @cached_property
    def buckets(self) -> list[Bucket]:
        return [Bucket(**b) for b in self.method["market_cap_buckets"]["buckets"]]

    def bucket_for(self, market_cap_millions: float | None) -> str | None:
        if market_cap_millions is None:
            return None
        return next((b.key for b in self.buckets if b.contains(market_cap_millions)), None)

    def bucket(self, key: str) -> Bucket:
        return next(b for b in self.buckets if b.key == key)

    # Prompts

    def prompt(self, name: str, **values: object) -> str:
        text = (self.root / "prompts" / f"{name}.md").read_text()
        return Template(text).substitute({k: str(v) for k, v in values.items()})

    def prompt_version(self, name: str) -> str:
        text = (self.root / "prompts" / f"{name}.md").read_bytes()
        return f"{self.version}/{name}@{hashlib.sha256(text).hexdigest()[:10]}"
