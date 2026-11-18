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
`docs/BUILD_PLAN.md`, the agent and tool spec in `docs/AGENT_SPEC.md`, and the chat endpoint contract
(for the UI developer) in `docs/API_CONTRACT.md`.

## Layout

```
config.py              settings from the environment / .env (fails fast on missing DATABASE_URL)
supabase/migrations/   SQL schema
catalog/               shared catalog logic: search_models, get_model, compare_models
mcp_server/            MCP server wrapping catalog/ (stdio and streamable HTTP)
agent/                 tool_specs.py (flat Bedrock tool schemas); Bedrock loop, chat endpoint, sessions (step 4+)
tests/                 pytest: schema, catalog, MCP (real PostgreSQL, fictional fixture data)
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
Supabase's default grants, so row-level security is what blocks clients) and every migration, then give
each test a fresh database. To use an existing server instead, set `TEST_DATABASE_URL` to a superuser
connection string; the tests create and drop their own databases there. When run as root the harness
starts PostgreSQL as the `postgres` OS user.

### MCP server

Read-only tools over the catalog: `search_models`, `get_model`, `compare_models`. Its database
connections run with `default_transaction_read_only=on`.

```bash
uv run python -m mcp_server --transport stdio
```

```bash
uv run python -m mcp_server --transport http --port 8001
```

```bash
uv run python scripts/mcp_smoke.py http://localhost:8001/mcp
```

Uses `mcp` SDK 2.x (`MCPServer`, formerly `FastMCP`). The HTTP transport has no authentication, so in
phase one it only binds to a loopback address and is not deployed: the agent calls `catalog/` in-process.
Before outside agents use it, put an API key in front of it.

### Data standard

`docs/DATA_STANDARD.md` tells the data team how to record facts. Its vocabulary table is generated from
`capability_definitions`; after adding a name in a new migration, refresh it:

```bash
uv run python scripts/data_standard.py
```

## Database

Apply the migrations with the Supabase CLI (`supabase db push`) or any Postgres
client. Tables:

| Table | Purpose |
| --- | --- |
| `models`, `capabilities`, `sources` | The curated catalog. One capability fact per row, each with a source and a verified or unverified status. A missing row means "not recorded". Pricing and API availability are capability rows too; `models.description` is display text only. |
| `capability_definitions`, `language_codes` | The controlled vocabulary: allowed capability names, value types, allowed values, and ISO 639-1 language codes. The database rejects anything else. See `docs/DATA_STANDARD.md`. |
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

The migration was applied to a clean PostgreSQL 16 instance with a stub of
Supabase's `auth` schema. Checked: profile auto-creation on sign-up, the counter
(allows up to the limit, then blocks, also blocks unknown users), the verified-needs-date
rule, and row-level security (users see only their own profile and conversations,
cannot write the catalog, cannot call `consume_message`). Not yet run against a real
Supabase project.

## Status

Done: steps 0 to 3 of `docs/BUILD_PLAN.md` (setup, database harness, catalog module, MCP server), the
capability vocabulary migration, and the agent's flat Bedrock tool specs.

Next:

1. Agent service with the Bedrock loop, streaming, sessions and the counter (step 4).
2. Chat endpoint contract with a mock mode for the UI developer (step 5).
