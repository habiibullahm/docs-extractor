# One image for the API (which also serves the review UI from web/) and the RQ worker.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    OMP_THREAD_LIMIT=1 DOCEX_ROOT=/app PYTHONPATH=/app/src:/app

RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-ind tesseract-ocr-eng curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[api]"
COPY configs ./configs
COPY prompts ./prompts
COPY web ./web
COPY eval/reports/summary.json ./eval/reports/summary.json
COPY demo_samples ./demo_samples
COPY deploy/start.sh ./start.sh

RUN useradd -m -u 1000 app && mkdir -p /app/data && chown -R app /app
USER app
ENV DOCEX_DATA_DIR=/app/data

EXPOSE 7860
HEALTHCHECK CMD curl -fs http://localhost:${PORT:-7860}/health || exit 1
CMD ["./start.sh"]
