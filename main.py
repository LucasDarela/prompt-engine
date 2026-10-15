"""Entrypoint ASGI: `uvicorn main:app --reload`."""

from prompt_engine.api.app import create_app

app = create_app()
