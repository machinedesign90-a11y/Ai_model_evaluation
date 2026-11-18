# Change request 002 (2026-11-18)

For Claude Code. Apply `docs/CHANGE_REQUEST_001.md` first if it is not done. This builds on it and changes
the search design. Read `docs/AGENT_SPEC.md` (rewritten) and `docs/DATA_STANDARD.md` first. These are the
owner's decisions; do not reopen them.

## Why

Search results are the first impression and the top of the funnel, so quality and speed matter most.
The design becomes three tiers: **verified queries**, then a **semantic layer of measures and filters** the
agent queries through, then a **review loop** that improves both from unanswered questions.

## Decisions

1. **Tier 1: verified queries.** Human-verified mappings from a question to a structured query spec, stored in
   `verified_queries` with concrete paraphrases in `verified_query_phrases`. They store the **query, never an
   answer**. Only `status = 'verified'` is used by the agent.
2. **Tier 2: semantic layer.** Every capability is a **measure** (compared, thresholded, sorted) or a
   **filter** (included or excluded). The agent composes a query spec; the server validates it against the
   layer and compiles parameterized SQL. **The model still never writes SQL.**
3. **Tier 3: review loop.** Each turn is logged with its tier; `review_queue` surfaces repeated tier-2 and
   unanswered questions. Improve in order: verified query, then measures/filters/aliases, then data gaps.
4. **Only a person verifies a query.** Code and the model may create `draft` entries (for example
   `source = 'suggested_from_log'`), never `verified`.
5. **Prices are comparable numbers**, one measure per unit (`price_usd_per_second` and so on). The JSON
   `pricing` capability is removed.
6. **Answer experience** (see "Answer experience" in the spec): lead with a shortlist, cite sources, never a
   dead end, do not spend a free message on an avoidable clarifying question, offer two follow-ups, and track
   time to first text.

## What to do

1. **Database.** Add `supabase/migrations/20261118180000_semantic_layer_and_verified_queries.sql` (provided
   and tested; do not edit the earlier migrations). It runs in a transaction and **fails loudly if a `pricing`
   fact exists**. Apply in this order: migrations 1, 2, 3, then `supabase/seed/vocabulary_draft.sql`, then
   `supabase/seed/verified_queries_draft.sql`. Update the test harness to do the same.
2. **Database tests**: `kind`, `operators` and `better` constraints (a measure on a boolean is rejected;
   `better` on a filter is rejected; an unknown operator is rejected); a verified query needs `verified_by`
   and `verified_at`; the canonical question is unique; `review_queue` groups variants that differ only in
   case, spacing or punctuation, and keeps non-Latin text such as Devanagari intact; feedback accepts only
   -1 or 1; signed-in clients cannot read `verified_queries` or `review_queue`; migration 3 fails and rolls
   back completely when a `pricing` fact exists; the upgrade path works when the earlier seed was already
   applied.
3. **Catalog module.**
   - Rewrite `search_models` to take the query spec (`modality`, `filters`, `measures`, `sort`, `limit`).
     Implement term resolution, operators including `at_least` and `at_most` on numbers and ordered enums,
     modality-aware alias resolution (the word "price" maps to several capabilities), single-phrase resolution
     (for example "closed source" gives `open_weights = false`), per-requirement status, `interpretation`,
     `unresolved`, `ambiguous`, `full_matches` / `close_matches` / `partial_matches`, and sort with unrecorded
     values last. Move a mis-placed entry (a measure under filters) and report it.
   - Add `find_verified_query` and `run_verified_query` (slot substitution, defaults, validation).
   - Make `list_capabilities` return `kind`, `operators`, `better`, unit and allowed values.
   - Add a validator and a CI test that every verified query's spec is valid against the current
     `capability_definitions`.
4. **Tier-1 retrieval.** Rank by the higher of trigram `similarity` and `word_similarity` over the phrases
   and the canonical question. Return the top 3 above a low floor (`VERIFIED_QUERY_FLOOR`, start 0.15) and let
   the model decide. **Do not auto-run on a score.** In a first test the correct entry was in the top 2 every
   time but scores overlapped with non-matches. Build a small paraphrase test set (including informal and
   misspelled phrasing) and report the top-1 and top-3 hit rates.
5. **Agent.** System prompt v3 with the semantic layer injected from the database and cached
   (`SEMANTIC_LAYER_TTL_SECONDS`, default 3600). Internal tools `save_context` and `offer_followups`. Write a
   `queries` row every turn: question, answered, tier, verified query id, spec, missing capabilities,
   `ttft_ms`. When tier 2 answers well, store the spec as a `draft` suggested verified query (not verified).
   Keep flat tool schemas and the schema test.
6. **API.** Add `suggestions` to the `done` event and `POST /v1/messages/{id}/feedback` per
   `docs/API_CONTRACT.md`. Update the `MOCK_AGENT` mode to include both.
7. **Tests** for the behaviours listed in "Behaviour requirements" in `docs/AGENT_SPEC.md`. Fixtures use
   fictional models only. Do not mark any verified query as verified, and do not add catalog facts.
8. **Docs and settings.** Update `README.md` and `.env.example`.
9. Report what changed, what was tested, and what was not.

## Not changing

Free-tier counter, conversations and messages (apart from the feedback columns), the chat API (apart from the
two additions), the build order, the rule that answers come only from tool results, and the rule that the MCP
server is not exposed publicly in phase one.
