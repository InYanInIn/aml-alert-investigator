FROM ghcr.io/astral-sh/uv:0.13.6 AS uv

FROM python:3.12-slim AS build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.12-slim
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin aml
WORKDIR /app
COPY --from=build /app/.venv ./.venv
COPY config/thresholds ./config/thresholds
COPY eval/results ./eval/results
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
USER 10001
ENTRYPOINT ["aml"]
