from django.conf import settings
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


def _safe_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def resolve_non_paginated_limit(request):
    default_limit = max(1, _safe_int(getattr(settings, "API_DEFAULT_LIST_LIMIT", 100), 100))
    max_limit = max(default_limit, _safe_int(getattr(settings, "API_MAX_LIST_LIMIT", 250), 250))

    raw = request.query_params.get("limit")
    if raw in (None, ""):
        return default_limit
    return max(1, min(_safe_int(raw, default_limit), max_limit))


def apply_non_paginated_limit(request, queryset):
    if request.query_params.get("page") or request.query_params.get("page_size"):
        return queryset
    return queryset[:resolve_non_paginated_limit(request)]


class OptionalPageNumberPagination(PageNumberPagination):
    page_size = None
    page_query_param = "page"
    page_size_query_param = "page_size"

    def get_page_size(self, request):
        page_requested = request.query_params.get(self.page_query_param)
        page_size_requested = request.query_params.get(self.page_size_query_param)
        if page_requested in (None, "") and page_size_requested in (None, ""):
            return None

        default_size = max(1, _safe_int(getattr(settings, "API_DEFAULT_PAGE_SIZE", 25), 25))
        max_size = max(default_size, _safe_int(getattr(settings, "API_MAX_PAGE_SIZE", 100), 100))
        if page_size_requested in (None, ""):
            return default_size
        return max(1, min(_safe_int(page_size_requested, default_size), max_size))

    def get_paginated_response(self, data):
        return Response(
            {
                "count": self.page.paginator.count,
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "page": self.page.number,
                "page_size": len(data),
                "results": data,
            }
        )


class BoundedListMixin:
    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        return apply_non_paginated_limit(self.request, queryset)
