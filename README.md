# STN — Orbital Conjunction Ingestion & Screening Microservice

FastAPI + Pydantic v2 + SQLAlchemy 2.0 + Alembic + PostgreSQL 16, packaged with Docker Compose.
It ingests Conjunction Data Messages (CDMs), drops debris-on-debris noise before it costs any
database work, stores analytics-ready records (kilometres, honest `NULL`s), and keeps a background
worker ingesting mock records without ever slowing the REST API down.

---

## 1. Quick start (one command)

```bash
docker compose up --build
```

Then open <http://localhost:8000/docs> (Swagger UI) or:

```bash
curl -s localhost:8000/healthz
curl -s -X POST localhost:8000/api/v1/conjunctions/ingest \
  -H 'content-type: application/json' \
  -d '{"message_id":"CDM-2026-X992","event_time_utc":"2030-10-15T08:30:00Z",
       "primary_norad_id":25544,"primary_object_type":"PAYLOAD",
       "secondary_norad_id":41235,"secondary_object_type":"DEBRIS",
       "miss_distance_m":450.2,"collision_probability":0.00012,"relative_velocity_kms":11.4}'
curl -s 'localhost:8000/api/v1/conjunctions/active?min_miss_distance_km=0.1&has_collision_prob=true&limit=10'
```

What happens on boot:

1. `db` (postgres:16-alpine) starts with a named volume `pgdata` (data survives `docker compose down`).
2. The `api` container **waits for the `db` healthcheck (`pg_isready`) to pass** (`depends_on: condition: service_healthy`).
3. `docker-entrypoint.sh` confirms connectivity, runs `alembic upgrade head`, then starts Uvicorn.
4. ~immediately the background worker ingests its first mock batch, then one every 30 s.

Watch it work: `docker compose logs -f api` (you will see `triage_drop`, `ingest_done`, `worker_tick_ok`).
Stop: `docker compose down` (add `-v` to also delete the database volume).

Optional config: copy `.env.example` → `.env` (ports, credentials, `WORKER_INTERVAL_SECONDS`, …).

## 2. Running the tests

The suite needs a real PostgreSQL (the code uses `ON CONFLICT` and advisory locks) and builds its
schema **through Alembic**, so migrations are exercised on every run. It auto-creates a separate
`stn_test` database and refuses to run against any database whose name doesn't end in `_test`.

**With Docker (no local Python needed):**
```bash
docker compose --profile test run --rm tests
```

