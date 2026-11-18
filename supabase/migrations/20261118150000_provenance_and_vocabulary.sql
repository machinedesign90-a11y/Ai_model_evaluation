-- Migration 2: one provenance mechanism, a controlled vocabulary, and the
-- lookup tables that make search forgiving.
--
-- Assumes the catalog holds no real data yet (data gathering is a separate
-- activity). It drops two uncited columns and adds a foreign key that existing
-- rows would have to satisfy.

create extension if not exists pg_trgm;

-- ---------------------------------------------------------------------------
-- 1. Price and API availability become capabilities, so they carry a source
--    and a verified date like every other fact. Description stays as display
--    text only; the agent must never make claims from it.
-- ---------------------------------------------------------------------------
alter table models
  drop column if exists pricing,
  drop column if exists api_available;

comment on column models.description is
  'Display text only. Uncited: the agent must not make factual claims from it.';

-- ---------------------------------------------------------------------------
-- 2. Controlled vocabulary: the only capability names that may be recorded,
--    and the format of each one's value.
-- ---------------------------------------------------------------------------
create table capability_definitions (
  name           text primary key check (name ~ '^[a-z][a-z0-9_]*$'),
  label          text not null,
  description    text not null,
  value_type     text not null
                 check (value_type in ('boolean', 'number', 'text', 'enum', 'enum_list', 'json')),
  unit           text,                         -- e.g. 'seconds' for numbers
  allowed_values jsonb,                        -- canonical values for enum and enum_list
  modalities     text[] not null default '{}', -- empty = applies to every modality
  created_at     timestamptz not null default now(),
  check (
    value_type not in ('enum', 'enum_list')
    or (allowed_values is not null and jsonb_typeof(allowed_values) = 'array')
  )
);

-- Words people use for a capability ("voiceover", "dubbing") mapped to its canonical name.
create table capability_aliases (
  id              uuid primary key default gen_random_uuid(),
  capability_name text not null references capability_definitions (name)
                  on delete cascade on update cascade,
  alias           text not null
);
create unique index capability_aliases_alias_uq on capability_aliases (lower(alias));
create index capability_aliases_trgm_idx on capability_aliases using gin (lower(alias) gin_trgm_ops);

-- Words people use for a value ("Hindi", "4K") mapped to its canonical value ("hi", "2160p").
create table value_aliases (
  id              uuid primary key default gen_random_uuid(),
  capability_name text not null references capability_definitions (name)
                  on delete cascade on update cascade,
  alias           text not null,
  canonical_value jsonb not null
);
create unique index value_aliases_uq on value_aliases (capability_name, lower(alias));
create index value_aliases_trgm_idx on value_aliases using gin (lower(alias) gin_trgm_ops);

-- Trigram index on the canonical names themselves, for typo-tolerant lookup.
create index capability_definitions_name_trgm_idx on capability_definitions using gin (name gin_trgm_ops);

-- ---------------------------------------------------------------------------
-- 3. Capabilities must use a defined name and a value of the defined format.
-- ---------------------------------------------------------------------------
alter table capabilities
  add constraint capabilities_name_fk
  foreign key (name) references capability_definitions (name) on update cascade;

create or replace function validate_capability_value()
returns trigger
language plpgsql
as $$
declare
  d capability_definitions%rowtype;
  v jsonb := new.value;
begin
  select * into d from capability_definitions where name = new.name;
  if not found then
    raise exception 'Unknown capability "%": add it to capability_definitions first', new.name;
  end if;

  case d.value_type
    when 'boolean' then
      if jsonb_typeof(v) <> 'boolean' then
        raise exception 'Capability "%" needs a boolean (true or false), got %', new.name, v;
      end if;
    when 'number' then
      if jsonb_typeof(v) <> 'number' then
        raise exception 'Capability "%" needs a number, got %', new.name, v;
      end if;
    when 'text' then
      if jsonb_typeof(v) <> 'string' then
        raise exception 'Capability "%" needs a text value, got %', new.name, v;
      end if;
    when 'enum' then
      if not (d.allowed_values @> jsonb_build_array(v)) then
        raise exception 'Capability "%": % is not an allowed value. Allowed: %', new.name, v, d.allowed_values;
      end if;
    when 'enum_list' then
      if jsonb_typeof(v) <> 'array' or jsonb_array_length(v) = 0 then
        raise exception 'Capability "%" needs a non-empty list, got %', new.name, v;
      end if;
      if exists (
        select 1 from jsonb_array_elements(v) e
        where not (d.allowed_values @> jsonb_build_array(e))
      ) then
        raise exception 'Capability "%": % contains a value outside the allowed list %', new.name, v, d.allowed_values;
      end if;
    when 'json' then
      null; -- shape documented in docs/DATA_STANDARD.md
  end case;

  return new;
end;
$$;

create trigger capabilities_validate_value
  before insert or update of name, value on capabilities
  for each row execute function validate_capability_value();

-- ---------------------------------------------------------------------------
-- 4. Row-level security and grants for the new tables (read-only for clients).
-- ---------------------------------------------------------------------------
alter table capability_definitions enable row level security;
alter table capability_aliases     enable row level security;
alter table value_aliases          enable row level security;

create policy "vocabulary readable by signed-in users" on capability_definitions
  for select to authenticated using (true);
create policy "vocabulary readable by signed-in users" on capability_aliases
  for select to authenticated using (true);
create policy "vocabulary readable by signed-in users" on value_aliases
  for select to authenticated using (true);

grant select on capability_definitions, capability_aliases, value_aliases to authenticated;
