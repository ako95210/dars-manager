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

Every source and generated artifact receives a SHA-256 digest. The worker
recomputes the source digest after download and rejects an object that changed
after a previous verification.

Run periodic metering and deletion independently from the API with:

```bash
python -m backend.maintenance
```

The service records cumulative storage in millionths of a decimal GB-month,
then purges expired objects. Set `DARSM_STORAGE_PROVIDER`,
`DARSM_STORAGE_MODEL`, `DARSM_STORAGE_GB_MONTH_USD` and
`DARSM_STORAGE_PRICE_SOURCE_URL` from the selected provider's invoice. An S3
deployment refuses to start without an explicit GB-month rate; request charges
will be added separately when the final provider is selected.

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

## Cloud transcription

The beta compose file uses `DARSM_TRANSCRIPTION_BACKEND=openai` and
`whisper-1`. Only the worker receives `OPENAI_API_KEY`; the browser and API
never expose it. The API quotes the audio duration before launch, then each
encoded fragment is recorded as confirmed usage with its provider request ID.
The initial estimate remains in the ledger with the `reconciled` status.
Paid transcription responses are checkpointed in temporary object storage, so
a worker retry can continue through rendering without paying for the same
fragment again.

The worker then sends only the timestamped transcript to the configured
semantic analyzer (`DARSM_SEMANTIC_ANALYSIS_BACKEND=openai`). The structured
response must cover every transcript segment exactly once and is converted into
content-based subchapters, titles and descriptions. Its token usage is quoted,
confirmed and reconciled separately from transcription. Existing completed
courses can be reanalyzed from their stored transcript without retranscribing
the audio.

Long inputs are converted to mono 16 kHz WAV and split into nine-minute
fragments by default. Tune `DARSM_TRANSCRIPTION_CHUNK_SECONDS` and
`DARSM_TRANSCRIPTION_CHUNK_MAX_BYTES` if the provider contract changes. Local
development keeps `DARSM_TRANSCRIPTION_BACKEND=local` and can select
`DARSM_LOCAL_WHISPER_MODEL=base` without an API key.

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
