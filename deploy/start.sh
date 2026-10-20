#!/bin/sh
# Single-container mode (Hugging Face Spaces): the API serves the review UI at / on ${PORT:-7860}.
set -e
exec uvicorn docex.api.app:app --host 0.0.0.0 --port "${PORT:-7860}" --log-level warning
