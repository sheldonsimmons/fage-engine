#!/bin/bash
# release.sh — runs on every Heroku deploy (release phase)
# Always: apply DB migrations + seed known_models if empty
# Demo only: auto-populate enterprise demo data

set -e

echo "→ Running DB setup..."
cd backend
python -c "
from database.migrate import create_tables, run_migrations
create_tables()
print('   DB tables ready')

# Same migration function main.py runs on every dyno boot — kept as a single
# source of truth so release-phase and startup-phase migrations can't drift
# out of sync with each other (see database/migrate.py).
run_migrations()
print('   Column migrations applied')

# Table migrations — create new tables if not present
from database.models import KnownModel
from database.db import Base, engine as _engine
KnownModel.__table__.create(bind=_engine, checkfirst=True)
print('   known_models table ready')

# Known models seed — populate Quick Select dropdown if table is empty
from database.models import KnownModel
from database.db import SessionLocal
db2 = SessionLocal()
if db2.query(KnownModel).count() == 0:
    KNOWN_MODELS = [
        # Anthropic
        dict(display_name='Claude Opus 4.8',      model_id='claude-opus-4-8',           provider='Anthropic',  provider_group='Anthropic — Claude 4.x',  tier=4, cost_input_per_1m=15.00, cost_output_per_1m=75.00),
        dict(display_name='Claude Opus 4.6',      model_id='claude-opus-4-6',           provider='Anthropic',  provider_group='Anthropic — Claude 4.x',  tier=4, cost_input_per_1m=15.00, cost_output_per_1m=75.00),
        dict(display_name='Claude Sonnet 4.6',    model_id='claude-sonnet-4-6',         provider='Anthropic',  provider_group='Anthropic — Claude 4.x',  tier=2, cost_input_per_1m=3.00,  cost_output_per_1m=15.00),
        dict(display_name='Claude Haiku 4.5',     model_id='claude-haiku-4-5-20251001', provider='Anthropic',  provider_group='Anthropic — Claude 4.x',  tier=1, cost_input_per_1m=0.25,  cost_output_per_1m=1.25),
        # OpenAI
        dict(display_name='GPT-4o',               model_id='gpt-4o',                    provider='OpenAI',     provider_group='OpenAI — GPT-4o',         tier=3, cost_input_per_1m=5.00,  cost_output_per_1m=15.00),
        dict(display_name='GPT-4o mini',          model_id='gpt-4o-mini',               provider='OpenAI',     provider_group='OpenAI — GPT-4o',         tier=1, cost_input_per_1m=0.15,  cost_output_per_1m=0.60),
        dict(display_name='GPT-4.1',              model_id='gpt-4.1',                   provider='OpenAI',     provider_group='OpenAI — GPT-4.1',        tier=3, cost_input_per_1m=2.00,  cost_output_per_1m=8.00),
        dict(display_name='GPT-4.1 mini',         model_id='gpt-4.1-mini',              provider='OpenAI',     provider_group='OpenAI — GPT-4.1',        tier=1, cost_input_per_1m=0.40,  cost_output_per_1m=1.60),
        dict(display_name='o3',                   model_id='o3',                        provider='OpenAI',     provider_group='OpenAI — o-series',       tier=4, cost_input_per_1m=10.00, cost_output_per_1m=40.00),
        dict(display_name='o3 mini',              model_id='o3-mini',                   provider='OpenAI',     provider_group='OpenAI — o-series',       tier=2, cost_input_per_1m=1.10,  cost_output_per_1m=4.40),
        dict(display_name='o4 mini',              model_id='o4-mini',                   provider='OpenAI',     provider_group='OpenAI — o-series',       tier=2, cost_input_per_1m=1.10,  cost_output_per_1m=4.40),
        # Google
        dict(display_name='Gemini 2.5 Pro',       model_id='gemini-2.5-pro',            provider='Google',     provider_group='Google — Gemini 2.x',     tier=4, cost_input_per_1m=1.25,  cost_output_per_1m=10.00),
        dict(display_name='Gemini 2.5 Flash',     model_id='gemini-2.5-flash',          provider='Google',     provider_group='Google — Gemini 2.x',     tier=2, cost_input_per_1m=0.15,  cost_output_per_1m=0.60),
        dict(display_name='Gemini 2.0 Flash',     model_id='gemini-2.0-flash',          provider='Google',     provider_group='Google — Gemini 2.x',     tier=1, cost_input_per_1m=0.10,  cost_output_per_1m=0.40),
        # Mistral
        dict(display_name='Mistral Large',        model_id='mistral-large-latest',      provider='Mistral',    provider_group='Mistral',                 tier=3, cost_input_per_1m=2.00,  cost_output_per_1m=6.00),
        dict(display_name='Mistral Small',        model_id='mistral-small-latest',      provider='Mistral',    provider_group='Mistral',                 tier=1, cost_input_per_1m=0.10,  cost_output_per_1m=0.30),
        dict(display_name='Codestral',            model_id='codestral-latest',          provider='Mistral',    provider_group='Mistral',                 tier=2, cost_input_per_1m=0.30,  cost_output_per_1m=0.90),
        # Azure OpenAI
        dict(display_name='Azure GPT-4o',         model_id='azure-gpt-4o',              provider='Azure OpenAI', provider_group='Azure OpenAI',          tier=3, cost_input_per_1m=5.00,  cost_output_per_1m=15.00),
        dict(display_name='Azure GPT-4o mini',    model_id='azure-gpt-4o-mini',         provider='Azure OpenAI', provider_group='Azure OpenAI',          tier=1, cost_input_per_1m=0.15,  cost_output_per_1m=0.60),
    ]
    for km in KNOWN_MODELS:
        db2.add(KnownModel(**km))
    db2.commit()
    print(f'   Known models seeded: {len(KNOWN_MODELS)} presets loaded')
else:
    print(f'   Known models already seeded ({db2.query(KnownModel).count()} entries)')
db2.close()
"

if [ "$DEMO_MODE" = "true" ]; then
  echo "→ DEMO_MODE=true — loading Meridian Financial enterprise data..."
  python database/populate_enterprise.py
  echo "→ Enterprise demo data loaded."
fi

echo "→ Release phase complete."
