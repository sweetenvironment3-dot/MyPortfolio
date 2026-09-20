FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FLASK_DEBUG=0

WORKDIR /app

COPY requirements.txt .
# gunicorn is installed explicitly as well, so the container can never start
# without it (a missing gunicorn is what causes "exit code 127").
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY . .

EXPOSE 3000

# - ${PORT:-3000}: use the port the platform gives us, else 3000.
# - -w 1 --threads 8: one worker with threads is plenty for this site and keeps
#   the SQLite database and the rate limiter simple.
# - --worker-tmp-dir /dev/shm: avoids gunicorn heartbeat hangs on container filesystems.
# - --capture-output: print()s and tracebacks show up on the platform's Logs page.
CMD ["sh", "-c", "exec python -m gunicorn app:app --bind 0.0.0.0:${PORT:-3000} -w 1 --threads 8 --timeout 60 --worker-tmp-dir /dev/shm --capture-output --log-level info --access-logfile - --error-logfile -"]
