from flask import jsonify


def api_error(message: str, status_code: int = 400):
    return jsonify({"code": status_code, "message": message, "data": None}), status_code


def api_ok(data=None, message: str = "ok"):
    return jsonify({"code": 0, "data": data, "message": message})
