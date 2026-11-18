-- DRAFT verified queries (tier 1). Every row is created as 'draft'.
-- A draft is NOT used by the agent. A person must review the question, the query
-- spec and the paraphrases, then set status = 'verified' with verified_by and
-- verified_at (the database refuses 'verified' without them).
--
-- These store questions and query specs only. They contain no claim about any
-- real model. Apply after the migrations and vocabulary_draft.sql.
--
-- Spec shape: {modality, filters[], measures[], sort, limit}. A value written as
-- "{{name}}" is a slot the agent fills from the user's words.
-- Retrieval matches the user's text against the paraphrases (and the question).

insert into verified_queries (title, question, query_spec, slots) values
  ('Models that speak a language',
   'Which {modality} models can speak {language}?',
   '{"modality":"{{modality}}","filters":[{"capability":"speech_languages","operator":"contains","value":"{{language}}"}],"measures":[],"limit":10}',
   '[{"name":"modality","capability":null,"required":true,"default":"video","description":"video, image, text or speech"},
     {"name":"language","capability":"speech_languages","required":true,"default":null,"description":"language of the generated speech"}]'),
  ('Voiceover in a language with background audio',
   'Which video models can do {language} voiceover with background audio?',
   '{"modality":"video","filters":[{"capability":"speech_languages","operator":"contains","value":"{{language}}"},{"capability":"background_audio","operator":"equals","value":true}],"measures":[],"limit":10}',
   '[{"name":"language","capability":"speech_languages","required":true,"default":null,"description":"language of the generated speech"}]'),
  ('Models with an API',
   'Which {modality} models have an API?',
   '{"modality":"{{modality}}","filters":[{"capability":"api_available","operator":"equals","value":true}],"measures":[],"limit":10}',
   '[{"name":"modality","capability":null,"required":true,"default":"video","description":"video, image, text or speech"}]'),
  ('Open-weights models',
   'Which open-source {modality} models are there?',
   '{"modality":"{{modality}}","filters":[{"capability":"open_weights","operator":"equals","value":true}],"measures":[],"limit":10}',
   '[{"name":"modality","capability":null,"required":true,"default":"video","description":"video, image, text or speech"}]'),
  ('Longest video clips',
   'Which video model makes the longest clips?',
   '{"modality":"video","filters":[],"measures":[],"sort":{"capability":"max_clip_seconds","direction":"best"},"limit":10}',
   '[]'),
  ('Cheapest video models',
   'Which video model is cheapest?',
   '{"modality":"video","filters":[],"measures":[],"sort":{"capability":"price_usd_per_second","direction":"best"},"limit":10}',
   '[]'),
  ('Minimum video resolution',
   'Which video models support at least {resolution}?',
   '{"modality":"video","filters":[],"measures":[{"capability":"max_resolution","operator":"at_least","value":"{{resolution}}"}],"limit":10}',
   '[{"name":"resolution","capability":"max_resolution","required":true,"default":"1080p","description":"minimum output resolution"}]')
on conflict (question) do nothing;

insert into verified_query_phrases (verified_query_id, phrase)
select v.id, p.phrase
from verified_queries v
join (values
  ('Which {modality} models can speak {language}?', 'which video models can speak hindi'),
  ('Which {modality} models can speak {language}?', 'ai video generators with hindi voice'),
  ('Which {modality} models can speak {language}?', 'video models that support hindi voiceover'),
  ('Which {modality} models can speak {language}?', 'text to video with spanish audio'),
  ('Which video models can do {language} voiceover with background audio?', 'video model with hindi voiceover and background music'),
  ('Which video models can do {language} voiceover with background audio?', 'hindi narration with background sound'),
  ('Which video models can do {language} voiceover with background audio?', 'best model for hindi voiceover along with background audio'),
  ('Which {modality} models have an API?', 'which video models have an api'),
  ('Which {modality} models have an API?', 'image models with api access'),
  ('Which open-source {modality} models are there?', 'which open source video models are there'),
  ('Which open-source {modality} models are there?', 'self hosted image models'),
  ('Which open-source {modality} models are there?', 'open weights text models'),
  ('Which video model makes the longest clips?', 'which video model makes the longest clips'),
  ('Which video model makes the longest clips?', 'longest video generation'),
  ('Which video model makes the longest clips?', 'maximum video length'),
  ('Which video model is cheapest?', 'cheapest video generation model'),
  ('Which video model is cheapest?', 'lowest cost ai video'),
  ('Which video model is cheapest?', 'cheap video models'),
  ('Which video models support at least {resolution}?', 'video models that support 4k'),
  ('Which video models support at least {resolution}?', 'which video model does full hd'),
  ('Which video models support at least {resolution}?', '1080p video generation')
) as p(question, phrase) on p.question = v.question
on conflict do nothing;
