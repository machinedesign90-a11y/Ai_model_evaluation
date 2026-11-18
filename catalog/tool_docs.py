"""Tool descriptions the model reads. Shared by the MCP server and the agent's Bedrock tool specs."""

FIND_VERIFIED_QUERY = """\
Tier 1. Look up human-verified queries that ask the same thing as the user's question. Returns up to 3 \
candidates, best first, each with a score and its slots. Scores overlap between true and false matches, so \
judge by meaning: use a candidate only if it truly asks the same thing, then call run_verified_query. If none \
fits, compose a search_models query instead."""

RUN_VERIFIED_QUERY = """\
Tier 1. Run a verified query by id, with slots filled from the user's own words (for example \
{"language": "Hindi"}); values are translated like any search term. An empty slot takes its default; a \
required slot with no value is reported in `unresolved`. Returns the same shape as search_models, plus \
`verified_query` and `slots_used`."""

SEARCH_MODELS = """\
Tier 2. Run a query spec composed from the semantic layer. `filters` are attributes to include or exclude \
(languages, open or closed, deprecated, API); `measures` are quantities to compare (clip length, resolution, \
size, price); `sort` orders by a measure (direction best uses its better direction; models with no recorded \
value come last). Write capability and value in the user's own words ("Hindi voiceover", "closed source", \
"at least 4K", "longer than 10 seconds", sort "cheapest"); the tool translates them and reports how in \
`interpretation` (confidence exact, alias or approximate). Words it could not read are in `unresolved` or \
`ambiguous`, never dropped.

Results: `full_matches` (every 'must' requirement satisfied), `close_matches` (nothing contradicted but some \
requirement NOT RECORDED: say "can't tell", never "no"), `partial_matches` (some requirement not satisfied; \
each lists `met` and `missed`). Every result carries its facts with source and verification status. \
Operators default to at_least for measures where higher is better, at_most where lower is better (price), \
contains for lists, equals otherwise. 'nice' requirements only change the ranking."""

LIST_CAPABILITIES = """\
The semantic layer: every measure and filter with its operators, better direction, unit, allowed values and \
example wording. Use it to refresh your copy or when unsure how the user's words map to the data."""

GET_MODEL = """\
Everything recorded about one model, found by slug or name (misspellings allowed; `matched_by` says how): \
every fact, including prices and `api_available`, with its source and verification status. A capability \
absent from `facts` is not recorded. `display_description` is display text only: never make claims from it. \
An unknown model returns status 'not_found' with suggestions."""

COMPARE_MODELS = """\
Side-by-side facts for 2 or 3 models (slugs or names). `capability_names` may be everyday words; each \
reading is in `capability_interpretation` (a shared word such as "price" lists each fitting capability). \
Omit it to compare every capability any of them has. For each model, each capability is a sourced fact or \
listed in `not_recorded`. Unreadable models or capabilities are in `unresolved`."""
