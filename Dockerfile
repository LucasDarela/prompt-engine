# API do Prompt Engine. Build: docker compose build · Run: docker compose up
# Python 3.14: a versão em que a suíte de testes roda (pyproject aceita >= 3.12).
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    LOG_FORMAT=json

WORKDIR /app

# Dependências primeiro (camada cacheável); o pacote leva os prompts YAML e as parciais Jinja.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[gemini]"

COPY main.py ./
COPY data ./data

# Usuário sem privilégios; var/ guarda os traces (SQLite) e pode ser um volume.
RUN useradd --create-home --uid 10001 app && mkdir -p var && chown app:app var
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)"

# --no-access-log: o middleware já registra request.completed em JSON, com request_id.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