**Locally:**
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
# any reachable Postgres whose user may CREATE DATABASE:
export TEST_DATABASE_URL=postgresql+psycopg2://stn:stn@localhost:5432/stn_test
pytest -v
```

| Required coverage | File |
|---|---|
| 1. Ingestion + persistence | `tests/test_ingestion.py` |
| 2. Triage Gate (debris/debris dropped) | `tests/test_triage_gate.py` |
| 3. Data honesty (`None` stays `None`) | `tests/test_data_honesty.py` |
| 4. m → km conversion | `tests/test_unit_conversion.py` |
| Extra: `/active` filters & paging, worker, advisory lock, **<100 ms under load**, `/healthz`, migrations | `test_active_endpoint.py`, `test_worker_and_concurrency.py`, `test_health_and_migrations.py` |

58 tests. I also mutation-checked the suite: deliberately breaking the unit divisor, disabling the
triage gate, defaulting velocity to 7.5, and an `or None` falsy-zero bug each makes tests fail.

## 3. Architecture & directory layout

```
.
├── docker-compose.yml          # db (+volume, healthcheck) → api; opt-in `tests` profile
├── Dockerfile                  # multi-stage: base → test → runtime
├── docker-entrypoint.sh        # wait for DB → alembic upgrade head → uvicorn
├── alembic.ini / alembic/      # env.py + versions/0001_create_conjunction_events.py
├── app/
│   ├── main.py                 # app factory + lifespan (starts/stops the worker)
│   ├── config.py               # env-driven settings
│   ├── api/
│   │   ├── health.py           # GET /healthz
│   │   └── v1/conjunctions.py  # POST /ingest, GET /active
│   ├── schemas/conjunction.py  # Pydantic v2 contracts (payload stays in METERS)
│   ├── services/
│   │   ├── ingestion.py        # validate → Triage Gate → persist → commit (shared by API & worker)
│   │   └── mock_generator.py   # 5–10 randomized mock CDMs per batch
│   ├── repositories/conjunction_repository.py   # DB boundary: m→km conversion, bulk upsert, queries
│   ├── models/conjunction_event.py              # SQLAlchemy 2.0 typed model
│   ├── db/{base,session}.py    # engines, pooling, get_db dependency
│   └── worker/runner.py        # APScheduler background ingestion
└── tests/
```

Request flow: `HTTP/worker → CDMIn validation (per record) → Triage Gate → repository.to_row()
[m→km] → INSERT … ON CONFLICT DO NOTHING → commit`. The API and the worker share the *same*
pipeline function, so every business rule is enforced identically on both paths.

### Key business rules and where they live

| Rule | Implementation |
|---|---|
| Never fabricate missing data | `collision_probability` / `relative_velocity_kms` are `Optional[...] = None` in the schema, nullable columns **with no DB default**, and the mapper passes values through untouched (tests use `is None`, never truthiness, so a genuine `0.0` survives). |
| Units: m in, km stored | Schema validates and carries `miss_distance_m` unchanged. The **only** division by 1000 is `to_row()` in the repository (the DB boundary). Column is `miss_distance_km`; there is no meters column. |
| Triage Gate | `should_drop_by_triage()` runs after validation and **before** the repository is called; logs `triage_drop …`, reports the drop in the response, performs no DB write. |
| Strict contract | `extra="forbid"`, strict numeric types, probability ∈ [0,1], distance ≥ 0, timezone-aware timestamps (normalised to UTC), primary ≠ secondary. A feed sending `miss_distance_km` is *rejected* rather than silently ignored — a deliberate guard against km/m mix-ups. |

### Database
`conjunction_events` (created only by Alembic — there is no `create_all()` anywhere): BIGINT identity PK,
`TIMESTAMPTZ` event time, native PG enum `object_type`, `DOUBLE PRECISION` metrics, `CHECK` constraints
(range checks that let `NULL` through, distinct objects), a **unique index on `message_id`** (idempotent
re-delivery), and indexes on `event_time_utc`, `primary_norad_id` and `secondary_norad_id` (the `norad_id`
filter matches either side). `alembic check` reports no drift between model and migration, and
`downgrade base → upgrade head` is tested.

## 4. API reference

### `POST /api/v1/conjunctions/ingest`
Body: one CDM object **or** a JSON array of them (max `MAX_INGEST_BATCH_SIZE`, default 1000).
Records are validated individually, so one bad record never poisons a batch.

| Status | Meaning |
|---|---|
| `201` | ≥1 record persisted, none rejected |
| `200` | Processed, nothing new persisted (all dropped by triage and/or duplicates) |
| `207` | Mixed: some records rejected, the rest processed (see `rejected_records`) |
| `422` | Every record invalid / empty batch |
| `413` | Batch too large |

The response lists `persisted_message_ids`, `duplicate_message_ids`, `dropped_message_ids` and per-record
`rejected_records` (index, message_id, error details).

### `GET /api/v1/conjunctions/active`
"Active" = `event_time_utc` **strictly after** the server's current UTC time. Ordered by
`(event_time_utc, id)` so pagination is stable.

| Param | Meaning |
|---|---|
| `min_miss_distance_km` | `miss_distance_km >= value` |
| `norad_id` | object is primary **or** secondary |
| `has_collision_prob` | `true` → probability present; `false` → probability is `NULL` |
| `limit` (1–500, default 50), `offset` (≥0) | pagination; response includes `total` |

### `GET /healthz`
`200 {"status":"ok","database":"up","worker":{…}}`, or `503` with `"status":"degraded","database":"down"`
if the DB is unreachable. Also reports whether the worker is running and the outcome of its last tick.

## 5. Concurrency: background worker vs. REST API

Goal: ingestion work must never make `/healthz` or `/active` slow (<100 ms).

* **Different threads, never the event loop.** The worker is an APScheduler `BackgroundScheduler` job
  on its own dedicated thread. All route handlers are plain `def`, which FastAPI runs in its thread
  pool, so even blocking DB calls don't touch the asyncio loop. Nothing heavy ever runs on the loop.
* **Separate connection pools.** The API and the worker use different SQLAlchemy engines. A worker
  burst cannot exhaust the connections the API needs (API pool: 10+10; worker pool: 2).
* **Bounded work per request.** `limit ≤ 500`, a 5 s `statement_timeout` on API connections,
  `pool_timeout`/`connect_timeout`, and short transactions (one bulk `INSERT … ON CONFLICT` per batch).
  Postgres MVCC means readers never wait on the worker's inserts.
* **Single-flight ingestion.** `max_instances=1` + `coalesce=True` stop ticks overlapping or
  piling up; a Postgres **advisory lock** (`pg_try_advisory_xact_lock`) extends that guarantee
  across processes/replicas — a second runner simply skips the tick.
* **Failure isolation.** `run_once()` catches everything; a bad tick is logged and the schedule continues.
* **Proof.** `test_api_stays_under_100ms_while_a_slow_batch_is_being_processed` holds the worker in a
  2 s "heavy" step (`WORKER_SIMULATED_LATENCY_SECONDS`) and asserts every `/healthz` and `/active` call
  stays < 100 ms. On a real Uvicorn process (200 requests per endpoint, worker busy 75 % of the time)
  I measured: `/healthz` median 1.4 ms, p99 2.1 ms, max 11 ms; `/active` median 2.6 ms, p99 3.3 ms, max 3.7 ms.

**Trade-offs / honest limits.** The mock worker is I/O-bound, so threads are the right tool (the GIL is
released during DB I/O). Genuinely CPU-bound screening (orbital propagation, Pc computation) should
move to a process pool or Celery workers — the `ingest_raw_records()` seam makes that a small change.
Uvicorn runs **one** process per container because the scheduler lives in-process; scale horizontally with
more containers (the advisory lock keeps ingestion single-flight), or set `WORKER_ENABLED=false` on API
replicas and run a dedicated worker container.

## 6. Design decisions worth a second look

* **`message_id` conflicts → first write wins** (`DO NOTHING`), reported as `duplicates`. If STN needs
  CDM *updates* to supersede earlier versions, switch to `DO UPDATE` keyed on `message_id` (or add a version).
* **Strict ints/floats**: `"450.2"` (a string) is rejected rather than coerced — malformed feeds should
  fail loudly at the boundary.
* **No authentication** by design, per the assignment (the Express edge gateway owns it).
* `min_miss_distance_km` is implemented literally (`>=`), as specified.

## 7. Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg2://stn:stn@localhost:5432/stn` | set by compose for the container |
| `WORKER_ENABLED` | `true` | run the background worker in this process |
| `WORKER_INTERVAL_SECONDS` | `30` | tick period |
| `WORKER_BATCH_MIN` / `WORKER_BATCH_MAX` | `5` / `10` | mock batch size |
| `WORKER_SIMULATED_LATENCY_SECONDS` | `0` | load-test knob; simulates heavy work in the worker |
| `MAX_INGEST_BATCH_SIZE` | `1000` | request batch cap |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` / `WORKER_DB_POOL_SIZE` | `10` / `10` / `2` | pooling |
| `LOG_LEVEL` | `INFO` | |

Manual migration commands (inside the container or a venv): `alembic upgrade head`, `alembic downgrade base`,
`alembic check`.
# stn_repo
