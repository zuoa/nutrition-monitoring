"""Public vendor callbacks: protocol signature authentication, no user JWT."""

from flask import Blueprint, current_app, request
from werkzeug.exceptions import HTTPException

from app import db
from app.services.sport_ingestion_service import SportIngestionError, ingest_file, ingest_results, verify_signature

bp = Blueprint("external_sports", __name__)


@bp.before_request
def authenticate():
    verify_signature(request.headers)
    # Flask 3.1 supports a per-request bound without affecting other uploads.
    if request.endpoint == "external_sports.push_file":
        request.max_content_length = current_app.config.get("SPORTS_PUSH_MAX_FILE_BYTES", 100 * 1024 * 1024) + 64 * 1024
    else:
        request.max_content_length = current_app.config.get("SPORTS_PUSH_MAX_REQUEST_BYTES", 4 * 1024 * 1024)


@bp.errorhandler(SportIngestionError)
def validation_error(exc):
    db.session.rollback()
    return {"code": exc.status, "message": str(exc)}, exc.status


@bp.errorhandler(HTTPException)
def http_error(exc):
    db.session.rollback()
    return {"code": exc.code, "message": exc.description}, exc.code


@bp.errorhandler(Exception)
def ingestion_error(exc):
    db.session.rollback()
    current_app.logger.exception("Sport callback ingestion failed")
    return {"code": 500, "message": "接收失败，请稍后重试"}, 500


@bp.post("/results")
def push_results():
    ingest_results(request.get_json())
    return {"code": 0, "message": "成功"}


@bp.post("/files")
def push_file():
    if request.mimetype != "multipart/form-data":
        raise SportIngestionError("文件上传请使用 multipart/form-data", 415)
    ingest_file(request.form, request.files.get("file"))
    return {"code": 0, "message": "成功"}
