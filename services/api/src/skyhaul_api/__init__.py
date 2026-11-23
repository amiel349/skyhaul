"""Skyhaul fleet API."""

from flask import Flask


def create_app() -> Flask:
    """Application factory. Blueprints, config and extensions are added in M2."""
    app = Flask(__name__)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
