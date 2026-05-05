"""Веб-страница уведомлений и API для отметки прочитанным."""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .models import Notification


def _serialize(n: Notification):
    return {
        "id": n.id,
        "kind": n.kind,
        "kind_label": n.get_kind_display(),
        "title": n.title,
        "body": n.body,
        "url": n.url,
        "actor": (
            (n.actor.get_full_name().strip() or n.actor.username) if n.actor else "Система"
        ),
        "is_unread": n.is_unread,
        "created_label": timezone.localtime(n.created_at).strftime("%d.%m.%Y %H:%M"),
    }


@login_required
@require_http_methods(["GET"])
def notifications_view(request):
    """Список уведомлений текущего пользователя.

    При первом открытии все непрочитанные помечаются как прочитанные —
    привычное поведение «inbox»."""
    user = request.user
    qs = Notification.objects.select_related("actor", "task", "leave_request").filter(
        recipient=user
    )
    items = [_serialize(n) for n in qs[:200]]
    unread_ids = list(qs.filter(read_at__isnull=True).values_list("id", flat=True))
    if unread_ids:
        Notification.objects.filter(id__in=unread_ids).update(read_at=timezone.now())

    role = getattr(user, "role", "employee")
    template = (
        "dashboard/manager/notifications.html"
        if role == "manager"
        else "dashboard/employee/notifications.html"
    )
    return render(
        request,
        template,
        {
            "active_tab": "notifications",
            "page_title": "Уведомления",
            "page_subtitle": "Все события по вашим задачам и заявкам",
            "notifications": items,
        },
    )


@login_required
@require_http_methods(["POST"])
def notifications_mark_read(request):
    Notification.objects.filter(recipient=request.user, read_at__isnull=True).update(
        read_at=timezone.now()
    )
    return JsonResponse({"ok": True})
