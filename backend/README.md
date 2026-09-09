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

Create an administrator with `create-user --admin`, or promote an existing
account explicitly:

```bash
python backend/manage.py set-role client@example.com admin
```

Job progress and completed artifact metadata are persisted in PostgreSQL. Redis
is reserved for queue coordination and transient worker signals; it is not the
source of truth for a job. Audio, images, transcripts and videos are never
stored in Redis or PostgreSQL.

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
