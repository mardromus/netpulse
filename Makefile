PY ?= python3

.PHONY: install dev test lint format demo serve clean

install:
	$(PY) -m pip install -e .

dev:
	$(PY) -m pip install -e ".[dev]"

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check src tests

format:
	$(PY) -m ruff format src tests
	$(PY) -m ruff check --fix src tests

demo:
	$(PY) -m netpulse demo --out out

serve:
	$(PY) -m netpulse serve out/demo.db

clean:
	rm -rf out build dist .pytest_cache .ruff_cache src/*.egg-info
