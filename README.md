# AI model discovery: phase one

A chat agent that answers "which AI model fits my need?" from a curated, sourced
database. Free after registration, limited to a few messages per user.

## Architecture

UI, then the agent service, then a model on Amazon Bedrock, then the MCP tools,
then Postgres (Supabase). The agent is a separate layer on top of the MCP server,
so the MCP server stays a plain data interface that other agents can use too.

## Decisions so far

- Backend language: Python.
- Free-tier limit counts each user message, tied to the registered user.
- Data gathering is a separate activity from the build; the schema is ready for it.
- Bedrock model is chosen later, from the test-set comparison.

## Building with Claude Code

Open this folder in Claude Code. It reads `CLAUDE.md` automatically; the ordered build steps are in
`docs/BUILD_PLAN.md`, the agent and tool spec in `docs/AGENT_SPEC.md`, the chat endpoint contract
(for the UI developer) in `docs/API_CONTRACT.md`, and the rules for filling the catalog (for the data team)
in `docs/DATA_STANDARD.md`. `docs/CHANGE_REQUEST_001.md` (provenance, vocabulary, forgiving search) and
`docs/CHANGE_REQUEST_002.md` (three answer tiers, semantic layer, numeric prices) record the owner's
decisions; both are applied up to build step 3.

## Layout

```
config.py              settings from the environment / .env (fails fast on missing DATABASE_URL)
supabase/migrations/   SQL schema
supabase/seed/         draft vocabulary and draft verified queries (no model data; nothing is verified)
catalog/               shared catalog logic: find/run_verified_query, search_models, list_capabilities,
                       get_model, compare_models, validate_verified_spec
mcp_server/            MCP server wrapping catalog/ (stdio and streamable HTTP)
agent/                 tool_specs.py (flat Bedrock schemas), prompts/system_v3.md, semantic_layer.py (prompt
                       text + cache); loop, API, sessions (step 4+)
tests/                 pytest: schema, vocabulary, search, verified queries, catalog, MCP (real PostgreSQL,
                       fictional models); tier1_paraphrases.py is the tier-1 retrieval test set
tests/eval/            question set for comparing Bedrock models                 (step 8, not built)
scripts/               helper scripts
.env.example           every setting; copy to .env
```

## Development

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/) and PostgreSQL 16 binaries (`initdb`, `pg_ctl`) for tests.

```bash
uv sync
```

```bash
uv run pytest
```

```bash
uv run ruff check . && uv run ruff format --check .
```

Tests start a throwaway PostgreSQL cluster, apply a stub of Supabase's `auth` schema (including
Supabase's default grants, so row-level security is what blocks clients), every migration and both
draft seeds, then give each test a fresh database. Tier-1 tests mark the draft verified queries as verified
in their throwaway database only. To use an existing server instead, set `TEST_DATABASE_URL` to a superuser
connection string; the tests create and drop their own databases there. When run as root the harness
starts PostgreSQL as the `postgres` OS user.

### MCP server

Six read-only tools over the catalog: `find_verified_query`, `run_verified_query`, `search_models`,
`list_capabilities`, `get_model`, `compare_models` (`save_context` and `offer_followups` stay internal to
the agent). Its database connections run with `default_transaction_read_only=on`, so `run_verified_query`
over MCP does not update usage counts; the agent service records those.

```bash
uv run python -m mcp_server --transport stdio
```

```bash
uv run python -m mcp_server --transport http --port 8001
```

```bash
uv run python scripts/mcp_smoke.py http://localhost:8001/mcp
```

Phase one keeps `MCP_PUBLIC=false`: the HTTP transport then binds only to a loopback address and is not
deployed; the agent calls `catalog/` in-process. With `MCP_PUBLIC=true` the server listens on any host and
every request needs `Authorization: Bearer $MCP_API_KEY` (at least 32 characters; startup fails without
it). Uses `mcp` SDK 2.x (`MCPServer`, formerly `FastMCP`).

### Answer tiers

1. **Verified queries.** `find_verified_query` returns up to three human-verified queries whose question
   or paraphrases look like the user's question (score: the mean of `pg_trgm` similarity and
   word_similarity; candidates at or above `VERIFIED_QUERY_FLOOR`, default 0.15). The model judges whether
   one really fits; nothing auto-runs on a score. `run_verified_query` fills its slots from the user's words
   and runs it. Only `status = 'verified'` is ever used, and only a person sets that. On the paraphrase set
   in `tests/tier1_paraphrases.py` the right entry is first 28 of 34 times and in the top three every time.
