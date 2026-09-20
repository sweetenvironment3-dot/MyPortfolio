# MyPortfolio - production image
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=3000 \
    FLASK_DEBUG=0 \
    DATABASE_PATH=/data/portfolio.db

WORKDIR /app

# Install dependencies first so rebuilds are fast when only code changes.
COPY requirements.txt .
RUN pip install -r requirements.txt

# App code (see .dockerignore for what is left out).
COPY . .

# The app runs as a normal user, not root. /data holds the SQLite database:
# mount a persistent volume there or comments will reset on every redeploy.
RUN useradd --system --uid 10001 --no-create-home appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /app /data

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','3000'), timeout=4)" || exit 1

# "sh" is used on purpose so the script works even if the zip lost its execute bit.
ENTRYPOINT ["sh", "/app/docker-entrypoint.sh"]
CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:app"]
