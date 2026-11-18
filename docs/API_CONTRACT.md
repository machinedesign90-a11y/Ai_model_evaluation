# Chat API contract

For the frontend developer and for the agent service. Base path `/v1`. JSON unless noted.
Build against `MOCK_AGENT=true` first; it returns canned responses in exactly these shapes.

## Authentication

Every endpoint except `/healthz` needs `Authorization: Bearer <Supabase access token>`.
The service verifies the JWT and uses its `sub` as the user id. Missing or invalid token returns `401`:

```json
{ "error": { "code": "unauthorized", "message": "Sign in to continue." } }
```

Registration and sign-in are handled by Supabase Auth in the frontend; users do not call this API to register.

## `POST /v1/chat` (Server-Sent Events)

Send one user message and stream the reply.

Request:

```json
{ "conversation_id": "uuid, optional", "message": "string, 1 to 2000 chars" }
```

- Omit `conversation_id` to start a new conversation.
- If the given conversation is expired (24 hours inactive) or not owned by the user, the service starts a
  new one and says so in the first event.

Response: `200`, `Content-Type: text/event-stream`. Each event has an `event:` name and a JSON `data:`.

| Event | Data | Meaning |
| --- | --- | --- |
| `conversation` | `{"conversation_id": "uuid", "is_new": true}` | Always first. Tells the UI which conversation this is. |
| `status` | `{"phase": "searching"}` | Optional progress hint while tools run. |
| `delta` | `{"text": "partial text"}` | Streamed answer text; append in order. |
| `done` | `{"message_id": "uuid", "remaining_messages": 2, "shortlist": ["slug", "..."]}` | Final event on success. |
| `error` | `{"code": "model_unavailable", "message": "..."}` | Final event if something fails mid-stream. |

### Failures before streaming starts

Returned as normal HTTP errors with the same error body shape:

| Status | `code` | When |
| --- | --- | --- |
| 400 | `invalid_request` | Empty or too-long message, bad JSON. |
| 401 | `unauthorized` | Missing or invalid token. |
| 402 | `free_tier_exhausted` | The user has used all free messages. Body also has `"remaining_messages": 0`. The UI should show the "limit reached" state. |
| 429 | `rate_limited` | Too many requests too fast. Includes `Retry-After`. |
| 503 | `model_unavailable` | The model service is down or throttled. |

## `GET /v1/me`

```json
{ "user_id": "uuid", "email": "a@example.com", "messages_used": 2, "message_limit": 4, "remaining_messages": 2 }
```

Use it to show "2 free messages left" on page load.

## `GET /v1/conversations/{id}`

Returns the stored history so the UI can restore a chat. `404` if not found or not owned.

```json
{
  "conversation_id": "uuid",
  "status": "active | expired",
  "started_at": "2026-11-18T06:00:00Z",
  "last_activity_at": "2026-11-18T06:05:00Z",
  "use_case": "string or null",
  "shortlist": ["slug"],
  "messages": [
    { "id": "uuid", "role": "user | assistant", "text": "string", "created_at": "..." }
  ]
}
```

Tool-call internals are not returned.

## `GET /healthz`

`200 {"status": "ok"}`. No auth. Used by the host's health check.

## Notes for the UI

- Show the answer as it streams; citations (source and verified date) arrive inside the text.
- After `done`, update the remaining-messages counter from `remaining_messages`.
- When `free_tier_exhausted` arrives, disable the input and show the limit message. Future phases will
  add a paid option here.
- CORS allowed origins come from the `CORS_ORIGINS` environment variable.
