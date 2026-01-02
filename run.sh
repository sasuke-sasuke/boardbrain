#!/bin/bash
cd "$(dirname "$0")"
source .venv/bin/activate
export PYTHONPATH="$(pwd)"
streamlit run app/app.py
