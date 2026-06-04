#!/bin/bash
# Railway start script: runs worker (scheduler) + Streamlit dashboard
python -u worker.py &
streamlit run app.py --server.port $PORT --server.address 0.0.0.0
