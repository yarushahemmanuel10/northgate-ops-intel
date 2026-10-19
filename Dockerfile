# Northgate prototype images (step 6.5, D-046). Two targets from one base:
#   pipeline   data generator, mock shop API and the pipeline (writes the data volumes)
#   dashboard  Streamlit dashboard only: no generator code, no generator config, no shop API, no tests
# Base image pinned by its multi-architecture index digest (includes linux/amd64 and linux/arm64),
# python:3.12-slim-bookworm as published on Docker Hub, last updated 2026-10-06.
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258

FROM ${PYTHON_IMAGE} AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    PATH=/opt/venv/bin:$PATH
RUN pip install --no-cache-dir uv==0.12.13
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev && rm -rf /root/.cache && chmod -R a+rX,go-w /opt/venv
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app
# The app user has no home folder; point HOME at /tmp so any library that keeps settings or a cache there can
# still write (precaution, D-046). Everything the project itself writes goes under /app/data.
ENV HOME=/tmp

FROM base AS pipeline
COPY generator/ generator/
COPY shop_api/ shop_api/
COPY pipeline/ pipeline/
COPY config/ config/
COPY semantic/ semantic/
COPY knowledge_base/ knowledge_base/
# COPY keeps the host's file modes (owner-only 600 on the author's Mac), so make every copied file readable and
# every folder enterable by all users, and writable by root only (D-046). Then the empty mount points, owned by the
# app user so new named volumes start with that owner: the data folders are the only place it can write.
RUN chmod -R a+rX,go-w /app \
 && mkdir -p data/warehouse data/alerts data/audit && chown -R app:app /app/data
USER app
CMD ["python", "-m", "pipeline.run"]

FROM base AS dashboard
COPY pipeline/ pipeline/
COPY dashboard/ dashboard/
COPY .streamlit/ .streamlit/
COPY semantic/ semantic/
# Only the settings the dashboard reads; config/generator.yaml is deliberately not copied.
COPY config/pipeline.yaml config/warehouse.yaml config/roles.yaml config/audit.yaml config/assistant.yaml config/kpi_thresholds.yaml config/
# Readable by all, writable by root only; the data folders are the only place the app user can write (D-046).
RUN chmod -R a+rX,go-w /app \
 && mkdir -p data/warehouse data/alerts data/audit && chown -R app:app /app/data
USER app
EXPOSE 8501
# Listens on all interfaces inside the container; compose publishes it on the host's 127.0.0.1 only.
CMD ["streamlit", "run", "dashboard/app.py", "--server.address=0.0.0.0", "--server.port=8501"]
