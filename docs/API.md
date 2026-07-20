# Control HTTP API

When the recorder is started in multi-user automatic mode (i.e. `-mode automatic`
with a comma-separated `-user` list, or with **no arguments** at all), it
exposes a local HTTP control API on `http://127.0.0.1:8723`. The API is the
single integration point for a desktop dashboard: all user management and
status inspection goes through it.

- **Base URL:** `http://127.0.0.1:8723`
- **Bind address:** `127.0.0.1` only (no auth, intended for local use)
- **Content-Type:** all request and response bodies are `application/json`
- **Charset:** UTF-8

All endpoints return JSON. Endpoints that take a body require
`Content-Type: application/json`.

---

## Table of contents

- [Conventions](#conventions)
- [Endpoints](#endpoints)
  - [`GET /list`](#get-list)
  - [`POST /add`](#post-add)
  - [`POST /remove`](#post-remove)
  - [`GET /cookies`](#get-cookies)
  - [`POST /cookies`](#post-cookies)
  - [`GET /interval`](#get-interval)
  - [`POST /interval`](#post-interval)
- [Status state machine](#status-state-machine)
- [User object](#user-object)
- [Error responses](#error-responses)
- [HTTP status codes](#http-status-codes)
- [Lifecycle notes](#lifecycle-notes)

---

## Conventions

### Request bodies

Bodies must be a JSON object (`{...}`). Arrays, scalars, and `null` are
rejected.

### Timestamps

Timestamps in responses (`since`) are POSIX seconds with a fractional
component (float, UTC epoch). Convert with:

```js
new Date(entry.since * 1000).toISOString();
```

### Polling

There is no WebSocket / SSE push. The intended pattern is to poll
[`GET /list`](#get-list) at a fixed interval (e.g. every 1–2 seconds) and
diff the result against the previous snapshot. The `since` field on each
entry changes on every status transition and can be used as a cheap
"has anything changed?" hint.

---

## Endpoints

### `GET /list`

Return the current set of monitored users and each one's recording status.

**Request:** no body.

**Response — `200 OK`:**

```json
{
  "users": [
    {
      "user": "alice",
      "status": "waiting",
      "since": 1734567890.123,
      "message": ""
    },
    {
      "user": "bob",
      "status": "live",
      "since": 1734567891.456,
      "message": ""
    }
  ]
}
```

The `users` array is sorted by `user` ascending. The list contains only
users that are currently being managed (a user that was removed and whose
recorder process has fully exited will no longer appear). See
[Lifecycle notes](#lifecycle-notes).

**Error responses:** none beyond the standard 404 / 405 below.

---

### `POST /add`

Start recording a new user. Equivalent to adding `-user <name>` at the
command line — the recorder spawns a new background process that polls the
user's live status and records whenever they go live.

**Request body:**

```json
{ "user": "charlie" }
```

The leading `@` is stripped; surrounding whitespace is ignored. The value
must be a non-empty string after normalization.

**Responses:**

| Status | Body | Meaning |
|--------|------|---------|
| `200 OK` | `{"ok": true, "user": "charlie"}` | Recorder process started. |
| `400 Bad Request` | `{"error": "missing or empty 'user' field"}` | `user` is not a string or is blank. |
| `400 Bad Request` | `{"error": "<message>"}` | Validation rejected the value (e.g. empty after strip). |
| `409 Conflict` | `{"error": "user 'charlie' is already being recorded"}` | A recorder for this user is already running. |
| `415 Unsupported Media Type` | `{"error": "Content-Type must be application/json"}` | Wrong / missing `Content-Type`. |

**Side effects:**

- A new recorder process is spawned.
- The user is appended to `settings.json` (persisted for next startup).
- The user appears in subsequent `/list` responses with `status: "waiting"`.

---

### `POST /remove`

Stop recording a user. The behavior depends on the user's current state
(see [Status state machine](#status-state-machine)):

- If the user is **not** currently recording (status `waiting` or `error`),
  the recorder process is terminated immediately and the user is removed
  from the list.
- If the user **is** recording (status `live`), the `removed` flag is set
  on the recorder and the process exits gracefully when the current
  recording finishes. The user remains visible in `/list` with
  `status: "stopped"` until the process exits, then disappears.

**Request body:**

```json
{ "user": "charlie" }
```

**Responses:**

| Status | Body | Meaning |
|--------|------|---------|
| `200 OK` | `{"ok": true, "user": "charlie"}` | Remove request accepted. |
| `400 Bad Request` | `{"error": "missing or empty 'user' field"}` | `user` is not a string or is blank. |
| `404 Not Found` | `{"error": "user 'charlie' is not being recorded"}` | No such user is currently managed. |
| `415 Unsupported Media Type` | `{"error": "Content-Type must be application/json"}` | Wrong / missing `Content-Type`. |

**Note:** `POST /remove` for a currently-`live` user returns `200` even
though the recorder has not yet exited. Watch `status` on subsequent
`/list` calls to observe the graceful shutdown.

---

### `GET /cookies`

Return the current contents of `cookies.json`.

**Request:** no body.

**Response — `200 OK`:**

```json
{
  "sessionid_ss": "e22cdac5b3abf83555083f5e8d4c7895",
  "tt-target-idc": "eu-ttp2"
}
```

The shape matches `cookies.json` exactly — all keys are returned, not just
`sessionid_ss`.

**Responses:**

| Status | Body | Meaning |
|--------|------|---------|
| `200 OK` | the cookies object | Success. |
| `404 Not Found` | `{"error": "not found"}` | The recorder was not started with cookies support wired in. |
| `500 Internal Server Error` | `{"error": "could not read cookies: <reason>"}` | File I/O error. |

---

### `POST /cookies`

Update `sessionid_ss` in `cookies.json`. Other keys in the file are
preserved. Useful for rotating the session cookie without restarting the
recorder.

**Request body:**

```json
{ "sessionid_ss": "new-session-id-here" }
```

The value must be a non-empty string after stripping whitespace.

**Responses:**

| Status | Body | Meaning |
|--------|------|---------|
| `200 OK` | `{"ok": true, "sessionid_ss": "new-session-id-here"}` | File updated. |
| `400 Bad Request` | `{"error": "missing or empty 'sessionid_ss' field"}` | Field missing, not a string, or blank. |
| `404 Not Found` | `{"error": "not found"}` | The recorder was not started with cookies support wired in. |
| `500 Internal Server Error` | `{"error": "could not read cookies: <reason>"}` | Could not read existing file. |
| `500 Internal Server Error` | `{"error": "could not write cookies: <reason>"}` | File I/O error during write. |
| `415 Unsupported Media Type` | `{"error": "Content-Type must be application/json"}` | Wrong / missing `Content-Type`. |

**Important:** recorders that are already running keep the cookies they
were started with — `RecorderConfig.cookies` is captured per process at
spawn time. The new `sessionid_ss` takes effect for any recorder spawned
*after* the update (i.e. via `POST /add`). To pick up a new cookie in
existing recorders, `POST /remove` and `POST /add` the same user.

---

### `GET /interval`

Return the current automatic-mode recheck interval, in minutes. This is
the same value passed at startup via `-automatic_interval`, but mutable
at runtime via [`POST /interval`](#post-interval).

**Request:** no body.

**Response — `200 OK`:**

```json
{ "interval": 5 }
```

**Responses:**

| Status | Body | Meaning |
|--------|------|---------|
| `200 OK` | `{"interval": <int>}` | Success. |
| `404 Not Found` | `{"error": "not found"}` | The recorder was not started with interval support wired in. |
| `500 Internal Server Error` | `{"error": "could not read interval: <reason>"}` | Unexpected error reading the shared value. |

---

### `POST /interval`

Change the automatic-mode recheck interval at runtime.

**Request body:**

```json
{ "interval": 10 }
```

The value must be an integer ≥ 1 (minutes). Booleans, floats, and strings
are rejected.

**Behavior:**

- The current in-flight sleep in each recorder finishes with the **old**
  interval.
- The **next** sleep in each recorder uses the **new** interval.
- Because each recorder reads the interval fresh at the start of every
  sleep and recorders are at different points in their cycle (they were
  spawned at different times), the new interval takes effect at
  different moments for different users. Timers do **not** reset in
  unison.

**Responses:**

| Status | Body | Meaning |
|--------|------|---------|
| `200 OK` | `{"ok": true, "interval": 10}` | Interval updated. |
| `400 Bad Request` | `{"error": "'interval' must be an integer"}` | Wrong type (bool, float, string, null, missing). |
| `400 Bad Request` | `{"error": "'interval' must be one minute or more"}` | Value < 1. |
| `404 Not Found` | `{"error": "not found"}` | The recorder was not started with interval support wired in. |
| `415 Unsupported Media Type` | `{"error": "Content-Type must be application/json"}` | Wrong / missing `Content-Type`. |
| `500 Internal Server Error` | `{"error": "could not write interval: <reason>"}` | Unexpected error writing the shared value. |

---

## Status state machine

Each user in `/list` has a `status` field that cycles through these values:

```
            ┌──────────┐
   spawn ─▶ │ waiting  │ ◀──────────────┐
            └────┬─────┘                │
                 │                      │
            user goes live              │
                 │                      │
                 ▼                      │
            ┌──────────┐                │
            │   live   │ ─── user ends ─┤
            └────┬─────┘                │
                 │                      │
        unexpected exception             │
                 │                      │
                 ▼                      │
            ┌──────────┐                │
            │  error   │ ─── retry ok ──┘
            └──────────┘
                 │
            POST /remove
            (or recorder exits)
                 │
                 ▼
            ┌──────────┐
            │ stopped  │ (terminal, then entry is removed)
            └──────────┘
```

| Status | Meaning |
|--------|---------|
| `waiting` | Recorder is alive and polling for the user to go live. |
| `live` | Recorder is currently downloading and saving a live stream. |
| `error` | Last poll cycle raised an unexpected exception. The recorder retries on the next interval. `message` contains the exception text. |
| `stopped` | Recorder has exited. The entry remains visible only briefly while a graceful `POST /remove` shutdown is in progress, then disappears. |

`waiting` is also the implicit state for any managed user whose status
has never been observed (it is the initial value on spawn).

---

## User object

Each element of the `users` array in `/list` has the following fields:

| Field | Type | Description |
|-------|------|-------------|
| `user` | string | Normalized username (no leading `@`, no surrounding whitespace). |
| `status` | string | One of `waiting`, `live`, `error`, `stopped`. |
| `since` | float | POSIX timestamp (seconds, UTC) of the last status transition for this user. Use this to detect changes between polls. |
| `message` | string | Free-form text. Set on `error` to the exception message; on `stopped` carries over the last `message` (e.g. the last error before exit). Empty otherwise. |

---

## Error responses

Every non-2xx response has the shape:

```json
{ "error": "human-readable description" }
```

The `error` string is intended for humans (logging, debugging) — do not
parse it. Instead, branch on the HTTP status code.

---

## HTTP status codes

The API uses these status codes:

| Code | Used for |
|------|----------|
| `200 OK` | Successful read or mutation. |
| `400 Bad Request` | Body is not JSON, missing required field, or value failed validation. |
| `404 Not Found` | Unknown path, or a `/remove` for an unknown user, or an endpoint that wasn't enabled at startup. |
| `405 Method Not Allowed` | Wrong HTTP verb (e.g. `GET /add`). |
| `409 Conflict` | `POST /add` for a user that already has a recorder. |
| `415 Unsupported Media Type` | `Content-Type` is not `application/json`. |
| `500 Internal Server Error` | Unexpected server-side error (file I/O, etc.). |

---

## Lifecycle notes

**Startup.** The recorder reads `settings.json` from the current
working directory on startup and seeds the manager with any users found
there. Users added via `POST /add` are appended to this file; users
removed via `POST /remove` are dropped from it. Deleting the file resets
the managed user set on the next start.

**Persistence of cookies updates.** As noted under `POST /cookies`,
updating `sessionid_ss` does **not** propagate to currently-running
recorders. The pattern for a rotating cookie in a live dashboard is:

1. `POST /remove` the affected users (graceful — waits for recordings).
2. `POST /cookies` with the new `sessionid_ss`.
3. `POST /add` the users again to spawn fresh recorders.

**Graceful remove timing.** When you `POST /remove` a user that is
currently `live`, the HTTP call returns `200` immediately. The recorder
exits only after the in-progress stream ends. During this window, the
user remains in `/list` with `status: "stopped"`. Dashboard code should
treat this as "remove in progress" and stop displaying the user once it
disappears entirely from `/list`.

**No WebSocket / SSE.** This API is intentionally polling-only. A desktop
dashboard should poll `GET /list` on a fixed interval (1–2 s is
reasonable) and diff results to drive its UI.

**No authentication.** The server binds to `127.0.0.1` only and is
intended for local use. Do not expose it to the network without adding
auth and TLS upstream.

---

## Quick reference

```bash
# List users and statuses
curl -s http://127.0.0.1:8723/list

# Add a user
curl -s -X POST -H 'Content-Type: application/json' \
     -d '{"user":"charlie"}' http://127.0.0.1:8723/add

# Remove a user
curl -s -X POST -H 'Content-Type: application/json' \
     -d '{"user":"charlie"}' http://127.0.0.1:8723/remove

# Read cookies
curl -s http://127.0.0.1:8723/cookies

# Update sessionid_ss
curl -s -X POST -H 'Content-Type: application/json' \
     -d '{"sessionid_ss":"new-id"}' http://127.0.0.1:8723/cookies

# Read the current recheck interval (minutes)
curl -s http://127.0.0.1:8723/interval

# Change the recheck interval to 10 minutes
curl -s -X POST -H 'Content-Type: application/json' \
     -d '{"interval":10}' http://127.0.0.1:8723/interval
```