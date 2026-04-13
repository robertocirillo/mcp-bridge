UV = uv

.PHONY: default install setup clean rebuild run test lint format type-check

default: install

install:
	$(UV) sync --dev

setup: install

clean:
	rm -rf .venv

rebuild: clean install

run:
	$(UV) run uvicorn main:app --reload

test:
	$(UV) run pytest -q

lint:
	$(UV) run flake8 .

format:
	$(UV) run black .

type-check:
	$(UV) run mypy .
