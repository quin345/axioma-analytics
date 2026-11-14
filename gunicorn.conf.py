"""Production server settings for the AXIOMA Flask app.

Used by the axioma systemd unit:

    venv/bin/gunicorn -c gunicorn.conf.py

nginx terminates TLS and proxies to 127.0.0.1:8000. run.py remains the
lightweight launcher for local development (Flask's built-in server).
"""
import os

# webapp/app.py imports its siblings (analytics, db, ...) as top-level modules.
pythonpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp")
wsgi_app = "app:app"

bind = os.environ.get("GUNICORN_BIND", "127.0.0.1:8000")

# Requests mostly wait on Kusto, so a few threads per worker go further than
# extra processes on this 2-vCPU VM. The app keeps no in-process state between
# requests (SQLite in WAL mode, one connection per call), so workers are safe.
workers = int(os.environ.get("GUNICORN_WORKERS", "2"))
worker_class = "gthread"
threads = int(os.environ.get("GUNICORN_THREADS", "4"))

# Just above nginx's 120s proxy_read_timeout, so nginx gives up first.
timeout = 130
graceful_timeout = 30
keepalive = 5

# Recycle workers now and then to cap any slow memory growth.
max_requests = 1000
max_requests_jitter = 100

# Logs go to stdout/stderr, which journald collects.
accesslog = "-"
errorlog = "-"
loglevel = "info"
# The real client address comes from nginx's X-Forwarded-For.
access_log_format = '%({x-forwarded-for}i)s "%(r)s" %(s)s %(b)s %(M)sms'
forwarded_allow_ips = "127.0.0.1"
