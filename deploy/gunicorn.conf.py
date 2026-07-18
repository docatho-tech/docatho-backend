"""Gunicorn configuration for the docatho backend.

Referenced by deploy/systemd/docatho-gunicorn.service via `-c`.
Binds a unix socket that nginx proxies to.
"""

import multiprocessing

# Bind to a unix socket in the systemd RuntimeDirectory (/run/docatho).
bind = "unix:/run/docatho/gunicorn.sock"
# Make the socket group-accessible so nginx (group www-data) can reach it.
umask = 0o007

# Worker processes. Override with GUNICORN_WORKERS if the instance is small.
workers = int(multiprocessing.cpu_count() * 2 + 1)
worker_class = "sync"

# Timeouts / recycling.
timeout = 60
graceful_timeout = 30
keepalive = 5
max_requests = 1000
max_requests_jitter = 100

# Log to stdout/stderr so journald captures it.
accesslog = "-"
errorlog = "-"
loglevel = "info"

# Trust the X-Forwarded-* headers set by nginx (SECURE_PROXY_SSL_HEADER).
forwarded_allow_ips = "127.0.0.1"
