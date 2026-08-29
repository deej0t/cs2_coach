"""WSGI entrypoint for gunicorn / production servers."""
from cs2_coach.web.app import create_app

app = create_app()
