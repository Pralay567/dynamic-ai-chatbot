"""WSGI entry point for production-style servers.

Examples (install the server separately; it is not in requirements.txt):
    Windows : waitress-serve --listen=127.0.0.1:5000 wsgi:app
    Linux   : gunicorn wsgi:app

Configuration is read from environment variables / .env exactly as for
``python run.py``.
"""

from server import create_app

app = create_app()
