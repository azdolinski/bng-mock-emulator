# BNG mock emulator

Lightweight **Flask** API that simulates a PPPoE BNG (NAS): sends RADIUS auth/accounting to a configured server, listens for **CoA** / **Disconnect**, and keeps a full exchange log.

## Quick start

```bash
cd bng-mock-emulator
docker compose up -d --build
curl http://127.0.0.1:18080/health
```

Base URL (Docker): `http://127.0.0.1:18080`

---

## API index

| ID | Method | Path | Description |
|----|--------|------|-------------|
| **1** | `GET` | `/health` | Server status + list of active sessions |
| **2** | `POST` | `/users` | Create / update a PPPoE client profile |
| **3** | `GET` | `/users` | List profiles and `active` flag |
| **4** | `GET` | `/users/{username}` | Profile details + session state |
| **5** | `DELETE` | `/users/{username}` | Delete profile (must be stopped first) |
| **6** | `POST` | `/users/{username}/start` | Auth + Acct-Start, register session |
| **7** | `POST` | `/users/{username}/stop` | Acct-Stop + unregister session |
| **8** | `GET` | `/logs` | RADIUS/API exchange history |
| **9** | `DELETE` | `/logs` | Clear log |
| **10** | `PUT` | `/config/coa-response` | Default NAS response to CoA/Disconnect |

Outside HTTP: **CoA / Disconnect** on UDP `:3799` (see section at the end).

---

## 1. `GET /health`

Check whether the emulator is running and list users with an active session.

### Request schema

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| — | — | — | — | No parameters |

### Response schema

| Field | Type | Description |
|-------|------|-------------|
| `status` | `string` | Always `"ok"` on success |
| `coa_port` | `integer` | UDP CoA listen port (env `BNG_COA_PORT`) |
| `active_sessions` | `string[]` | User-Name values with an active session |

**HTTP:** `200 OK`

### Example

```bash
curl -s http://127.0.0.1:18080/health
```

**Server response:**

```json
{
  "status": "ok",
  "coa_port": 3799,
  "active_sessions": ["pppoe-demo"]
}
```

---

## 2. `POST /users`

Defines a client profile (credentials, target RADIUS, NAS). If the user already exists, the profile is overwritten (unless a session is active).

### Request schema

**Content-Type:** `application/json`

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `username` | `string` | **yes** | — | User-Name in RADIUS |
| `password` | `string` | **yes** | — | Password (PAP/CHAP) |
| `radius_host` | `string` | no | `"127.0.0.1"` | RADIUS server address |
| `radius_auth_port` | `integer` | no | `2812` | Auth port |
| `radius_acct_port` | `integer` | no | `2813` | Accounting port |
| `radius_secret` | `string` | no | `BNG_RADIUS_SECRET` | Shared secret for RADIUS |
| `nas_ip` | `string` | no | `BNG_NAS_IP` | NAS-IP-Address in packets |
| `auth_method` | `string` | no | `"chap"` | `"chap"` or `"pap"` |
| `accounting_enabled` | `boolean` | no | `true` | Send Acct Start/Stop |
| `accounting_interim_seconds` | `integer` | no | `0` | Interim-Update every N seconds; `0` = Start/Stop only |
| `extra_auth_attrs` | `object` | no | `{}` | Extra attributes in Access-Request |
| `extra_acct_attrs` | `object` | no | `{}` | Extra attributes in Accounting-Request |

### Response schema

**Success — `201 Created`**

| Field | Type | Description |
|-------|------|-------------|
| `username` | `string` | Created / updated user |
| `created` | `boolean` | Always `true` |

**Error — `400 Bad Request`**

| Field | Type | Description |
|-------|------|-------------|
| `error` | `string` | e.g. `"username and password are required"` |

**Error — `409 Conflict`**

| Field | Type | Description |
|-------|------|-------------|
| `error` | `string` | e.g. session active — call `POST .../stop` first |

### Example

```bash
curl -s -X POST http://127.0.0.1:18080/users \
  -H 'Content-Type: application/json' \
  -d '{
    "username": "pppoe-demo",
    "password": "test-pass",
    "radius_host": "192.1.0.2",
    "radius_auth_port": 2812,
    "radius_acct_port": 2813,
    "radius_secret": "testing123",
    "nas_ip": "10.255.0.1",
    "auth_method": "chap",
    "accounting_enabled": true,
    "accounting_interim_seconds": 60
  }'
```

