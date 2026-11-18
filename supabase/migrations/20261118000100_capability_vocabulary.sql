-- Capability vocabulary, and provenance for every fact.
--
-- 1. Pricing and API availability move from columns on `models` to ordinary
--    capability rows, so they carry a source and verification status like every
--    other fact. `models.description` stays as display text only; the agent must
--    not make claims from it.
-- 2. `capability_definitions` is the controlled vocabulary: capabilities.name must
--    reference it, and a trigger checks every value against its definition (type,
--    allowed values, language codes). A typo or near-duplicate name cannot be inserted.
--
-- Defining a vocabulary is not a claim about any model; no model data is added here.

-- ---------------------------------------------------------------------------
-- Guard: this migration drops two columns. The catalog is expected to be empty;
-- refuse to run rather than lose data if it is not.
-- ---------------------------------------------------------------------------
do $$
begin
  if exists (select 1 from models where pricing <> '{}'::jsonb or api_available is not null) then
    raise exception 'models.pricing or models.api_available holds data; move it to capability rows before applying this migration';
  end if;
  if exists (select 1 from capabilities) then
    raise exception 'capabilities already has rows; their names must be defined in capability_definitions first (edit this migration to add them)';
  end if;
end;
$$;

alter table models drop column pricing;
alter table models drop column api_available;
comment on column models.description is
  'Display text only. Not a sourced fact: the agent must not make claims from it.';

-- ---------------------------------------------------------------------------
-- ISO 639-1 language codes (from the ISO 639 data in pycountry 26.2.16).
-- ---------------------------------------------------------------------------
create table language_codes (
  code text primary key check (code ~ '^[a-z]{2}$'),
  name text not null
);

