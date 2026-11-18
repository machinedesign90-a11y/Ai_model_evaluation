-- Phase one schema: curated model database, chat storage, free-tier counter.
-- Plain Postgres on purpose (only auth.users is Supabase-specific), so a later
-- move to RDS or Azure is a dump and restore.

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------------
-- Shared helper
-- ---------------------------------------------------------------------------
create or replace function set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- Curated catalog
-- ---------------------------------------------------------------------------

-- Where a fact came from. Every capability row points at one of these.
create table sources (
  id           uuid primary key default gen_random_uuid(),
  source_type  text not null
               check (source_type in ('official_docs', 'provider_api', 'scraped', 'manual_review')),
  url          text,
  title        text,
  retrieved_at timestamptz not null default now(),
  created_at   timestamptz not null default now()
);

create table models (
  id            uuid primary key default gen_random_uuid(),
  provider      text not null,
  name          text not null,
  slug          text not null unique,          -- e.g. 'google-veo-3'
  modality      text not null,                 -- 'video', 'text', 'image', 'speech', ...
  description   text,
  pricing       jsonb not null default '{}'::jsonb,  -- unit and amounts, shape varies by modality
  api_available boolean,                       -- null = not recorded
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create index models_modality_idx on models (modality);
create trigger models_updated_at before update on models
  for each row execute function set_updated_at();

-- One structured fact per row, e.g. (veo, 'output_languages', ["hi","en"]).
-- A missing row means "not recorded"; the agent must never guess.
create table capabilities (
  id                  uuid primary key default gen_random_uuid(),
  model_id            uuid not null references models (id) on delete cascade,
  name                text not null,           -- e.g. 'output_languages', 'audio_generation', 'max_resolution'
  value               jsonb not null,
  source_id           uuid references sources (id) on delete set null,
  verification_status text not null default 'unverified'
                      check (verification_status in ('unverified', 'verified')),
  last_verified_at    timestamptz,             -- set when a person confirms the fact
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  unique (model_id, name),
  check (verification_status = 'unverified' or last_verified_at is not null)
);

create index capabilities_name_idx on capabilities (name);
create index capabilities_value_gin_idx on capabilities using gin (value);
create trigger capabilities_updated_at before update on capabilities
  for each row execute function set_updated_at();

-- ---------------------------------------------------------------------------
-- Users and the free-tier counter
-- ---------------------------------------------------------------------------

-- One row per registered visitor. Registration itself lives in Supabase Auth.
create table profiles (
  id            uuid primary key references auth.users (id) on delete cascade,
  email         text,
  messages_used integer not null default 0 check (messages_used >= 0),
  message_limit integer not null default 4 check (message_limit >= 0),
  created_at    timestamptz not null default now()
);

create or replace function handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into profiles (id, email) values (new.id, new.email)
  on conflict (id) do nothing;
  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function handle_new_user();

-- Atomically count one user message against the free tier.
-- The single UPDATE with a WHERE guard means two simultaneous requests cannot
-- both slip under the limit. Call it from the agent service (service role)
-- before invoking the model.
create or replace function consume_message(p_user uuid)
returns table (allowed boolean, remaining integer)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_used  integer;
  v_limit integer;
begin
  update profiles
     set messages_used = messages_used + 1
   where id = p_user
     and messages_used < message_limit
  returning messages_used, message_limit into v_used, v_limit;

  if found then
    return query select true, v_limit - v_used;
  else
    return query select false, 0;
  end if;
end;
$$;

revoke all on function consume_message(uuid) from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- Conversations and messages
-- ---------------------------------------------------------------------------

create table conversations (
  id               uuid primary key default gen_random_uuid(),
  user_id          uuid not null references profiles (id) on delete cascade,
  status           text not null default 'active' check (status in ('active', 'expired')),
  -- Structured context captured as the chat goes; phase two pre-fills a project from it.
  use_case         text,
  constraints      jsonb not null default '{}'::jsonb,
  shortlist        jsonb not null default '[]'::jsonb,   -- model slugs the agent recommended
  started_at       timestamptz not null default now(),
  last_activity_at timestamptz not null default now()
);

-- Supports the 24-hour inactivity rule: find a user's latest active conversation.
create index conversations_user_activity_idx on conversations (user_id, last_activity_at desc);

create table messages (
  id              uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references conversations (id) on delete cascade,
  role            text not null check (role in ('user', 'assistant', 'tool')),
  content         jsonb not null,              -- text and tool blocks, as sent to or from the model
  model_id        text,                        -- model that produced an assistant turn
  input_tokens    integer,
  output_tokens   integer,
  created_at      timestamptz not null default now()
);

create index messages_conversation_idx on messages (conversation_id, created_at);

-- Log of questions, mainly to find data gaps (what visitors ask that we cannot answer).
create table queries (
  id                   uuid primary key default gen_random_uuid(),
  user_id              uuid references profiles (id) on delete set null,
  conversation_id      uuid references conversations (id) on delete set null,
  question             text not null,
  answered             boolean,                 -- null until the turn finishes
  missing_capabilities text[] not null default '{}',
  created_at           timestamptz not null default now()
);

create index queries_unanswered_idx on queries (created_at desc) where answered is false;

-- ---------------------------------------------------------------------------
-- Row-level security
-- The agent and MCP services use the service role, which bypasses RLS. These
-- policies protect direct client access (for example from the browser).
-- ---------------------------------------------------------------------------

alter table sources       enable row level security;
alter table models        enable row level security;
alter table capabilities  enable row level security;
alter table profiles      enable row level security;
alter table conversations enable row level security;
alter table messages      enable row level security;
alter table queries       enable row level security;

create policy "catalog readable by signed-in users" on models
  for select to authenticated using (true);
create policy "catalog readable by signed-in users" on capabilities
  for select to authenticated using (true);
create policy "catalog readable by signed-in users" on sources
  for select to authenticated using (true);

create policy "users read own profile" on profiles
  for select to authenticated using (id = auth.uid());

create policy "users read own conversations" on conversations
  for select to authenticated using (user_id = auth.uid());

create policy "users read own messages" on messages
  for select to authenticated
  using (exists (
    select 1 from conversations c
    where c.id = messages.conversation_id and c.user_id = auth.uid()
  ));

-- queries has no client policy: it is written and read by the service role only.

-- Explicit read grants for signed-in clients (Supabase adds similar defaults,
-- but stating them keeps the migration portable to plain Postgres). Writes to
-- every table go through the service role only.
grant usage on schema public to authenticated;
grant select on models, capabilities, sources, profiles, conversations, messages to authenticated;
