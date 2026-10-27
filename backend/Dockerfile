FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app
RUN pip install --no-cache-dir uv==0.8.15
RUN groupadd --gid 1000 app && useradd --uid 1000 --gid app --create-home app
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend ./backend
COPY alembic.ini ./alembic.ini
RUN uv sync --frozen --no-dev
RUN mkdir -p /data/artifacts /data/browser-profile && chown -R app:app /data
USER app
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
