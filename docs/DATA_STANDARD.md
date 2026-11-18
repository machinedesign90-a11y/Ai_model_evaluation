# Data standard (for whoever fills the catalog and curates queries)

The agent is only as good as this data. Three ideas: **every fact has a source**, **every fact uses an
agreed name and value format**, and **a person verifies the common questions**. The database enforces the
names and formats, so a typo is rejected instead of becoming a silent gap.

## Rules for facts

1. **One fact per row** in `capabilities`: a model, a capability name, a value, a source.
2. **Use only defined names.** A capability name must exist in `capability_definitions`. To record something
   new, add the definition first (name, description, value type, kind, allowed values), then its aliases.
3. **Use canonical values.** Languages are two-letter ISO 639-1 codes (`hi`, not "Hindi"). Resolutions come
   from the allowed list (`2160p`, not "4K"). Booleans are `true` or `false`. Numbers are plain numbers in
   the unit stated in the definition.
4. **Always attach a source** (`sources` row: type, URL, title) and choose its type honestly:
   `official_docs`, `provider_api`, `manual_review` or `scraped`.
5. **Verification.** New or scraped facts start `unverified`. A person confirms the fact against its source,
   then sets `verified` and `last_verified_at`. The database refuses `verified` without a date.
6. **Missing means "not recorded".** Do not store `false`, `null` or a guess for something you do not know.
   Leave out the row. The agent then says "not recorded", and a search does not wrongly rule the model out.
7. **Price and API availability are facts** with sources, like everything else. The model's `description` is
   display text only; the agent never makes claims from it.
8. **Re-verify on a schedule.** Models change roughly monthly; old verified dates are visible to users.

## Measures and filters

Every capability is one or the other. This is how the agent knows how to query it.

- **Measure**: a quantity you compare, threshold or sort (clip length, image size, context length, model
  size, price). Always a number or an ordered enum. Has a `better` direction where one exists (higher clip
  length is better, lower price is better; model size has none).
- **Filter**: an attribute you include or exclude (spoken languages, open weights, API available,
  deprecated, input types).

When you add a capability, decide which it is. A measure on a yes/no value is rejected by the database.

## Prices

Prices are stored as comparable numbers, **one measure per unit**, in US dollars at list price:

| Measure | Unit | For |
| --- | --- | --- |
| `price_usd_per_second` | USD per second of output | video, speech |
| `price_usd_per_image` | USD per image | image |
| `price_usd_per_1m_input_tokens` | USD per 1M tokens | text |
| `price_usd_per_1m_output_tokens` | USD per 1M tokens | text |

Put tier, resolution or free-allowance details in `pricing_notes` (text, display only). If a model has several
prices, record the standard list price for the measure and explain the rest in the notes. If a unit you need
does not exist (for example per clip), add a new measure with the owner; do not put prices in free text.

## Draft vocabulary

Seeded from `supabase/seed/vocabulary_draft.sql`. **The data team should confirm or change these before real
data goes in.**

| Name | Kind | Type | Allowed values or unit |
| --- | --- | --- | --- |
| `speech_languages` | filter | list | ISO 639-1 codes in the seed |
| `output_languages` | filter | list | same codes |
| `audio_generation` | filter | boolean | |
| `background_audio` | filter | boolean | |
| `api_available` | filter | boolean | |
| `open_weights` | filter | boolean | false means closed or proprietary |
| `deprecated` | filter | boolean | |
| `input_modalities`, `output_modalities` | filter | list | text, image, audio, video |
| `pricing_notes` | filter | text | display only |
| `max_resolution` | measure | ordered | 480p, 720p, 1080p, 1440p, 2160p |
| `max_clip_seconds` | measure | number | seconds |
| `max_image_megapixels` | measure | number | megapixels |
| `context_window_tokens` | measure | number | tokens |
| `parameter_count_billions` | measure | number | billion parameters |
| `price_usd_per_second`, `price_usd_per_image`, `price_usd_per_1m_input_tokens`, `price_usd_per_1m_output_tokens` | measure | number | see Prices |

To add a language or value, update that definition's `allowed_values` and add its everyday names to
`value_aliases`. To add a capability, insert into `capability_definitions` and `capability_aliases`.

## Aliases: how search becomes forgiving

People do not type canonical terms. `capability_aliases` maps everyday words to a capability ("voiceover",
"dubbing", "narration" all mean `speech_languages`; "model size" means `parameter_count_billions`).
`value_aliases` maps everyday values to the canonical one ("Hindi", "हिन्दी" to `hi`; "4K" to `2160p`;
"closed source" to `open_weights = false`).

Adding aliases is the cheapest way to improve search. No code change is needed.

## Verified queries (tier 1)

A verified query records that a kind of question maps to a particular query. It stores the **question and its
query spec, never an answer**, so results are always live and cited.

Each entry has:

- a `question` (canonical wording; `{slot}` names allowed for display) and a short `title`;
- `query_spec`: modality, filters, measures, sort, limit, with `"{{slot}}"` where the user's words go;
- `slots`: for each slot its name, which capability it fills, whether it is required, a default, a description;
- several **paraphrases** in `verified_query_phrases`: concrete ways people ask it. More paraphrases mean
  better matching; use realistic wording, including informal and mixed-language phrasing.

### Lifecycle

1. **Draft.** Entries are created as `draft`. The agent ignores drafts.
2. **Review.** A person checks that the question, the spec and the paraphrases are right, and runs the spec
   against current data to see the result looks sensible.
3. **Verify.** Set `status = 'verified'` with `verified_by` and `verified_at`. The database refuses
   `verified` without both. Only verified entries are used.
4. **Retire.** Set `status = 'retired'` when a question is no longer relevant or a capability was renamed.

The starter drafts in `supabase/seed/verified_queries_draft.sql` are examples to review, not verified.
Your twenty test questions are good candidates for new entries.

## Improving search over time

The `review_queue` view lists repeated questions that the agent answered at tier 2 or could not answer, most
frequent first. Work through it in this order:

1. **Add a verified query** for the common question (cheapest and safest). Include its paraphrases.
2. **Extend measures, filters and aliases** if the question exposed a missing concept or wording
   (for example, add an alias, or a new measure such as price per clip).
3. **Fill data gaps** if the question is well understood but the facts are not recorded.

Mark each row's `review_status` as `reviewed`, `promoted` (turned into a verified query) or `ignored`.

## Checklist before adding a model

- [ ] Model row created (provider, name, slug, modality).
- [ ] Every fact uses a defined name and canonical value.
- [ ] Prices are in US dollars, per the matching unit measure.
- [ ] Every fact has a source with a URL.
- [ ] Facts only confirmed against the source are marked `verified` with a date.
- [ ] Nothing unknown was stored as `false` or a guess.
