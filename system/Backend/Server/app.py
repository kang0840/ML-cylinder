"""Flask application factory for the Backend API."""

from flask import Flask

from system.Backend.API.cylinder_result import cylinder_result_blueprint


def create_app() -> Flask:
    """Create the Flask app and register its API routes."""
    app = Flask(__name__)
    app.register_blueprint(cylinder_result_blueprint)
    return app
