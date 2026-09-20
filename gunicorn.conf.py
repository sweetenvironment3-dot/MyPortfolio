"""Gunicorn settings for production. Every value can be overridden with an
environment variable, so you can tune it from the host's dashboard."""
import os

# The host sends traffic to this port (3000 by default).
bind = f"0.0.0.0:{os.environ.get('PORT', '3000')}"

# SQLite + a small portfolio site: a couple of workers with threads is plenty.
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
worker_class = "gthread"

timeout = 60
graceful_timeout = 30
keepalive = 5
max_requests = 2000          # recycle workers now and then to keep memory tidy
max_requests_jitter = 200
preload_app = True

# Docker: keep worker heartbeat files in memory instead of the container disk.
worker_tmp_dir = "/dev/shm"

# Log to stdout/stderr so the host's log viewer shows everything.
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info")