insert into language_codes (code, name) values
  ('aa', 'Afar'),
  ('ab', 'Abkhazian'),
  ('ae', 'Avestan'),
  ('af', 'Afrikaans'),
  ('ak', 'Akan'),
  ('am', 'Amharic'),
  ('an', 'Aragonese'),
  ('ar', 'Arabic'),
  ('as', 'Assamese'),
  ('av', 'Avaric'),
  ('ay', 'Aymara'),
  ('az', 'Azerbaijani'),
  ('ba', 'Bashkir'),
  ('be', 'Belarusian'),
  ('bg', 'Bulgarian'),
  ('bi', 'Bislama'),
  ('bm', 'Bambara'),
  ('bn', 'Bengali'),
  ('bo', 'Tibetan'),
  ('br', 'Breton'),
  ('bs', 'Bosnian'),
  ('ca', 'Catalan'),
  ('ce', 'Chechen'),
  ('ch', 'Chamorro'),
  ('co', 'Corsican'),
  ('cr', 'Cree'),
  ('cs', 'Czech'),
  ('cu', 'Church Slavic'),
  ('cv', 'Chuvash'),
  ('cy', 'Welsh'),
  ('da', 'Danish'),
  ('de', 'German'),
  ('dv', 'Divehi'),
  ('dz', 'Dzongkha'),
  ('ee', 'Ewe'),
  ('el', 'Modern Greek (1453-)'),
  ('en', 'English'),
  ('eo', 'Esperanto'),
  ('es', 'Spanish'),
  ('et', 'Estonian'),
  ('eu', 'Basque'),
  ('fa', 'Persian'),
  ('ff', 'Fulah'),
  ('fi', 'Finnish'),
  ('fj', 'Fijian'),
  ('fo', 'Faroese'),
  ('fr', 'French'),
  ('fy', 'Western Frisian'),
  ('ga', 'Irish'),
  ('gd', 'Scottish Gaelic'),
  ('gl', 'Galician'),
  ('gn', 'Guarani'),
  ('gu', 'Gujarati'),
  ('gv', 'Manx'),
  ('ha', 'Hausa'),
  ('he', 'Hebrew'),
  ('hi', 'Hindi'),
  ('ho', 'Hiri Motu'),
  ('hr', 'Croatian'),
  ('ht', 'Haitian'),
  ('hu', 'Hungarian'),
  ('hy', 'Armenian'),
  ('hz', 'Herero'),
  ('ia', 'Interlingua (International Auxiliary Language Association)'),
  ('id', 'Indonesian'),
  ('ie', 'Interlingue'),
  ('ig', 'Igbo'),
  ('ii', 'Sichuan Yi'),
  ('ik', 'Inupiaq'),
  ('io', 'Ido'),
  ('is', 'Icelandic'),
  ('it', 'Italian'),
  ('iu', 'Inuktitut'),
  ('ja', 'Japanese'),
  ('jv', 'Javanese'),
  ('ka', 'Georgian'),
  ('kg', 'Kongo'),
  ('ki', 'Kikuyu'),
  ('kj', 'Kuanyama'),
  ('kk', 'Kazakh'),
  ('kl', 'Kalaallisut'),
  ('km', 'Khmer'),
  ('kn', 'Kannada'),
  ('ko', 'Korean'),
  ('kr', 'Kanuri'),
  ('ks', 'Kashmiri'),
  ('ku', 'Kurdish'),
  ('kv', 'Komi'),
  ('kw', 'Cornish'),
  ('ky', 'Kirghiz'),
  ('la', 'Latin'),
  ('lb', 'Luxembourgish'),
  ('lg', 'Ganda'),
  ('li', 'Limburgan'),
  ('ln', 'Lingala'),
  ('lo', 'Lao'),
  ('lt', 'Lithuanian'),
  ('lu', 'Luba-Katanga'),
  ('lv', 'Latvian'),
  ('mg', 'Malagasy'),
  ('mh', 'Marshallese'),
  ('mi', 'Maori'),
  ('mk', 'Macedonian'),
  ('ml', 'Malayalam'),
  ('mn', 'Mongolian'),
  ('mr', 'Marathi'),
  ('ms', 'Malay (macrolanguage)'),
  ('mt', 'Maltese'),
  ('my', 'Burmese'),
  ('na', 'Nauru'),
  ('nb', 'Norwegian Bokmål'),
  ('nd', 'North Ndebele'),
  ('ne', 'Nepali (macrolanguage)'),
  ('ng', 'Ndonga'),
  ('nl', 'Dutch'),
  ('nn', 'Norwegian Nynorsk'),
  ('no', 'Norwegian'),
  ('nr', 'South Ndebele'),
  ('nv', 'Navajo'),
  ('ny', 'Chichewa'),
  ('oc', 'Occitan (post 1500)'),
  ('oj', 'Ojibwa'),
  ('om', 'Oromo'),
  ('or', 'Oriya (macrolanguage)'),
  ('os', 'Ossetian'),
  ('pa', 'Panjabi'),
  ('pi', 'Pali'),
  ('pl', 'Polish'),
  ('ps', 'Pushto'),
  ('pt', 'Portuguese'),
  ('qu', 'Quechua'),
  ('rm', 'Romansh'),
  ('rn', 'Rundi'),
  ('ro', 'Romanian'),
  ('ru', 'Russian'),
  ('rw', 'Kinyarwanda'),
  ('sa', 'Sanskrit'),
  ('sc', 'Sardinian'),
  ('sd', 'Sindhi'),
  ('se', 'Northern Sami'),
  ('sg', 'Sango'),
  ('sh', 'Serbo-Croatian'),
  ('si', 'Sinhala'),
  ('sk', 'Slovak'),
  ('sl', 'Slovenian'),
  ('sm', 'Samoan'),
  ('sn', 'Shona'),
  ('so', 'Somali'),
  ('sq', 'Albanian'),
  ('sr', 'Serbian'),
  ('ss', 'Swati'),
  ('st', 'Southern Sotho'),
  ('su', 'Sundanese'),
  ('sv', 'Swedish'),
  ('sw', 'Swahili (macrolanguage)'),
  ('ta', 'Tamil'),
  ('te', 'Telugu'),
  ('tg', 'Tajik'),
  ('th', 'Thai'),
  ('ti', 'Tigrinya'),
  ('tk', 'Turkmen'),
  ('tl', 'Tagalog'),
  ('tn', 'Tswana'),
  ('to', 'Tonga (Tonga Islands)'),
  ('tr', 'Turkish'),
  ('ts', 'Tsonga'),
  ('tt', 'Tatar'),
  ('tw', 'Twi'),
  ('ty', 'Tahitian'),
  ('ug', 'Uighur'),
  ('uk', 'Ukrainian'),
  ('ur', 'Urdu'),
  ('uz', 'Uzbek'),
  ('ve', 'Venda'),
  ('vi', 'Vietnamese'),
  ('vo', 'Volapük'),
  ('wa', 'Walloon'),
  ('wo', 'Wolof'),
  ('xh', 'Xhosa'),
  ('yi', 'Yiddish'),
  ('yo', 'Yoruba'),
  ('za', 'Zhuang'),
  ('zh', 'Chinese'),
  ('zu', 'Zulu');

-- ---------------------------------------------------------------------------
-- Capability definitions
-- ---------------------------------------------------------------------------
create table capability_definitions (
  name           text primary key check (name ~ '^[a-z][a-z0-9_]{1,62}$'),
  value_type     text not null check (value_type in ('boolean', 'number', 'text', 'text_list', 'object')),
  description    text not null,
  -- For text and text_list: the only values allowed (a JSON array of strings). Null = any text.
  allowed_values jsonb check (allowed_values is null or jsonb_typeof(allowed_values) = 'array'),
  -- For text and text_list: a named format every value must follow.
  value_format   text check (value_format in ('language_code')),
  unit           text,                         -- e.g. 'tokens', 'seconds'; for numbers
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  check (allowed_values is null or value_type in ('text', 'text_list')),
  check (value_format is null or value_type in ('text', 'text_list')),
  check (unit is null or value_type in ('number', 'object'))
);

