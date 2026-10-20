Build a common list of risk themes so that risks disclosed by different companies can be compared, and map each company's own risk categories onto the standard taxonomy.

Standard taxonomy (category keys):
$taxonomy

Context-inferred risks for the client (ids start with "C"):
$context_risks

Risks disclosed by each company (ids start with "R"):
$disclosed_risks

Each company's own risk categories (ids start with "K"):
$categories

Instructions:
- themes: a list of risk themes, each with a short snake_case key, a name, a taxonomy category key and a one-line description. One theme covers the same underlying risk across companies even when they word it differently ("Rising interest rates" and "Refinancing risk" can share "Interest rate and refinancing"). Keep genuinely different risks apart. Aim for roughly 15 to 35 themes.
- assignments: assign every C and R id to exactly one theme key.
- category_maps: map every K id to the closest taxonomy category key, or "other" if none fits, with a short note when the fit is partial (for example a company category covering both IT and safety).
