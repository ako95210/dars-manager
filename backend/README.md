# Phase 1 backend prototype

This prototype validates the temporary upload, Whisper, audio export, cover and
static-video pipeline before the production UI is built.

The default workspace is `/dev/shm/dars-manager-beta`. It must remain temporary
and must not be backed up.

## Database migrations

The API applies Alembic migrations automatically at startup. They can also be
run explicitly before an administrative operation:

```bash
python -m backend.manage migrate
```

Create an administrator with `create-user --admin`, or promote an existing
account explicitly:

```bash
python -m backend.manage set-role client@example.com admin
```

Job progress and completed artifact metadata are persisted in PostgreSQL. Redis
is reserved for queue coordination and transient worker signals; it is not the
source of truth for a job. Audio, images, transcripts and videos are never
stored in Redis or PostgreSQL.

## Temporary media storage

Uploads use a storage contract independent from the processing API. In local
development, `DARSM_MEDIA_BACKEND=local` writes under `DARSM_MEDIA_ROOT`. In
production, set `DARSM_MEDIA_BACKEND=s3` and configure `DARSM_S3_BUCKET`,
`DARSM_S3_REGION` and standard AWS credentials. `DARSM_S3_ENDPOINT_URL` can
target another S3-compatible provider.

The browser first reserves an asset, uploads it with a short-lived target, then
asks the API to validate its exact size. Source media expires after seven days
by default (`DARSM_MEDIA_RETENTION_SECONDS=604800`). Configure the bucket with a
matching lifecycle rule and CORS permissions for browser `POST` requests.
Expired metadata is purged when the API starts; the bucket lifecycle remains the
independent safety net if the API is unavailable at the expiration time.

## Separate worker

Set `DARSM_EXECUTION_BACKEND=worker` on the API, then run the worker as a
separate process:

```bash
python -m backend.worker
```

PostgreSQL owns the durable queue state, worker lease, attempt count and
progress. Redis wakes workers quickly, while periodic database polling recovers
from a lost notification. Workers download source assets into their own
ephemeral workspace and upload every output through `MediaStorage`; they never
depend on the API filesystem.

## Run the API

```bash
python -m pip install -r backend/requirements.txt
TMPDIR=/dev/shm DARSM_TEMP_ROOT=/dev/shm/dars-manager-beta \
  uvicorn backend.app.main:app --reload
```

## Run the pipeline directly

```bash
python backend/run_spike.py path/to/audio.aac --cpu-threads 4
```

Use an existing analysis to validate the downstream stages without rerunning
Whisper:

```bash
python backend/run_spike.py path/to/audio.aac --analysis path/to/analysis.json
```

Artifacts are deleted at the end unless `--keep` is passed.