**Server response (`201`):**

```json
{
  "username": "pppoe-demo",
  "created": true
}
```

---

## 3. `GET /users`

List all defined profiles.

### Request schema

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| — | — | — | — | No parameters |

### Response schema

**HTTP:** `200 OK`

| Field | Type | Description |
|-------|------|-------------|
| `users` | `object[]` | Array of profiles |

Each element in `users[]`:

| Field | Type | Description |
|-------|------|-------------|
| `username` | `string` | User-Name |
| `radius_host` | `string` | Target RADIUS |
| `radius_auth_port` | `integer` | Auth port |
| `radius_acct_port` | `integer` | Acct port |
| `auth_method` | `string` | `"chap"` / `"pap"` |
| `accounting_enabled` | `boolean` | Whether accounting is enabled |
| `active` | `boolean` | Whether a session is registered |
| `acct_session_id` | `string \| null` | Accounting session ID (when `active`) |

### Example

```bash
curl -s http://127.0.0.1:18080/users
```

**Server response:**

```json
{
  "users": [
    {
      "username": "pppoe-demo",
      "radius_host": "192.1.0.2",
      "radius_auth_port": 2812,
      "radius_acct_port": 2813,
      "auth_method": "chap",
      "accounting_enabled": true,
      "active": true,
      "acct_session_id": "bng-pppoe-demo-a1b2c3d4"
    }
  ]
}
```

---

## 4. `GET /users/{username}`

Details for one profile and session state.

### Request schema

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `username` | path | `string` | **yes** | User-Name |

### Response schema

**Success — `200 OK`**

| Field | Type | Description |
|-------|------|-------------|
| `username` | `string` | User-Name |
| `radius_host` | `string` | Target RADIUS |
| `radius_auth_port` | `integer` | Auth port |
| `radius_acct_port` | `integer` | Acct port |
| `nas_ip` | `string` | NAS-IP-Address |
| `auth_method` | `string` | Auth method |
| `accounting_enabled` | `boolean` | Accounting on/off |
| `accounting_interim_seconds` | `integer` | Interim interval |
| `active` | `boolean` | Whether session is active |
| `session` | `object \| null` | `null` when no session |

`session` object (when `active: true`):

| Field | Type | Description |
|-------|------|-------------|
| `acct_session_id` | `string` | Acct-Session-Id |
| `started_at` | `string` | ISO 8601 UTC (session start) |

**Error — `404 Not Found`**

| Field | Type | Description |
|-------|------|-------------|
| `error` | `string` | `"not found"` |

### Example

```bash
curl -s http://127.0.0.1:18080/users/pppoe-demo
```

**Server response (active session):**

```json
{
  "username": "pppoe-demo",
  "radius_host": "192.1.0.2",
  "radius_auth_port": 2812,
  "radius_acct_port": 2813,
  "nas_ip": "10.255.0.1",
  "auth_method": "chap",
  "accounting_enabled": true,
  "accounting_interim_seconds": 60,
  "active": true,
  "session": {
    "acct_session_id": "bng-pppoe-demo-a1b2c3d4",
    "started_at": "2026-05-19T12:34:56.789012+00:00"
  }
}
```

---

## 5. `DELETE /users/{username}`

Deletes a profile. Requires stopping the session first (`POST .../stop`).

### Request schema

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `username` | path | `string` | **yes** | User-Name |

### Response schema

**Success — `200 OK`**

| Field | Type | Description |
|-------|------|-------------|
| `username` | `string` | Deleted user |
| `deleted` | `boolean` | Always `true` |

**Error — `404 Not Found`**

| Field | Type | Description |
|-------|------|-------------|
| `error` | `string` | `"not found"` |

**Error — `409 Conflict`**

| Field | Type | Description |
|-------|------|-------------|
| `error` | `string` | Session still active |

### Example

```bash
curl -s -X DELETE http://127.0.0.1:18080/users/pppoe-demo
```

**Server response:**

```json
{
  "username": "pppoe-demo",
  "deleted": true
}
```

---

## 6. `POST /users/{username}/start`

Simulates PPPoE connect: sends **Access-Request** to RADIUS, on `Access-Accept` registers the session and (optionally) sends **Accounting-Start**.

### Request schema

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `username` | path | `string` | **yes** | Existing profile (`POST /users`) |

No body.

### Response schema

**Success — `200 OK`**

