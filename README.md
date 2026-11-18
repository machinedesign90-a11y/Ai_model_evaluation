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
in `docs/DATA_STANDARD.md`. `docs/CHANGE_REQUEST_001.md` records the owner's decisions on provenance,
vocabulary and forgiving search (applied).

## Layout

```
config.py              settings from the environment / .env (fails fast on missing DATABASE_URL)
supabase/migrations/   SQL schema
supabase/seed/         draft starter vocabulary (names, formats, aliases; no model data)
catalog/               shared catalog logic: search_models, list_capabilities, get_model, compare_models
mcp_server/            MCP server wrapping catalog/ (stdio and streamable HTTP)
agent/                 tool_specs.py (flat Bedrock schemas), prompts/ (system prompt); loop, API, sessions (step 4+)
tests/                 pytest: schema, vocabulary, search, catalog, MCP (real PostgreSQL, fictional models)
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
Supabase's default grants, so row-level security is what blocks clients), every migration and the draft
vocabulary seed, then give each test a fresh database. To use an existing server instead, set `TEST_DATABASE_URL` to a superuser
connection string; the tests create and drop their own databases there. When run as root the harness
starts PostgreSQL as the `postgres` OS user.

### MCP server

Four read-only tools over the catalog: `search_models`, `list_capabilities`, `get_model`,
`compare_models` (`save_context` stays internal to the agent). Its database connections run with
`default_transaction_read_only=on`.

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

### Search

Stored data is strict; search is forgiving. `search_models` takes requirements in the user's words
("Hindi voiceover", "4K") and resolves each one: exact canonical term, then an alias from
`capability_aliases` / `value_aliases`, then `pg_trgm` similarity (0.45 for capability names, 0.40 for
values, measured against the draft vocabulary and pinned by `tests/test_search.py`). Every reading is
reported in `interpretation`, unreadable terms in `unresolved`, and models come back as full, close
(not recorded, so can't tell) or partial matches. The cheapest way to improve search is adding aliases;
no code change needed.

### Data standard

`docs/DATA_STANDARD.md` tells the data team how to record facts. A test checks that it mentions every
capability defined by the seed.

## Database

Apply the migrations with the Supabase CLI (`supabase db push`) or any Postgres
client, then the draft vocabulary (re-runnable):

```bash
psql "$DATABASE_URL" -f supabase/seed/vocabulary_draft.sql
```

Tables:

| Table | Purpose |
| --- | --- |
| `models`, `capabilities`, `sources` | The curated catalog. One capability fact per row, each with a source and a verified or unverified status. A missing row means "not recorded". Pricing and API availability are capability rows too; `models.description` is display text only. |
| `capability_definitions`, `capability_aliases`, `value_aliases` | The controlled vocabulary: the only allowed capability names and value formats, plus everyday wording ("voiceover", "Hindi", "4K") mapped to them so search is forgiving. See `docs/DATA_STANDARD.md`. |
| `profiles` | One row per registered user, with the free-tier counter (`messages_used`, `message_limit`). |
| `conversations`, `messages` | Stored chat history, plus the use case, constraints and shortlist captured for phase two. |
| `queries` | Log of questions and which ones could not be answered, to find data gaps. |

### Free-tier counter

Call `consume_message(user_id)` before each model call. It returns
`(allowed, remaining)` and increments atomically, so concurrent requests cannot
exceed the limit. It is callable only with the service role.

### Verified facts

A capability marked `verified` must have `last_verified_at` set (the database
enforces this). Scraped or synced facts stay `unverified` until a person confirms them.

## Tested

Both migrations and the seed are applied to a clean PostgreSQL 16 with a stub of Supabase's `auth`
schema in every test run (`uv run pytest`). Not yet run against a real Supabase project.

## Status

Done: steps 0 to 3 of `docs/BUILD_PLAN.md` with change request 001 applied (vocabulary and forgiving
search, four MCP tools, `MCP_PUBLIC` / `MCP_API_KEY`), the agent's flat Bedrock tool specs and system
prompt v2.

Next:

1. Agent service with the Bedrock loop, streaming, sessions and the counter (step 4).
2. Chat endpoint contract with a mock mode for the UI developer (step 5).
