#!/bin/bash
cd "$(dirname "$0")"
source .venv/bin/activate
export PYTHONPATH="$(pwd)"
export KB_RAW_DIR="$(pwd)/kb_raw"
python -m boardbrain.ingest
streamlit run app/app.py
