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

## Layout (planned)

```
supabase/migrations/   SQL schema (done)
mcp_server/            search_models, get_model, compare_models  (next)
agent/                 Bedrock Converse loop, streaming chat endpoint, sessions
tests/                 about twenty question and answer checks for the agent
.env.example           every setting; copy to .env
```

## Database

Apply the migration with the Supabase CLI (`supabase db push`) or any Postgres
client. Tables:

| Table | Purpose |
| --- | --- |
| `models`, `capabilities`, `sources` | The curated catalog. One capability fact per row, each with a source and a verified or unverified status. A missing row means "not recorded". |
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

## Next

1. MCP server with the three tools.
2. Agent service with the Bedrock loop, streaming, sessions and the counter.
3. Chat endpoint contract (with a mock response) for the UI developer.