| Field | Type | Description |
|-------|------|-------------|
| `username` | `string` | User-Name |
| `started` | `boolean` | `true` |
| `auth_result` | `string` | `"Access-Accept"` |
| `acct_session_id` | `string` | Generated Acct-Session-Id |
| `reply` | `object` | Attributes from Access-Accept (key → string value) |

**Auth denied — `422 Unprocessable Entity`**

| Field | Type | Description |
|-------|------|-------------|
| `username` | `string` | User-Name |
| `started` | `boolean` | `false` |
| `auth_result` | `string` | e.g. `"Access-Reject"` |
| `reply` | `object` | RADIUS response attributes |

**Error — `404 Not Found`**

| Field | Type | Description |
|-------|------|-------------|
| `error` | `string` | `"user not found; POST /users first"` |

**Error — `409 Conflict`**

| Field | Type | Description |
|-------|------|-------------|
| `error` | `string` | Session already active |

**Error — `502 Bad Gateway`**

| Field | Type | Description |
|-------|------|-------------|
| `error` | `string` | Auth or Acct-Start error (timeout, network, RADIUS) |

### Example

```bash
curl -s -X POST http://127.0.0.1:18080/users/pppoe-demo/start
```

**Server response (success):**

```json
{
  "username": "pppoe-demo",
  "started": true,
  "auth_result": "Access-Accept",
  "acct_session_id": "bng-pppoe-demo-a1b2c3d4",
  "reply": {
    "Framed-IP-Address": "10.0.0.42",
    "Session-Timeout": "3600"
  }
}
```

**Response on Access-Reject (`422`):**

```json
{
  "username": "pppoe-demo",
  "started": false,
  "auth_result": "Access-Reject",
  "reply": {
    "Reply-Message": "Invalid credentials"
  }
}
```

---

## 7. `POST /users/{username}/stop`

Simulates disconnect: **Accounting-Stop** (if enabled) and session unregister.

### Request schema

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `username` | path | `string` | **yes** | User-Name with an active session |

No body.

### Response schema

**Success — `200 OK`**

| Field | Type | Description |
|-------|------|-------------|
| `username` | `string` | User-Name |
| `stopped` | `boolean` | Always `true` |

**Error — `404 Not Found`**

| Field | Type | Description |
|-------|------|-------------|
| `error` | `string` | `"not found"` (profile missing) |

**Error — `409 Conflict`**

| Field | Type | Description |
|-------|------|-------------|
| `error` | `string` | `"no active session"` |

**Error — `502 Bad Gateway`**

| Field | Type | Description |
|-------|------|-------------|
| `error` | `string` | Acct-Stop error |

### Example

```bash
curl -s -X POST http://127.0.0.1:18080/users/pppoe-demo/stop
```

**Server response:**

```json
{
  "username": "pppoe-demo",
  "stopped": true
}
```

---

## 8. `GET /logs`

Full history of RADIUS and API exchanges (append-only).

### Request schema

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `username` | query | `string` | no | Filter by User-Name |
| `limit` | query | `integer` | no | Last N entries (after filter) |

### Response schema

**HTTP:** `200 OK`

| Field | Type | Description |
|-------|------|-------------|
| `total` | `integer` | Number of returned entries |
| `entries` | `object[]` | Log entries |

Each `entries[]` element:

| Field | Type | Description |
|-------|------|-------------|
| `id` | `integer` | Sequential entry number |
| `ts` | `string` | ISO 8601 UTC |
| `direction` | `string` | `"outbound"` \| `"inbound"` |
| `exchange` | `string` | `"auth"` \| `"acct"` \| `"coa"` \| `"disconnect"` \| `"api"` |
| `username` | `string \| null` | Associated user |
| `packet` | `string` | Packet name / endpoint |
| `attributes` | `object` | RADIUS attributes (string → string) |
| `peer` | `string \| null` | Peer address (e.g. `192.1.0.2:2812`) |
| `note` | `string \| null` | Optional note |

### Example

```bash
curl -s 'http://127.0.0.1:18080/logs?username=pppoe-demo&limit=10'
```

**Server response:**

