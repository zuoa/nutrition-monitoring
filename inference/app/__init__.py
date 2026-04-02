import logging

from pythonjsonlogger import jsonlogger


def configure_logging(app) -> None:
    handler = logging.StreamHandler()
    formatter = jsonlogger.JsonFormatter(
        "%(asctime)s %(name)s %(levelname)s %(message)s"
    )
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.setLevel(app.config.get("LOG_LEVEL", "INFO"))
    root.handlers = [handler]
