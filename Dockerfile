FROM ghcr.io/astral-sh/uv:0.13.6 AS uv

FROM node:22-slim AS web
WORKDIR /w/src/aml_investigator/web/frontend
COPY src/aml_investigator/web/frontend/package.json src/aml_investigator/web/frontend/package-lock.json ./
RUN npm ci
COPY src/aml_investigator/web/static/workspace /w/src/aml_investigator/web/static/workspace
COPY src/aml_investigator/web/frontend ./
RUN npm run build

FROM python:3.12-slim AS build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
COPY --from=web /w/src/aml_investigator/web/static/world ./src/aml_investigator/web/static/world
RUN uv sync --locked --no-dev --no-editable

FROM python:3.12-slim
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin aml
WORKDIR /app
COPY --from=build /app/.venv ./.venv
COPY config/thresholds ./config/thresholds
COPY config/simulation ./config/simulation
COPY eval/results ./eval/results
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
USER 10001
ENTRYPOINT ["aml"]
