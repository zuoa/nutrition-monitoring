from flask import Flask

from app import _configure_logging
from config import get_config


def create_inference_app(config_class=None):
    app = Flask(__name__)
    if config_class is None:
        config_class = get_config()
    app.config.from_object(config_class)
    _configure_logging(app)

    role = str(app.config.get("INFERENCE_SERVICE_ROLE", "all") or "all").strip().lower()
    if role in {"all", "detector"}:
        from app.inference_api.detector import bp as detector_bp

        app.register_blueprint(detector_bp)
    if role in {"all", "retrieval"}:
        from app.inference_api.retrieval import bp as retrieval_bp

        app.register_blueprint(retrieval_bp)

    @app.route("/health")
    def health():
        return {"status": "ok", "service": f"inference-{role}"}

    return app
