# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app
COPY docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh && useradd --system --uid 10001 stn && chown -R stn /srv

# ---- test image: adds pytest + the tests (docker compose --profile test run --rm tests)
FROM base AS test
COPY requirements-dev.txt pytest.ini ./
RUN pip install -r requirements-dev.txt
COPY tests ./tests
USER stn
CMD ["pytest", "-v"]

# ---- runtime image (default / last stage)
FROM base AS runtime
USER stn
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status==200 else 1)"
ENTRYPOINT ["./docker-entrypoint.sh"]
