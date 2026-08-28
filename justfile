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
