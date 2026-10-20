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
- related_listed_entities: listed entities linked to the company whose disclosures must not be credited to it.
