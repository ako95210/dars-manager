FROM node:20-alpine AS frontend-build

WORKDIR /web

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend ./
RUN npm run build


FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DARSM_TEMP_ROOT=/workspaces \
    TMPDIR=/workspaces \
    HF_HOME=/models \
    TOKENIZERS_PARALLELISM=false

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt backend/requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt -r backend/requirements.txt

COPY drsm_core.py ./drsm_core.py
COPY backend ./backend
COPY --from=frontend-build /web/dist ./frontend/dist

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
