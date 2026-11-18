"""Tool descriptions the model reads. Shared by the MCP server and the agent's Bedrock tool specs."""

SEARCH_MODELS = """\
Find models for a need described in everyday words. Each requirement has a `capability` and optional `value` \
in the user's own words ("Hindi voiceover", "4K", "background music"); the tool translates them into the \
catalog's terms and reports how in `interpretation` (with confidence exact, alias or approximate). Tell the \
user briefly how you read their words when it matters. Terms it could not read are in `unresolved` with \
suggestions: ask the user one short question or call list_capabilities; never drop them silently.

Results: `full_matches` (every 'must' requirement satisfied), `close_matches` (nothing contradicted but some \
requirement NOT RECORDED: say "can't tell", never "no"), `partial_matches` (some requirement not satisfied; \
each lists `met` and `missed`). Every result carries the facts with source and verification status. \
`operator` defaults to at_least for numbers and ordered values (resolution, clip length), else contains. \
'nice' requirements only change the ranking."""

LIST_CAPABILITIES = """\
The catalog's vocabulary: every capability that can be asked about, its value type, allowed values, unit and \
example wording. Use it when unsure how the user's words map to the data, at most once per conversation."""

GET_MODEL = """\
Everything recorded about one model, found by slug or name (misspellings allowed; `matched_by` says how): \
every fact, including `pricing` and `api_available`, with its source and verification status. A capability \
absent from `facts` is not recorded. `display_description` is display text only: never make claims from it. \
An unknown model returns status 'not_found' with suggestions."""

COMPARE_MODELS = """\
Side-by-side facts for 2 or 3 models (slugs or names). `capability_names` may be everyday words; each \
reading is in `capability_interpretation`. Omit it to compare every capability any of them has. For each \
model, each capability is a sourced fact or listed in `not_recorded`. Unreadable models or capabilities are \
in `unresolved`."""
