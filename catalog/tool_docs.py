"""Tool descriptions the model reads. Shared by the MCP server and the agent's Bedrock tool specs."""

SEARCH_MODELS = """\
Find models whose recorded facts satisfy every required capability.

Returns `matches` (all requirements recorded and satisfied, with the facts and their sources) and \
`unconfirmed` (no recorded fact contradicts the requirements, but some are NOT RECORDED; listed in \
`missing_capabilities`). Report unconfirmed models as "not recorded for X", never as matches. \
`total_models_in_modality` tells how much data exists. If `unknown_capability_names` is non-empty, that \
name is not a defined capability (see `known_capability_names`): retry with a defined name before concluding."""

GET_MODEL = """\
Everything recorded about one model: every fact (including `pricing` and `api_available`) with its \
source and verification status. A capability absent from `facts` is not recorded. `display_description` \
is display text only: never make claims from it. An unknown slug returns status 'not_found' with \
close-match suggestions."""

COMPARE_MODELS = """\
Side-by-side facts for 2 or 3 models. For each model, each requested capability is either a sourced \
fact or listed in `not_recorded`. Unknown slugs are returned in `not_found` with suggestions."""
