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

# Keyword upserts — add new output-demand keywords if not already present
import json
from core.routing_config import get_routing_config
from database.db import SessionLocal
db = SessionLocal()
cfg = get_routing_config(db)
kws = set(cfg.complexity_keywords)
new_kws = ['calculate', 'draft', 'generate', 'explain', 'predict', 'prioritize']
added = [kw for kw in new_kws if kw not in kws]
if added:
    cfg.complexity_keywords = list(kws) + added
    db.commit()
    print(f'   Keywords added: {added}')
else:
    print('   Keywords already up to date')
db.close()
"

if [ "$DEMO_MODE" = "true" ]; then
  echo "→ DEMO_MODE=true — loading Meridian Financial enterprise data..."
  python database/populate_enterprise.py
  echo "→ Enterprise demo data loaded."
fi

echo "→ Release phase complete."
