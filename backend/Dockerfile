FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.19 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY app ./app
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8000 LITELLM_LOCAL_MODEL_COST_MAP=True
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app \
    && mkdir /data && chown app:app /data
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH"
COPY --from=builder --chown=app:app /app/.venv ./.venv
COPY --chown=app:app app ./app
USER app
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --no-access-log"]
