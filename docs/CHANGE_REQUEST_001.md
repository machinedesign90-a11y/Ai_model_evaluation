# Change request 001 (2026-11-18)

For Claude Code. This changes work already built or planned. Read `docs/AGENT_SPEC.md` and
`docs/DATA_STANDARD.md` first. These are the owner's decisions; do not reopen them.

## Decisions

1. **One provenance mechanism.** Price and API availability are capabilities (`pricing`, `api_available`)
   with a source and verification status, like every other fact. `models.pricing` and `models.api_available`
   are removed. `models.description` stays as display text only; never use it to support a claim.
2. **Controlled vocabulary.** Capability names and value formats are defined in `capability_definitions`
   and enforced by the database. Names that are not defined are rejected.
3. **Forgiving search.** Exact matching is not acceptable: search is the first impression. Stored data
   stays strict; the search layer translates the user's words and returns closest matches (see
   `docs/AGENT_SPEC.md`, "Principle" and `search_models`).
4. **The MCP server is not exposed publicly in phase one.** On Railway only the agent service gets a public
   URL, and it calls the catalog logic in-process. If the MCP server is exposed later, it must require an API
   key (`MCP_API_KEY`, bearer token) first. Add a test that the HTTP endpoint rejects requests without it
   when `MCP_PUBLIC=true`.
5. **Flat tool schemas for Bedrock.** The agent uses its own flat schemas with no `$ref` or recursion, with a
   test that enforces it. The Step 6 smoke test confirms Bedrock accepts them.

## What to do

1. Add the new migration `supabase/migrations/20261118150000_provenance_and_vocabulary.sql` (provided and
   already tested on PostgreSQL 16). **Do not edit the first migration.** Add `supabase/seed/vocabulary_draft.sql`
   as the starter vocabulary (a draft for the data team to confirm).
2. Update the database test harness to apply both migrations. Add tests for: unknown capability name
   rejected; `"Hindi"` rejected where `hi` is expected; wrong type rejected; a valid fact accepted;
   `pricing` and `api_available` columns gone from `models`; vocabulary readable but not writable by a
   signed-in client.
3. Update `catalog/`: remove every use of `models.pricing` and `models.api_available`; return price and API
   availability as facts. Add `list_capabilities`. Rewrite `search_models` to the forgiving design:
   term resolution (exact, alias, trigram with a threshold you tune and test), per-requirement status,
   `interpretation`, `unresolved`, `full_matches` / `close_matches` / `partial_matches`, never empty when
   the modality has models. Update `get_model` and `compare_models` to resolve names and capability words.
4. Tests for search must include: informal and canonical wording giving the same result; a misspelling;
   an ambiguous term; an unresolved term (reported, not dropped); a not-recorded requirement ranked as
   "can't tell", not "no"; a partial match listing what is missed; an at-least comparison on `max_resolution`
   and `max_clip_seconds`. Fixtures use fictional models only.
5. Add `list_capabilities` to the MCP server (four tools). Keep `save_context` internal to the agent.
6. In the agent: flat schemas plus the schema test; the updated system prompt; the "how I read your words"
   behaviour. Keep `MCP_PUBLIC=false` as the default.
7. Update `README.md` and `.env.example` for the new settings (`MCP_PUBLIC`, `MCP_API_KEY`).
8. Report what changed, what was tested, and what was not.

## Not changing

Free-tier counter, conversations and messages, the chat API contract, the build order, and the rule that
the agent answers only from tool results.
