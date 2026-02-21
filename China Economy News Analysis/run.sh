#!/bin/bash
streamlit run run_dashboard.py \
  --server.port=8503 \
  --server.address=127.0.0.1 \
  --server.headless=true
