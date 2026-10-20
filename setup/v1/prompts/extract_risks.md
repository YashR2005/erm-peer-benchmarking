Below is text from the risk-related pages of $company_name's $doc_title. Each page is wrapped in a <page n="..."> tag with its page number in the PDF.

$pages

Extract two things.

1. The company's risk category structure: the groupings the company itself uses to organise its risks, either as an explicit list ("our risks are grouped into strategic, financial, ...") or as headings under which risks are described. Keep the company's names exactly. Record sub-categories if the company has them. If the company lists risks without grouping them, return no categories and say so in category_structure_note. Don't invent groupings.

2. Every distinct risk the company discloses about itself, for example "Interest rate risk" or "Delays in development projects". For each:
   - title: the company's own name for the risk.
   - summary: one sentence on how the company describes it.
   - company_category: the company's category it sits under, if any.
   - quote: a short verbatim passage (one or two sentences, at most about 60 words) copied exactly from the page, character for character. It will be checked against the PDF text automatically, so do not paraphrase, fix typos or join text from different places.
   - page: the page number from the <page> tag the quote comes from.

Skip general statements about risk management that don't name a risk, and risks of other entities (for example a REIT the company sponsors). If the pages contain no risk disclosure, set has_risk_disclosure to false.
