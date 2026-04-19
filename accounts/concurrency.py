from datetime import datetime

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import APIException, ValidationError


class ConflictError(APIException):
    status_code = 409
    default_code = "conflict"
    default_detail = "Ресурс был изменен другим пользователем. Обновите данные и повторите попытку."


def _as_aware(dt_value):
    if dt_value is None:
        return None
    if timezone.is_naive(dt_value):
        return timezone.make_aware(dt_value, timezone.get_current_timezone())
    return dt_value


def serialize_version(dt_value):
    aware = _as_aware(dt_value)
    if not aware:
        return ""
    return timezone.localtime(aware).isoformat()


def parse_client_version(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return _as_aware(value)

    raw = str(value).strip()
    if raw.startswith("W/"):
        raw = raw[2:].strip()
    raw = raw.strip('"')

    parsed = parse_datetime(raw)
    if parsed is None:
        raise ValidationError({"if_match": "Некорректный формат версии. Используйте ISO datetime."})
    return _as_aware(parsed)


def extract_client_version(request):
    return (
        request.headers.get("If-Match")
        or request.data.get("if_match")
        or request.data.get("updated_at")
    )


def assert_timestamp_fresh(client_version, server_version, tolerance_seconds=1):
    server_dt = _as_aware(server_version)
    if server_dt is None:
        return

    client_dt = parse_client_version(client_version)
    if client_dt is None:
        return

    if abs((server_dt - client_dt).total_seconds()) > tolerance_seconds:
        raise ConflictError(
            {
                "detail": "Данные устарели. Обновите страницу и повторите действие.",
                "current_updated_at": serialize_version(server_dt),
            }
        )


def assert_optimistic_lock(request, instance, *, field="updated_at", client_version=None):
    if client_version is None:
        client_version = extract_client_version(request)
    assert_timestamp_fresh(client_version, getattr(instance, field, None))


def assert_queryset_optimistic_lock(request, queryset, *, field="updated_at", client_version=None):
    if client_version is None:
        client_version = extract_client_version(request)
    server_version = queryset.order_by(f"-{field}").values_list(field, flat=True).first()
    assert_timestamp_fresh(client_version, server_version)
    return server_version


def set_resource_version_header(response, instance, *, field="updated_at"):
    version = serialize_version(getattr(instance, field, None))
    if version:
        response["ETag"] = f'W/"{version}"'
        response["X-Resource-Updated-At"] = version
    return response