create trigger capability_definitions_updated_at before update on capability_definitions
  for each row execute function set_updated_at();

insert into capability_definitions (name, value_type, description) values
  ('pricing', 'object',
   'Price as published by the provider. Object with "currency" (ISO 4217, e.g. "USD"), "unit" '
   '(what one price is for, e.g. "second of video", "1M input tokens", "image") and "amount" (number). '
   'Use "tiers" (a list of such objects, each with a "label") when there are several prices.'),
  ('api_available', 'boolean',
   'true if the model can be called through a public API, false if it cannot.');

-- Returns null when the value fits the definition, else a message saying why not.
create or replace function capability_value_problem(p_name text, p_value jsonb)
returns text
language plpgsql
stable
set search_path = public
as $$
declare
  d     capability_definitions;
  items jsonb;
  item  jsonb;
begin
  select * into d from capability_definitions where name = p_name;
  if not found then
    return format('capability %L is not defined in capability_definitions', p_name);
  end if;

  case d.value_type
    when 'boolean' then
      if jsonb_typeof(p_value) <> 'boolean' then return format('%s must be true or false', p_name); end if;
    when 'number' then
      if jsonb_typeof(p_value) <> 'number' then return format('%s must be a number', p_name); end if;
    when 'object' then
      if jsonb_typeof(p_value) <> 'object' then return format('%s must be a JSON object', p_name); end if;
    when 'text' then
      if jsonb_typeof(p_value) <> 'string' then return format('%s must be a string', p_name); end if;
    when 'text_list' then
      if jsonb_typeof(p_value) <> 'array' or jsonb_array_length(p_value) = 0 then
        return format('%s must be a non-empty list of strings', p_name);
      end if;
      if exists (select 1 from jsonb_array_elements(p_value) e where jsonb_typeof(e) <> 'string') then
        return format('%s must be a non-empty list of strings', p_name);
      end if;
      if (select count(distinct e) <> count(*) from jsonb_array_elements(p_value) e) then
        return format('%s must not repeat a value', p_name);
      end if;
  end case;

  if d.value_type in ('text', 'text_list') then
    items := case when d.value_type = 'text' then jsonb_build_array(p_value) else p_value end;
    for item in select * from jsonb_array_elements(items) loop
      if d.allowed_values is not null and not d.allowed_values @> jsonb_build_array(item) then
        return format('%s: %s is not one of the allowed values %s', p_name, item, d.allowed_values);
      end if;
      if d.value_format = 'language_code'
         and not exists (select 1 from language_codes where code = item #>> '{}') then
        return format('%s: %s is not an ISO 639-1 two-letter language code (e.g. "hi", "en")', p_name, item);
      end if;
    end loop;
  end if;

  return null;
end;
$$;

-- Unknown names are rejected by the foreign key as well; on update cascade lets a
-- definition be renamed without touching every fact by hand.
alter table capabilities
  add constraint capabilities_name_fkey
  foreign key (name) references capability_definitions (name) on update cascade;

create or replace function check_capability_value()
returns trigger
language plpgsql
set search_path = public
as $$
declare
  problem text := capability_value_problem(new.name, new.value);
begin
  if problem is not null then
    raise exception using errcode = 'check_violation', message = problem;
  end if;
  return new;
end;
$$;

create trigger capabilities_check_value before insert or update of name, value on capabilities
  for each row execute function check_capability_value();

-- Changing a definition must not leave existing facts that no longer fit it.
create or replace function recheck_capabilities_for_definition()
returns trigger
language plpgsql
set search_path = public
as $$
declare
  bad record;
begin
  select c.id, capability_value_problem(new.name, c.value) as problem into bad
    from capabilities c
   where c.name = new.name and capability_value_problem(new.name, c.value) is not null
   limit 1;
  if found then
    raise exception using errcode = 'check_violation',
      message = format('definition change rejected: existing capability %s no longer fits (%s)', bad.id, bad.problem);
  end if;
  return new;
end;
$$;

create constraint trigger capability_definitions_recheck
  after update of value_type, allowed_values, value_format on capability_definitions
  for each row execute function recheck_capabilities_for_definition();

-- Trigger functions run from triggers only; nobody needs to call them directly.
revoke all on function check_capability_value() from public, anon, authenticated;
revoke all on function recheck_capabilities_for_definition() from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- Row-level security: readable by signed-in users, written by the service role.
-- ---------------------------------------------------------------------------
alter table capability_definitions enable row level security;
alter table language_codes         enable row level security;

create policy "vocabulary readable by signed-in users" on capability_definitions
  for select to authenticated using (true);
create policy "vocabulary readable by signed-in users" on language_codes
  for select to authenticated using (true);

grant select on capability_definitions, language_codes to authenticated;