```json
{
  "total": 2,
  "entries": [
    {
      "id": 1,
      "ts": "2026-05-19T12:34:55.100000+00:00",
      "direction": "outbound",
      "exchange": "auth",
      "username": "pppoe-demo",
      "packet": "Access-Request",
      "attributes": {
        "User-Name": "pppoe-demo",
        "NAS-IP-Address": "10.255.0.1"
      },
      "peer": "192.1.0.2:2812",
      "note": null
    },
    {
      "id": 2,
      "ts": "2026-05-19T12:34:55.200000+00:00",
      "direction": "inbound",
      "exchange": "auth",
      "username": "pppoe-demo",
      "packet": "Access-Accept",
      "attributes": {
        "Framed-IP-Address": "10.0.0.42"
      },
      "peer": "192.1.0.2:2812",
      "note": null
    }
  ]
}
```

---

## 9. `DELETE /logs`

Clears the entire exchange log.

### Request schema

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| — | — | — | — | No parameters |

### Response schema

**HTTP:** `200 OK`

| Field | Type | Description |
|-------|------|-------------|
| `cleared` | `integer` | Number of removed entries |

### Example

```bash
curl -s -X DELETE http://127.0.0.1:18080/logs
```

**Server response:**

```json
{
  "cleared": 42
}
```

---

## 10. `PUT /config/coa-response`

Sets the default mock NAS response to incoming **CoA** / **Disconnect** (when session is inactive or for global testing).

### Request schema

**Content-Type:** `application/json`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `mode` | `string` | **yes** | `"ack"` → CoA-ACK / Disconnect-ACK; `"nak"` → CoA-NAK / Disconnect-NAK |

### Response schema

**Success — `200 OK`**

| Field | Type | Description |
|-------|------|-------------|
| `coa_default_response` | `string` | `"ack"` or `"nak"` |

**Error — `400 Bad Request`**

| Field | Type | Description |
|-------|------|-------------|
| `error` | `string` | `"mode must be 'ack' or 'nak'"` |

### Example

```bash
curl -s -X PUT http://127.0.0.1:18080/config/coa-response \
  -H 'Content-Type: application/json' \
  -d '{"mode": "ack"}'
```

**Server response:**

```json
{
  "coa_default_response": "ack"
}
```

---

## CoA / Disconnect (UDP, not REST)

The emulator listens on **UDP 3799** (env `BNG_COA_PORT`), secret `BNG_COA_SECRET` (default `testing123`).

| Condition | CoA | Disconnect |
|-----------|-----|------------|
| Active session for `User-Name` | `CoA-ACK` | `Disconnect-ACK` (+ session cleared) |
| Missing / stopped session | `CoA-NAK` | `Disconnect-NAK` |
| `PUT /config/coa-response` → `"nak"` | Forces NAK (testing) | Forces NAK |

Log entry (`GET /logs`): `exchange` = `"coa"` or `"disconnect"`, `direction` = `"inbound"`.

Register the mock NAS in the Share testbed with `nasname` = container IP on the test network (**192.1.0.10** with the `testbed` profile).

---

## Environment

| Variable | Default | Meaning |
|----------|---------|---------|
| `BNG_API_HOST` | `0.0.0.0` | Flask bind |
| `BNG_API_PORT` | `8080` | Flask port |
| `BNG_COA_HOST` | `0.0.0.0` | CoA listener bind |
| `BNG_COA_PORT` | `3799` | CoA/Disconnect UDP |
| `BNG_COA_SECRET` | `testing123` | NAS secret (proxy → BNG) |
| `BNG_RADIUS_SECRET` | `testing123` | Secret to RADIUS server |
| `BNG_NAS_IP` | `10.255.0.1` | NAS-IP-Address in packets |
| `BNG_DICTIONARY` | `/app/dictionary` | pyrad dictionary |

## Testbed network

```bash
docker compose --profile testbed up -d --build
```

Uses external network `freeradius-testbed` at **192.1.0.10** (same role as `CoAHelper` mock BNG in Jupyter tests).

## Local run (no Docker)

```bash
pip install -r requirements.txt
python -m src.app
```

## Typical test flow

```bash
# 2 — profile
curl -s -X POST http://127.0.0.1:18080/users -H 'Content-Type: application/json' \
  -d '{"username":"pppoe-demo","password":"test-pass","radius_host":"192.1.0.2"}'

# 6 — start (auth + acct)
curl -s -X POST http://127.0.0.1:18080/users/pppoe-demo/start

# 8 — log
curl -s 'http://127.0.0.1:18080/logs?username=pppoe-demo' | jq .

# 7 — stop
curl -s -X POST http://127.0.0.1:18080/users/pppoe-demo/stop
```
