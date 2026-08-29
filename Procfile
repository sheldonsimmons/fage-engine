release: bash scripts/release.sh
web: cd backend && uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1
worker: cd backend && python scripts/run_cdc_subscriber.py
