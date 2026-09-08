# Phase 1 backend prototype

This prototype validates the temporary upload, Whisper, audio export, cover and
static-video pipeline before the production UI is built.

The default workspace is `/dev/shm/dars-manager-beta`. It must remain temporary
and must not be backed up.

## Database migrations

The API applies Alembic migrations automatically at startup. They can also be
run explicitly before an administrative operation:

```bash
python backend/manage.py migrate
```

When `DARSM_REDIS_URL` is configured, job progress and artifact metadata are
kept in Redis with the same TTL as the temporary workspace. Without that
variable, the local development fallback keeps this state in process memory.
Audio, images, transcripts and videos are never stored in Redis.

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
