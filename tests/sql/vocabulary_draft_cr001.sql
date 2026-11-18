-- DRAFT starter vocabulary for the data team to confirm or change.
-- Contains names, value formats and everyday wording only. It makes no claim
-- about any real model. Safe to re-run (inserts skip existing rows).
-- Apply after the migrations:  psql "$DATABASE_URL" -f supabase/seed/vocabulary_draft.sql

insert into capability_definitions (name, label, description, value_type, unit, allowed_values, modalities) values
  ('speech_languages', 'Spoken languages',
   'Languages the model can speak in generated audio (voiceover, narration, dialogue). Two-letter ISO 639-1 codes.',
   'enum_list', null,
   '["en","hi","bn","ta","te","mr","gu","kn","ml","pa","ur","es","fr","de","pt","it","ja","ko","zh","ar","ru","id","tr"]',
   '{video,speech}'),
  ('output_languages', 'Written output languages',
   'Languages the model can write or render as text. Two-letter ISO 639-1 codes.',
   'enum_list', null,
   '["en","hi","bn","ta","te","mr","gu","kn","ml","pa","ur","es","fr","de","pt","it","ja","ko","zh","ar","ru","id","tr"]',
   '{}'),
  ('audio_generation', 'Generates audio',
   'True if the model produces audio (speech, music or sound) as part of its own output.',
   'boolean', null, null, '{video}'),
  ('background_audio', 'Background audio',
   'True if the model generates music or ambient sound alongside speech.',
   'boolean', null, null, '{video}'),
  ('max_resolution', 'Maximum resolution',
   'Highest output resolution offered.',
   'enum', null, '["480p","720p","1080p","1440p","2160p"]', '{video,image}'),
  ('max_clip_seconds', 'Maximum clip length',
   'Longest single generated clip, in seconds.',
   'number', 'seconds', null, '{video}'),
  ('api_available', 'API available',
   'True if the model can be used through a public API.',
   'boolean', null, null, '{}'),
  ('open_weights', 'Open weights',
   'True if the model weights are publicly downloadable.',
   'boolean', null, null, '{}'),
  ('context_window_tokens', 'Context window',
   'Maximum input context, in tokens.',
   'number', 'tokens', null, '{text}'),
  ('input_modalities', 'Input types',
   'Kinds of input the model accepts.',
   'enum_list', null, '["text","image","audio","video"]', '{}'),
  ('output_modalities', 'Output types',
   'Kinds of output the model produces.',
   'enum_list', null, '["text","image","audio","video"]', '{}'),
  ('pricing', 'Pricing',
   'Price as an object: {"unit": "...", "amount": number, "currency": "USD", "notes": "optional"}. unit is one of per_second, per_clip, per_image, per_1k_input_tokens, per_1k_output_tokens, per_month.',
   'json', null, null, '{}')
on conflict (name) do nothing;

insert into capability_aliases (capability_name, alias) values
  ('speech_languages', 'voiceover'),
  ('speech_languages', 'voice over'),
  ('speech_languages', 'voiceover language'),
  ('speech_languages', 'voiceover languages'),
  ('speech_languages', 'dubbing'),
  ('speech_languages', 'narration'),
  ('speech_languages', 'spoken language'),
  ('speech_languages', 'spoken languages'),
  ('speech_languages', 'speech language'),
  ('speech_languages', 'audio language'),
  ('speech_languages', 'dialogue language'),
  ('output_languages', 'language support'),
  ('output_languages', 'languages'),
  ('output_languages', 'text language'),
  ('audio_generation', 'audio'),
  ('audio_generation', 'sound'),
  ('audio_generation', 'native audio'),
  ('audio_generation', 'generates audio'),
  ('background_audio', 'background music'),
  ('background_audio', 'background sound'),
  ('background_audio', 'ambient sound'),
  ('background_audio', 'soundtrack'),
  ('max_resolution', 'resolution'),
  ('max_resolution', 'video quality'),
  ('max_clip_seconds', 'clip length'),
  ('max_clip_seconds', 'video length'),
  ('max_clip_seconds', 'duration'),
  ('api_available', 'api'),
  ('api_available', 'api access'),
  ('api_available', 'has api'),
  ('open_weights', 'open source'),
  ('open_weights', 'open model'),
  ('open_weights', 'self hosted'),
  ('context_window_tokens', 'context length'),
  ('context_window_tokens', 'context size'),
  ('context_window_tokens', 'context window'),
  ('pricing', 'price'),
  ('pricing', 'cost'),
  ('pricing', 'how much')
on conflict do nothing;

-- Language names people type, mapped to the code stored in the data.
-- The same words apply to both language capabilities.
insert into value_aliases (capability_name, alias, canonical_value)
select c.name, v.alias, to_jsonb(v.code)
from (values
  ('english','en'), ('hindi','hi'), ('हिन्दी','hi'), ('हिंदी','hi'),
  ('bengali','bn'), ('bangla','bn'), ('tamil','ta'), ('telugu','te'),
  ('marathi','mr'), ('gujarati','gu'), ('kannada','kn'), ('malayalam','ml'),
  ('punjabi','pa'), ('urdu','ur'), ('spanish','es'), ('french','fr'),
  ('german','de'), ('portuguese','pt'), ('italian','it'), ('japanese','ja'),
  ('korean','ko'), ('chinese','zh'), ('mandarin','zh'), ('arabic','ar'),
  ('russian','ru'), ('indonesian','id'), ('turkish','tr')
) as v(alias, code)
cross join (values ('speech_languages'), ('output_languages')) as c(name)
on conflict do nothing;

insert into value_aliases (capability_name, alias, canonical_value) values
  ('max_resolution', '4k',      '"2160p"'),
  ('max_resolution', 'uhd',     '"2160p"'),
  ('max_resolution', '2k',      '"1440p"'),
  ('max_resolution', 'full hd', '"1080p"'),
  ('max_resolution', 'fhd',     '"1080p"'),
  ('max_resolution', 'hd',      '"720p"'),
  ('max_resolution', 'sd',      '"480p"')
on conflict do nothing;
