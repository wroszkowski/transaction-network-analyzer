set dotenv-load := true


default: all-hooks

init:
    uv venv --clear && \
    source .venv/bin/activate && \
    uv sync --dev && \
    pre-commit install && \
    just all-hooks

all-hooks skip_test="" *args="--all-files":
    {{ skip_test }} uv run pre-commit run {{ args }}

hook-ruff-check path="src/ tests/" args="--fix":
    uv run ruff check {{ path }} {{ args }}

hook-ruff-format path="src/ tests/" args="":
    uv run ruff format {{ path }} {{ args }}

hook-ty path="src/ tests/" args="":
    uv run ty check {{ path }} {{ args }}

hook-unit-test path="tests/" args="":
    uv run pytest {{ path }} {{ args }}

# --- Transaction Network Analyzer ---

# Generate the demo dataset, analyse it, and write the report into public/
demo seed="42":
    uv run python -m tna.cli demo --seed {{ seed }}

# Write a synthetic labelled dataset to data/
generate seed="42" out="data":
    uv run python -m tna.cli generate --seed {{ seed }} --out {{ out }}

# Analyse any ledger with the same columns
analyze input="data/transactions.csv" out="public":
    uv run python -m tna.cli analyze --input {{ input }} --out {{ out }}

# Publish public/ to the production URL
deploy:
    vercel --prod --yes --cwd public

# Prove the deployed URL is reachable by an anonymous fetcher
verify url="https://transaction-network-analyzer.vercel.app":
    curl -sSI {{ url }}/ | head -1
    curl -sS {{ url }}/ | grep -c "Ranked" || echo "WARNING: findings table not found in fetched HTML"
