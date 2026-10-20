# One image for API, worker and UI. On Hugging Face Spaces it runs API + UI together (start.sh).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    OMP_THREAD_LIMIT=1 DOCEX_ROOT=/app PYTHONPATH=/app/src:/app

RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-ind tesseract-ocr-eng curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[api,ui]"
COPY configs ./configs
COPY prompts ./prompts
COPY ui ./ui
COPY .streamlit ./.streamlit
COPY eval/reports/summary.json ./eval/reports/summary.json
COPY demo_samples ./demo_samples
COPY deploy/start.sh ./start.sh

RUN useradd -m -u 1000 app && mkdir -p /app/data && chown -R app /app
USER app
ENV DOCEX_DATA_DIR=/app/data

EXPOSE 7860 8000
HEALTHCHECK CMD curl -fs http://localhost:8000/health || exit 1
CMD ["./start.sh"]
