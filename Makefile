.PHONY: install lint format format-check typecheck test cov ci clean help

# Use the project venv automatically when present; fall back to bare python
# (e.g. in CI, where deps are pip-installed into the job's interpreter).
VENV := .venv
PY := $(shell [ -x $(VENV)/bin/python ] && echo $(VENV)/bin/python || echo python)

# Minimum total coverage. Ratchet up as tests are backfilled (see
# docs/REMEDIATION_PLAN.md T13). Baseline at introduction: 38%.
# T13 backfill (config, nextgen canonicalize/providers/embeddings, storage
# vectors, ui) raised the total to ~45%; floor ratcheted to 42%.
COV_MIN := 42

help:
	@echo "Targets: install lint format format-check typecheck test cov ci clean"

install:  ## Install the package + dev tooling (uses uv) and pre-commit hooks
	uv pip install -e '.[dev]'
	$(PY) -m pre_commit install

lint:  ## Lint with ruff
	$(PY) -m ruff check src tests

format:  ## Auto-format with ruff
	$(PY) -m ruff format src tests

format-check:  ## Verify formatting without writing
	$(PY) -m ruff format --check src tests

typecheck:  ## Type-check with mypy
	$(PY) -m mypy src

test:  ## Run the test suite
	$(PY) -m pytest

cov:  ## Run tests with coverage gate
	$(PY) -m pytest --cov=src --cov-report=term-missing --cov-fail-under=$(COV_MIN)

ci: lint format-check typecheck cov  ## Everything CI runs

clean:  ## Remove caches and build artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov build dist *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
