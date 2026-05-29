#!/bin/bash
# release.sh — runs on every Heroku deploy (release phase)
# Always: apply DB migrations
# Demo only: auto-populate enterprise demo data

set -e

echo "→ Running DB setup..."
cd backend
python -c "
from database.db import engine
from database import models
models.Base.metadata.create_all(bind=engine)
print('   DB tables ready')

# Column migrations — safe to run on every deploy (IF NOT EXISTS)
from sqlalchemy import text
with engine.connect() as conn:
    conn.execute(text('''
        ALTER TABLE registered_agents
        ADD COLUMN IF NOT EXISTS pruning_enabled BOOLEAN DEFAULT TRUE
    '''))
    conn.commit()
print('   Column migrations applied')
"

if [ "$DEMO_MODE" = "true" ]; then
  echo "→ DEMO_MODE=true — loading Meridian Financial enterprise data..."
  python database/populate_enterprise.py
  echo "→ Enterprise demo data loaded."
fi

echo "→ Release phase complete."
