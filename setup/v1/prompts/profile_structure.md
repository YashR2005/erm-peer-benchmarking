Turn these research notes into a structured company profile.

<research_notes>
$notes
</research_notes>

<citations>
$citations
</citations>

Rules:
- Each fact is one short statement. Give the ids of the citations that support it (e.g. "c3"). If no citation supports a fact, leave citation_ids empty; don't attach a citation that doesn't say it.
- market_cap_sgd_millions: a number in SGD millions, or null if not found.
- Use null or empty lists for anything the notes don't contain.
- The figure fields are shown in small tiles, so keep them short and follow each field's format (e.g. "S$$27.0B (FY2025)", a date as YYYY-MM-DD, an exchange code like SGX). Put caveats, such as sources disagreeing or a different accounting basis, in data_notes, and the detail in a fact.
- related_listed_entities: listed entities linked to the company whose disclosures must not be credited to it.
