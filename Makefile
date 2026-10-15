VENV ?= venv
BIN  := $(VENV)/bin
PROVIDER ?= gemini

.PHONY: install test lint format typecheck check run eval

install:  ## instala o projeto com extras de dev e Gemini
	$(BIN)/pip install -e ".[dev,gemini]"

test:  ## testes offline (sem chave), com cobertura
	$(BIN)/pytest

lint:
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .

format:
	$(BIN)/ruff check --fix .
	$(BIN)/ruff format .

typecheck:
	$(BIN)/mypy

check: lint typecheck test  ## tudo que precisa estar verde antes de um commit

run:  ## API em http://localhost:8000/docs
	$(BIN)/uvicorn main:app --reload

eval:  ## evals contra o modelo real (gasta quota): make eval PROVIDER=gemini
	$(BIN)/pytest -m live --no-cov --provider $(PROVIDER)
