# Data standard (for whoever fills the catalog)

The agent is only as good as this data. Two ideas: **every fact has a source**, and **every fact uses an
agreed name and an agreed value format**. The database enforces the names and formats, so a typo is
rejected instead of becoming a silent gap.

## Rules

1. **One fact per row** in `capabilities`: a model, a capability name, a value, a source.
2. **Use only defined names.** A capability name must exist in `capability_definitions`. To record something
   new, add the definition first (name, description, value type, allowed values), then its aliases (see below).
3. **Use canonical values.** Languages are two-letter ISO 639-1 codes (`hi`, not "Hindi"). Resolutions come
   from the allowed list (`2160p`, not "4K"). Booleans are `true` or `false`. Numbers are plain numbers in the
   unit stated in the definition.
4. **Always attach a source** (`sources` row: type, URL, title) and choose its type honestly:
   `official_docs`, `provider_api`, `manual_review` or `scraped`.
5. **Verification.** New or scraped facts start `unverified`. A person confirms the fact against its source,
   then sets `verified` and `last_verified_at`. The database refuses `verified` without a date.
6. **Missing means "not recorded".** Do not store `false`, `null` or a guess for something you do not know.
   If a fact is not known, leave out the row. The agent then says "not recorded", and a search does not
   wrongly rule the model out.
7. **Price and API availability are facts too** (`pricing`, `api_available`) with sources. The model's
   `description` is display text only; the agent never makes claims from it.
8. **Re-verify on a schedule.** Models change roughly monthly; old verified dates are visible to users.

## Pricing format

`pricing` is a JSON object:

```json
{ "unit": "per_second", "amount": 0.5, "currency": "USD", "notes": "optional, for example tier or resolution" }
```

`unit` is one of `per_second`, `per_clip`, `per_image`, `per_1k_input_tokens`, `per_1k_output_tokens`,
`per_month`. If a model has several prices, record the one that matches the capability users compare on, and
put the rest in `notes`, or agree a second named capability with the owner. (The numbers above are only a
format example, not a real price.)

## Draft vocabulary

Seeded from `supabase/seed/vocabulary_draft.sql`. The data team should confirm or change these before
real data goes in.

| Name | Type | Allowed values or unit | Meaning |
| --- | --- | --- | --- |
| `speech_languages` | list of codes | ISO 639-1 list in the seed | Languages of generated spoken audio (voiceover, dialogue) |
| `output_languages` | list of codes | same list | Languages of written or rendered text |
| `audio_generation` | boolean | | The model produces audio itself |
| `background_audio` | boolean | | Music or ambient sound alongside speech |
| `max_resolution` | one of | 480p, 720p, 1080p, 1440p, 2160p | Highest output resolution |
| `max_clip_seconds` | number | seconds | Longest single clip |
| `api_available` | boolean | | Usable through a public API |
| `open_weights` | boolean | | Weights publicly downloadable |
| `context_window_tokens` | number | tokens | Maximum input context |
| `input_modalities`, `output_modalities` | list | text, image, audio, video | Input and output types |
| `pricing` | object | see above | Price with its unit |

To add a language or value, update `allowed_values` for that definition (and add its everyday names to
`value_aliases`). To add a capability, insert into `capability_definitions` and `capability_aliases`.

## Aliases: how search becomes forgiving

People do not type canonical terms. The `capability_aliases` table maps everyday words to a capability
("voiceover", "dubbing", "narration" all point to `speech_languages`). The `value_aliases` table maps everyday
values to the canonical one ("Hindi", "हिन्दी" point to `hi`; "4K" points to `2160p`).

Adding aliases is the cheapest way to improve search. When the query log shows a phrase that failed
(`queries` where `answered` is false), add it as an alias. No code change is needed.

## Checklist before adding a model

- [ ] Model row created (provider, name, slug, modality).
- [ ] Every fact uses a defined name and canonical value.
- [ ] Every fact has a source with a URL.
- [ ] Facts only confirmed against the source are marked `verified` with a date.
- [ ] Nothing unknown was stored as `false` or a guess.
