FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DRSM_WORK_DIR=/data \
    DRSM_CLOUD_SAFE_DEFAULT=false \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    HF_HOME=/data/hf_cache \
    XDG_CACHE_HOME=/data/cache \
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    TOKENIZERS_PARALLELISM=false

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

COPY . .

RUN mkdir -p /data/uploads /data/analyses /data/exports /data/hf_cache /data/cache

EXPOSE 8501

CMD ["sh", "-c", "streamlit run drsm_streamlit.py --server.address=0.0.0.0 --server.port=${PORT:-8501} --server.headless=true --browser.gatherUsageStats=false"]
