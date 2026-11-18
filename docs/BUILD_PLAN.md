# Build plan

If the build is already underway, apply `docs/CHANGE_REQUEST_001.md` and then `docs/CHANGE_REQUEST_002.md` first. Then do these in order. Each step lists what to build and the acceptance checks. Do not start a step
until the previous one passes its checks. Commit after each step.

## Step 0: Project setup

- `git init`, `.gitignore` (include `.env`, `.venv`, `__pycache__`).
- Python project with `pyproject.toml`, a virtualenv, and dependencies pinned.
- Layout:
  ```
  catalog/        shared data-access logic (queries, formatting)
  mcp_server/     MCP server exposing the catalog tools
  agent/          FastAPI app, Bedrock adapter, tool loop, sessions, free-tier logic
  tests/          pytest tests (unit, database, API)
  tests/eval/     question set and runner for comparing Bedrock models (step 8)
  scripts/        helper scripts
  ```
- Config module that reads `.env` settings via pydantic; fails fast on missing required values.
- **Accept:** `pytest` runs (even with zero tests), the app imports, lint passes.

## Step 1: Database test harness

- A pytest fixture that starts from a clean Postgres, creates a stub `auth` schema (roles `anon`,
  `authenticated`; table `auth.users(id, email)`; function `auth.uid()` reading
  `request.jwt.claim.sub`), applies the migration, and tears down.
- Add fixture data with **fictional** models and capabilities only.
- **Accept:** tests prove: profile created on sign-up; `consume_message` allows exactly
  `message_limit` calls then blocks (including under concurrent calls); verified fact without a date is
  rejected; users cannot read other users' conversations; an unknown capability name, a wrong value
  format (for example "Hindi" where `hi` is required) and a wrong type are rejected by the vocabulary
  rules; the vocabulary is readable but not writable by a signed-in client.

## Step 2: Catalog module (`catalog/`)

Implement, with typed pydantic models and parameterized SQL:

- `search_models(query spec: modality, filters, measures, sort, limit)`: forgiving, not exact-match (free-text terms resolved
  through aliases and trigram similarity; closest matches returned; see `docs/AGENT_SPEC.md`)
- `list_capabilities(modality)` (returns the measures and filters)
- `find_verified_query(question)` and `run_verified_query(id, slots)` (tier 1; only `verified` entries)
- `get_model(slug)`
- `compare_models(slugs, capability_names)`

Details are in `docs/AGENT_SPEC.md` ("Tool contracts"). Key behaviours: every fact is returned with
its source and verification status (price and API availability are facts like any other); requested
capabilities with no row appear under `not_recorded` and count as "can't tell", never "no"; `search_models`
reports how it read each term, lists unrecognised terms instead of dropping them, and returns full, close
and partial matches so it is never empty when the modality has models.

- **Accept:** unit and database tests cover match, no match, unrecorded capability, unverified versus
  verified, 2 to 3 model comparison, invalid slug, and SQL-injection-style inputs (treated as data).

## Step 3: MCP server (`mcp_server/`)

- Expose the six catalog tools from Step 2 using the official MCP Python SDK, wrapping `catalog/`. Clear tool descriptions
  (the model reads them). Support streamable HTTP and stdio.
- **Accept:** a test or script connects with an MCP client, lists the tools, and calls each one
  against fixture data.

## Step 4: Agent core (`agent/`)

- Bedrock adapter using the Converse API with tool use, model from `BEDROCK_MODEL_ID`. Isolate it behind
  an interface so it can be faked in tests and swapped later.
- Tool loop with `MAX_TOOL_CALLS_PER_TURN` and `MAX_OUTPUT_TOKENS`.
- System prompt and the internal `save_context` tool exactly as in `docs/AGENT_SPEC.md`.
- Flat tool schemas for Bedrock (no `$ref`, `$defs`, `anyOf`, `oneOf` or recursion), with a test that fails if
  any appears.
- Three-tier flow per `docs/AGENT_SPEC.md`: verified query, then semantic search, then logging. Write a
  `queries` row each turn (tier, spec, `ttft_ms`). Internal tools `save_context` and `offer_followups`.
  System prompt v3, with the semantic layer loaded from the database and cached.
- Conversation storage: create or resume a conversation, apply the 24-hour expiry rule, store messages
  with token counts, update `last_activity_at`.
- Call `consume_message` once per user message, before the model call.
- **Accept:** tests with a fake model verify: tool loop terminates at the cap; messages persist; an expired
  conversation is replaced; the counter is consumed once per message; the limit blocks the call before the
  model is invoked; `save_context` updates the conversation row.

## Step 5: Chat API (`agent/api`)

Implement exactly `docs/API_CONTRACT.md`: `POST /v1/chat` (Server-Sent Events), `GET /v1/me`,
`GET /v1/conversations/{id}`, `POST /v1/messages/{id}/feedback`, `GET /healthz`. Validate the Supabase JWT on every request except health.
Include a `MOCK_AGENT=true` mode that streams canned responses and the limit-reached case so the UI
developer can build against it before Bedrock is connected.

- **Accept:** API tests cover auth failure, success stream, limit-reached, expired conversation, and mock mode.

## Step 6: Wire real Bedrock

- Real `BedrockModel` implementation with streaming. Handle throttling and transient errors with retry
  and a clean `error` event. Do not count a message that failed before any model work started; decide
  and document the policy for failures after tool calls (see open question in `AGENT_SPEC.md`).
  A refund needs a `refund_message(user_id)` function, which does not exist yet: add it in a **new**
  migration file (never edit the applied one), service-role only like `consume_message`, and test it.
- **Accept:** a manual smoke test script (`scripts/smoke_chat.py`) runs one real question end to end when
  AWS credentials are present; automated tests still use the fake model.

## Step 7: Deploy

- `Dockerfile` (or Railway config), start command, health check, environment variable list.
- Document deployment to Railway and the environment variables in `README.md`.
- Only the agent service is public. The MCP server is not exposed (`MCP_PUBLIC=false`). If it is exposed
  later it must require a bearer API key (`MCP_API_KEY`), with a test that rejects unauthenticated requests.
- Add structured logging (no message content or secrets in logs at info level) and basic request IDs.
- **Accept:** container builds and passes the health check locally.

## Step 8: Agent evaluation harness (needs data first)

- `tests/eval/questions.yaml` format: `question`, `expected` (facts that must appear), `must_say_not_recorded`
  (boolean), `must_not_claim` (list). Provide the schema and 3 fictional examples.
- `scripts/run_eval.py --model <bedrock-model-id>` runs every question through the real agent and writes a
  results table (pass or fail per question, tokens, latency, cost estimate).
- The harness reports per question which tier answered it, time to first text, tokens and estimated cost, so
  tier-1 coverage and latency are visible.
- **Accept:** runs against fixture data with the fake model; the owner fills real questions once the database is populated.

## Out of scope for this build

- Populating the model database (separate human-led activity).
- Phase two: evaluation runs, credits, billing, workers.
- The UI.

## Open questions to ask the owner when reached

- Whether to count a user message if the model call fails after tool calls have already run.
- Which Bedrock model to start with, once step 8 gives evidence.
- Final free-tier limit (default 4).
- Similarity threshold and tie rules for approximate matching (start at 0.4, tune against the test set).
- Tier-1 candidate floor (start 0.15) and whether `pgvector` is needed after measuring top-3 hit rates.
