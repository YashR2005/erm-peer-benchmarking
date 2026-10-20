Turn these peer research notes into a structured candidate list.

<research_notes>
$notes
</research_notes>

<citations>
$citations
</citations>

Rules:
- One entry per candidate company, at most $candidate_count. Set recommended=true for the $peer_count best candidates and false for the alternatives.
- citation_ids: ids of the citations that support that candidate's figures. Leave empty rather than attach an unrelated citation.
- market_cap_sgd_millions: a number in SGD millions, or null if not found.
- Keep financial figures as short text with currency and year (e.g. "S$$3.1B (FY2024)"), or null.
- operating_countries: a count or range as text (e.g. "6-10"), or null.
- The figure fields are shown in small table cells, so keep them short and follow each field's format. Put caveats (unverified, sources disagree, currency conversion) in data_notes.
