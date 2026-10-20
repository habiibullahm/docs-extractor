#!/bin/sh
# Single-container mode (Hugging Face Spaces): API on :8000 (internal), Streamlit UI on :${PORT:-7860}.
set -e
uvicorn docex.api.app:app --host 0.0.0.0 --port 8000 --log-level warning &
exec streamlit run ui/app.py --server.port "${PORT:-7860}" --server.address 0.0.0.0 \
    --server.headless true --browser.gatherUsageStats false
