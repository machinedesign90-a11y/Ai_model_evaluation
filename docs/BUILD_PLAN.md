# Build plan

Do these in order. Each step lists what to build and the acceptance checks. Do not start a step
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
  rejected; users cannot read other users' conversations.

## Step 2: Catalog module (`catalog/`)

Implement, with typed pydantic models and parameterized SQL:

- `search_models(modality, required_capabilities, limit)`
- `get_model(slug)`
- `compare_models(slugs, capability_names)`

Details are in `docs/AGENT_SPEC.md` ("Tool contracts"). Key behaviours: every fact is returned with
its source and verification status; requested capabilities with no row appear under `not_recorded`;
`search_models` also reports candidates whose capability is unrecorded, so the agent never silently
drops them.

- **Accept:** unit and database tests cover match, no match, unrecorded capability, unverified versus
  verified, 2 to 3 model comparison, invalid slug, and SQL-injection-style inputs (treated as data).

## Step 3: MCP server (`mcp_server/`)

- Expose the three tools using the official MCP Python SDK, wrapping `catalog/`. Clear tool descriptions
  (the model reads them). Support streamable HTTP and stdio.
- **Accept:** a test or script connects with an MCP client, lists the tools, and calls each one
  against fixture data.

## Step 4: Agent core (`agent/`)

- Bedrock adapter using the Converse API with tool use, model from `BEDROCK_MODEL_ID`. Isolate it behind
  an interface so it can be faked in tests and swapped later.
- Tool loop with `MAX_TOOL_CALLS_PER_TURN` and `MAX_OUTPUT_TOKENS`.
- System prompt and the internal `save_context` tool exactly as in `docs/AGENT_SPEC.md`.
- Conversation storage: create or resume a conversation, apply the 24-hour expiry rule, store messages
  with token counts, update `last_activity_at`.
- Call `consume_message` once per user message, before the model call.
- **Accept:** tests with a fake model verify: tool loop terminates at the cap; messages persist; an expired
  conversation is replaced; the counter is consumed once per message; the limit blocks the call before the
  model is invoked; `save_context` updates the conversation row.

## Step 5: Chat API (`agent/api`)

Implement exactly `docs/API_CONTRACT.md`: `POST /v1/chat` (Server-Sent Events), `GET /v1/me`,
`GET /v1/conversations/{id}`, `GET /healthz`. Validate the Supabase JWT on every request except health.
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
- Add structured logging (no message content or secrets in logs at info level) and basic request IDs.
- **Accept:** container builds and passes the health check locally.

## Step 8: Agent evaluation harness (needs data first)

- `tests/eval/questions.yaml` format: `question`, `expected` (facts that must appear), `must_say_not_recorded`
  (boolean), `must_not_claim` (list). Provide the schema and 3 fictional examples.
- `scripts/run_eval.py --model <bedrock-model-id>` runs every question through the real agent and writes a
  results table (pass or fail per question, tokens, latency, cost estimate).
- **Accept:** runs against fixture data with the fake model; the owner fills real questions once the database is populated.

## Out of scope for this build

- Populating the model database (separate human-led activity).
- Phase two: evaluation runs, credits, billing, workers.
- The UI.

## Open questions to ask the owner when reached

- Whether to count a user message if the model call fails after tool calls have already run.
- Which Bedrock model to start with, once step 8 gives evidence.
- Final free-tier limit (default 4).