2. **Semantic layer.** `search_models` takes a query spec (`modality`, `filters`, `measures`, `sort`,
   `limit`) in the user's own words ("closed source", "at least 4K", "longer than 10 seconds", sort
   "cheapest"). Each term resolves through exact name, alias (`capability_aliases`, `value_aliases`, aware
   of the modality so "price" means price per second for video) and `pg_trgm` similarity (0.45 for
   capability names, 0.40 for values; pinned by `tests/test_search.py`). Every reading is reported in
   `interpretation`; unreadable terms in `unresolved` or `ambiguous`. Models come back as full, close (not
   recorded, so can't tell) or partial matches; with a sort, unrecorded values come last.
3. **Review loop.** `queries` logs the tier per turn and `review_queue` groups repeated tier-2 and
   unanswered questions (the logging itself arrives with the agent service, step 4).

`validate_verified_spec` checks a verified query against the current vocabulary; a test runs it over
every seeded query, so renaming or removing a capability fails CI rather than tier 1 in production.

The system prompt (`agent/prompts/system_v3.md`) gets the semantic layer from the database, cached for
`SEMANTIC_LAYER_TTL_SECONDS` (default 3600).

### Data standard

`docs/DATA_STANDARD.md` tells the data team how to record facts and curate verified queries. A test checks
that it mentions every capability defined by the seed.

## Database

Apply the migrations with the Supabase CLI (`supabase db push`) or any Postgres
client, in order, then the two draft seeds (both re-runnable):

```bash
psql "$DATABASE_URL" -f supabase/seed/vocabulary_draft.sql
```

```bash
psql "$DATABASE_URL" -f supabase/seed/verified_queries_draft.sql
```

Migration 3 refuses to run (and rolls back completely) if any `pricing` fact exists. Migration 4 keeps
`review_queue` private: under Supabase's default grants the view was otherwise readable by any signed-in
user.

Tables:

| Table | Purpose |
| --- | --- |
| `models`, `capabilities`, `sources` | The curated catalog. One capability fact per row, each with a source and a verified or unverified status. A missing row means "not recorded". Pricing and API availability are capability rows too; `models.description` is display text only. |
| `capability_definitions`, `capability_aliases`, `value_aliases` | The semantic layer: the only allowed capability names, each a measure or a filter with its operators, better direction, unit and allowed values, plus everyday wording ("voiceover", "Hindi", "4K") mapped to them so search is forgiving. See `docs/DATA_STANDARD.md`. |
| `verified_queries`, `verified_query_phrases` | Tier 1: human-verified question-to-query-spec mappings and their paraphrases. Stores the query, never the answer. Service role only. |
| `profiles` | One row per registered user, with the free-tier counter (`messages_used`, `message_limit`). |
| `conversations`, `messages` | Stored chat history, plus the use case, constraints and shortlist captured for phase two. |
| `queries`, `review_queue` | Log of questions with the tier that answered, the spec that ran and time to first text; the view groups repeated tier-2 and unanswered questions for review. Service role only. |

### Free-tier counter

Call `consume_message(user_id)` before each model call. It returns
`(allowed, remaining)` and increments atomically, so concurrent requests cannot
exceed the limit. It is callable only with the service role.

### Verified facts

A capability marked `verified` must have `last_verified_at` set (the database
enforces this). Scraped or synced facts stay `unverified` until a person confirms them.

## Tested

All four migrations and both seeds are applied to a clean PostgreSQL 16 with a stub of Supabase's `auth`
schema in every test run (`uv run pytest`), plus the upgrade path from the change request 001 state and
migration 3's failure and rollback. Not yet run against a real Supabase project.

## Status

Done: steps 0 to 3 of `docs/BUILD_PLAN.md` with change requests 001 and 002 applied to them (semantic
layer and forgiving search, tier-1 verified queries, six MCP tools, `MCP_PUBLIC` / `MCP_API_KEY`), the
agent's flat Bedrock tool specs, system prompt v3 and the semantic-layer cache.

Next:

1. Agent service (step 4): Bedrock loop, the three tiers, sessions, the counter, `save_context` and
   `offer_followups`, the `queries` row per turn with `ttft_ms`, and draft suggestions from tier 2.
2. Chat API (step 5): `suggestions` in `done`, `POST /v1/messages/{id}/feedback`, and `MOCK_AGENT` mode.
