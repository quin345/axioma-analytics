"""Entry point for the Axioma Analytics dashboard (the Flask app in webapp/).

The systemd unit runs:

    venv/bin/python3 run.py --host 127.0.0.1 --port 8000

The real application lives in ``webapp/app.py``. This file only puts that
folder on ``sys.path`` (so ``webapp``'s flat imports like ``import analytics``
resolve), loads the Flask app object, and serves it on the given host/port.

Kept as a thin launcher so the deployment unit does not need to know the
webapp layout, and so the app can also be started by hand:

    venv/bin/python3 run.py --host 127.0.0.1 --port 8000
"""
import argparse
import os
import sys

WEBAPP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp")
sys.path.insert(0, WEBAPP_DIR)

from app import app  # noqa: E402  (webapp/app.py, after sys.path setup)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1",
                        help="interface to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000,
                        help="port to bind (default: 8000)")
    parser.add_argument("--debug", action="store_true",
                        help="run the Flask dev server with the reloader")
    args = parser.parse_args(argv)

    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
