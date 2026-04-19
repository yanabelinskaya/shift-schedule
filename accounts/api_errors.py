from __future__ import annotations

from uuid import uuid4

from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.views import exception_handler


def _normalize_detail_message(data) -> str:
    if isinstance(data, dict):
        detail = data.get("detail")
        if detail:
            return str(detail)
        if data:
            first_value = next(iter(data.values()))
            return _normalize_detail_message(first_value)
        return "Ошибка запроса."
    if isinstance(data, list):
        if not data:
            return "Ошибка запроса."
        return _normalize_detail_message(data[0])
    if data is None:
        return "Ошибка запроса."
    return str(data)


def _extract_field_errors(detail, prefix: str = ""):
    errors = []
    if isinstance(detail, dict):
        for key, value in detail.items():
            nested_prefix = f"{prefix}.{key}" if prefix else str(key)
            errors.extend(_extract_field_errors(value, nested_prefix))
        return errors
    if isinstance(detail, list):
        for item in detail:
            errors.extend(_extract_field_errors(item, prefix))
        return errors
    if prefix:
        errors.append(
            {
                "field": prefix,
                "message": str(detail),
            }
        )
    return errors


def _status_code_to_code(http_status: int) -> str:
    mapping = {
        status.HTTP_400_BAD_REQUEST: "bad_request",
        status.HTTP_401_UNAUTHORIZED: "unauthorized",
        status.HTTP_403_FORBIDDEN: "forbidden",
        status.HTTP_404_NOT_FOUND: "not_found",
        status.HTTP_409_CONFLICT: "conflict",
        status.HTTP_405_METHOD_NOT_ALLOWED: "method_not_allowed",
        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: "unsupported_media_type",
        status.HTTP_429_TOO_MANY_REQUESTS: "throttled",
    }
    return mapping.get(http_status, "api_error")


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return response

    request = context.get("request")
    trace_id = ""
    if request is not None:
        trace_id = request.META.get("HTTP_X_REQUEST_ID", "").strip()
    if not trace_id:
        trace_id = uuid4().hex

    raw_data = response.data
    message = _normalize_detail_message(raw_data)
    if isinstance(exc, ValidationError):
        error_code = "validation_error"
    else:
        default_code = str(getattr(exc, "default_code", "") or "").strip()
        ignored_default_codes = {"error", "permission_denied", "not_authenticated"}
        error_code = (
            default_code
            if default_code and default_code not in ignored_default_codes
            else _status_code_to_code(response.status_code)
        )

    payload = {
        "code": error_code,
        "message": message,
        "detail": message,
        "trace_id": trace_id,
    }

    if isinstance(exc, ValidationError):
        payload["field_errors"] = _extract_field_errors(getattr(exc, "detail", raw_data))

    payload["errors"] = raw_data
    response.data = payload
    response["X-Trace-Id"] = trace_id
    return response
