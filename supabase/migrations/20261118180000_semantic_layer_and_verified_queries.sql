begin;  -- all or nothing: if any step fails, nothing is applied

-- Migration 3: semantic layer (measures and filters), verified queries (tier 1),
-- a review queue for unanswered questions, and comparable price measures.
--
-- Assumes no price facts are recorded yet (the catalog is still empty). If any
-- `pricing` fact exists, the delete below fails loudly instead of losing data.

-- ---------------------------------------------------------------------------
-- 1. Semantic layer: every capability is either a measure or a filter.
--    measure = a quantity you compare, threshold or sort (clip length, price)
--    filter  = an attribute you include or exclude (languages, open weights)
-- ---------------------------------------------------------------------------
alter table capability_definitions
  add column kind      text   not null default 'filter' check (kind in ('measure', 'filter')),
  add column operators text[] not null default '{}'
             check (operators <@ array['equals', 'contains', 'at_least', 'at_most']::text[]),
  add column better    text   check (better in ('higher', 'lower'));  -- which direction wins when ranking a measure

alter table capability_definitions
  add constraint measures_are_orderable
    check (kind = 'filter' or value_type in ('number', 'enum')),
  add constraint better_only_for_measures
    check (better is null or kind = 'measure');

comment on column capability_definitions.kind is
  'measure: compared, thresholded or sorted. filter: included or excluded. For enum measures the allowed_values order is the ranking (lowest first).';
comment on column capability_definitions.better is
  'For measures: whether higher or lower is better when ranking. Null when neither is better (for example model size).';

-- Sensible default operators for definitions that already exist.
update capability_definitions
   set operators = case value_type
         when 'boolean'   then array['equals']::text[]
         when 'enum'      then array['equals', 'at_least', 'at_most']::text[]
         when 'enum_list' then array['contains']::text[]
         when 'number'    then array['equals', 'at_least', 'at_most']::text[]
         when 'text'      then array['equals']::text[]
         else array[]::text[]
       end
 where operators = '{}';

-- ---------------------------------------------------------------------------
-- 2. Price becomes comparable numbers, one measure per unit, instead of a JSON
--    blob that cannot be sorted. The generic word "price" may map to several
--    capabilities, so the alias uniqueness is now per capability; the search
--    picks the ones that fit the modality being searched.
-- ---------------------------------------------------------------------------
delete from capability_definitions where name = 'pricing';

drop index if exists capability_aliases_alias_uq;
create unique index capability_aliases_alias_cap_uq on capability_aliases (lower(alias), capability_name);

-- ---------------------------------------------------------------------------
-- 3. Verified queries (tier 1): human-verified mappings from a question to a
--    structured query spec. They store the QUERY, never the answer, so results
--    are always computed from current, cited data.
-- ---------------------------------------------------------------------------
create table verified_queries (
  id              uuid primary key default gen_random_uuid(),
  title           text not null,
  question        text not null unique,        -- canonical phrasing, may contain {slot} names for display
  -- Structured spec: {modality, filters[], measures[], sort, limit}. Values may be "{{slot_name}}".
  query_spec      jsonb not null check (jsonb_typeof(query_spec) = 'object'),
  -- Slot definitions: [{name, capability, required, default, description}]
  slots           jsonb not null default '[]'::jsonb check (jsonb_typeof(slots) = 'array'),
  status          text not null default 'draft' check (status in ('draft', 'verified', 'retired')),
  source          text not null default 'manual' check (source in ('manual', 'suggested_from_log')),
  verified_by     text,
  verified_at     timestamptz,
  times_matched   integer not null default 0,
  last_matched_at timestamptz,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  check (status <> 'verified' or (verified_by is not null and verified_at is not null))
);

create trigger verified_queries_updated_at before update on verified_queries
  for each row execute function set_updated_at();
create index verified_queries_status_idx on verified_queries (status);
create index verified_queries_question_trgm_idx on verified_queries using gin (lower(question) gin_trgm_ops);

-- Concrete paraphrases people might type, for retrieving candidates by similarity.
create table verified_query_phrases (
  id                uuid primary key default gen_random_uuid(),
  verified_query_id uuid not null references verified_queries (id) on delete cascade,
  phrase            text not null
);
create unique index verified_query_phrases_uq on verified_query_phrases (verified_query_id, lower(phrase));
create index verified_query_phrases_trgm_idx on verified_query_phrases using gin (lower(phrase) gin_trgm_ops);

-- ---------------------------------------------------------------------------
-- 4. Query log: which tier answered, what ran, how fast, and a review queue.
--    Improvement order: add a verified query, then extend measures, filters and
--    aliases, then fill data gaps.
-- ---------------------------------------------------------------------------
alter table queries
  add column resolved_tier     text check (resolved_tier in ('verified_query', 'semantic', 'unanswered')),
  add column verified_query_id uuid references verified_queries (id) on delete set null,
  add column query_spec        jsonb,                -- the spec that actually ran
  add column ttft_ms           integer check (ttft_ms >= 0),   -- time to first streamed text
  add column review_status     text not null default 'new'
             check (review_status in ('new', 'reviewed', 'promoted', 'ignored')),
  add column review_note       text;

create index queries_review_idx on queries (review_status, created_at desc)
  where resolved_tier in ('semantic', 'unanswered');

-- What to work on next: repeated questions that reached tier 2 or failed.
-- Questions are grouped after lower-casing, trimming, dropping common punctuation and
-- collapsing spaces, so "Best model for Tamil dubbing?" and "best model for tamil dubbing"
-- count together. The punctuation list is explicit (not [:alnum:]) so non-Latin scripts such
-- as Devanagari are never stripped.
create view review_queue as
select regexp_replace(regexp_replace(lower(btrim(question)), '[?!.,;:"''()]', '', 'g'), '\s+', ' ', 'g')
                                                 as question,
       count(*)                                  as times_asked,
       max(created_at)                           as last_asked,
       array_agg(distinct resolved_tier)         as tiers,
       count(*) filter (where answered is false) as times_unanswered
  from queries
 where resolved_tier in ('semantic', 'unanswered')
   and review_status = 'new'
 group by 1
 order by times_asked desc, last_asked desc;

-- Answer feedback from the UI (thumbs up or down), to find weak answers.
alter table messages
  add column feedback         smallint check (feedback in (-1, 1)),
  add column feedback_comment text;

-- ---------------------------------------------------------------------------
-- 5. Row-level security: verified queries and the review queue are internal
--    (service role only), so no client policies or grants are added.
-- ---------------------------------------------------------------------------
alter table verified_queries       enable row level security;
alter table verified_query_phrases enable row level security;

commit;
