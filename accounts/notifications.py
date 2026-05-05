"""Хелперы для отправки in-app уведомлений и записи истории статусов задач.

Используются из views/api при действиях сотрудника и менеджера.
"""
from __future__ import annotations

from typing import Iterable, Optional

from django.contrib.auth import get_user_model
from django.db import transaction
from django.urls import reverse, NoReverseMatch

from .models import (
    DepartmentTask,
    EmployeeProfile,
    LeaveRequest,
    Notification,
    TaskStatusHistory,
)

User = get_user_model()


# ── helpers ──────────────────────────────────────────────────────────────


def _safe_reverse(name: str, *args, **kwargs) -> str:
    try:
        return reverse(name, *args, **kwargs)
    except NoReverseMatch:
        return ""


def _user_display(user) -> str:
    if not user:
        return "—"
    full = user.get_full_name().strip()
    return full or user.username


def _department_managers(department) -> list:
    """Все активные менеджеры отдела. Менеджер указывается как руководитель
    через `EmployeeProfile.is_manager_of`/`Department.manager` в зависимости
    от настройки. Берём пересечение ролей."""
    if not department:
        return []
    candidates = []
    direct_manager = getattr(department, "manager", None)
    if direct_manager and direct_manager.is_active and getattr(direct_manager, "role", None) == "manager":
        candidates.append(direct_manager)
    profile_qs = (
        EmployeeProfile.objects.select_related("user")
        .filter(department=department, user__role="manager", user__is_active=True)
    )
    for profile in profile_qs:
        if profile.user_id and profile.user not in candidates:
            candidates.append(profile.user)
    return candidates


# ── notifications ────────────────────────────────────────────────────────


def notify(
    *,
    recipient,
    kind: str,
    title: str,
    body: str = "",
    url: str = "",
    actor=None,
    task: Optional[DepartmentTask] = None,
    leave_request: Optional[LeaveRequest] = None,
) -> Optional[Notification]:
    """Создаёт одно уведомление, если получатель валиден."""
    if not recipient or not getattr(recipient, "is_active", True):
        return None
    if actor and recipient.id == getattr(actor, "id", None):
        # не уведомляем сами себя
        return None
    return Notification.objects.create(
        recipient=recipient,
        actor=actor,
        kind=kind,
        title=title[:200],
        body=body,
        url=url[:512],
        task=task,
        leave_request=leave_request,
    )


def notify_many(recipients: Iterable, **kwargs) -> int:
    seen = set()
    count = 0
    for recipient in recipients or []:
        if not recipient or recipient.id in seen:
            continue
        seen.add(recipient.id)
        if notify(recipient=recipient, **kwargs):
            count += 1
    return count


def notify_managers_of_task(task: DepartmentTask, **kwargs) -> int:
    return notify_many(_department_managers(task.department), task=task, **kwargs)


def notify_managers_of_leave(leave: LeaveRequest, **kwargs) -> int:
    department = None
    profile = EmployeeProfile.objects.filter(user_id=leave.user_id).select_related("department").first()
    if profile:
        department = profile.department
    return notify_many(_department_managers(department), leave_request=leave, **kwargs)


# ── presets ──────────────────────────────────────────────────────────────


def notify_task_taken(task: DepartmentTask, actor) -> int:
    url = _safe_reverse("manager-task-detail", args=[task.id])
    return notify_managers_of_task(
        task,
        actor=actor,
        kind=Notification.Kind.TASK_TAKEN,
        title=f"{_user_display(actor)} взял задачу",
        body=task.title,
        url=url,
    )


def notify_task_dropped(task: DepartmentTask, actor, reason: str = "") -> int:
    url = _safe_reverse("manager-task-detail", args=[task.id])
    body = task.title
    if reason:
        body = f"{task.title}\nПричина: {reason}"
    return notify_managers_of_task(
        task,
        actor=actor,
        kind=Notification.Kind.TASK_DROPPED,
        title=f"{_user_display(actor)} отказался от задачи",
        body=body,
        url=url,
    )


def notify_task_on_review(task: DepartmentTask, actor) -> int:
    url = _safe_reverse("manager-task-detail", args=[task.id])
    return notify_managers_of_task(
        task,
        actor=actor,
        kind=Notification.Kind.TASK_ON_REVIEW,
        title=f"Задача отправлена на проверку",
        body=f"{_user_display(actor)} → «{task.title}»",
        url=url,
    )


def notify_task_returned(task: DepartmentTask, actor, comment: str = "") -> int:
    recipient = task.taken_by or task.assigned_to
    if not recipient:
        return 0
    url = _safe_reverse("employee-task-detail", args=[task.id])
    body = task.title
    if comment:
        body = f"{task.title}\nКомментарий менеджера: {comment}"
    return notify_many(
        [recipient],
        actor=actor,
        kind=Notification.Kind.TASK_RETURNED,
        title="Задача возвращена на доработку",
        body=body,
        url=url,
        task=task,
    )


def notify_task_completed(task: DepartmentTask, actor) -> int:
    recipient = task.taken_by or task.assigned_to
    if not recipient:
        return 0
    url = _safe_reverse("employee-task-detail", args=[task.id])
    return notify_many(
        [recipient],
        actor=actor,
        kind=Notification.Kind.TASK_COMPLETED,
        title="Задача принята как выполненная",
        body=task.title,
        url=url,
        task=task,
    )


def notify_task_assigned(task: DepartmentTask, actor) -> int:
    recipient = task.assigned_to or task.taken_by
    if not recipient:
        return 0
    url = _safe_reverse("employee-task-detail", args=[task.id])
    return notify_many(
        [recipient],
        actor=actor,
        kind=Notification.Kind.TASK_ASSIGNED,
        title="Вам назначена задача",
        body=task.title,
        url=url,
        task=task,
    )


def notify_task_reassigned(task: DepartmentTask, actor, previous_user=None) -> int:
    recipient = task.assigned_to or task.taken_by
    if not recipient:
        return 0
    url = _safe_reverse("employee-task-detail", args=[task.id])
    body = task.title
    if previous_user:
        body = f"{task.title}\nРанее была у {_user_display(previous_user)}"
    return notify_many(
        [recipient],
        actor=actor,
        kind=Notification.Kind.TASK_REASSIGNED,
        title="Задача переназначена на вас",
        body=body,
        url=url,
        task=task,
    )


def notify_leave_request_new(leave: LeaveRequest) -> int:
    url = _safe_reverse("manager-leave-request-detail", args=[leave.id])
    title = f"Новая заявка: {leave.get_request_type_display().lower()}"
    body = f"{_user_display(leave.user)} · {leave.start_date:%d.%m.%Y} — {leave.end_date:%d.%m.%Y}"
    return notify_managers_of_leave(
        leave,
        actor=leave.user,
        kind=Notification.Kind.LEAVE_REQUEST_NEW,
        title=title,
        body=body,
        url=url,
    )


def notify_leave_request_decision(leave: LeaveRequest, actor) -> int:
    url = _safe_reverse("employee-requests")
    decision = "одобрена" if leave.status == "approved" else "отклонена"
    body = f"{leave.get_request_type_display()} · {leave.start_date:%d.%m.%Y} — {leave.end_date:%d.%m.%Y}"
    if leave.review_comment:
        body = f"{body}\nКомментарий: {leave.review_comment}"
    elif leave.rejection_reason:
        body = f"{body}\nПричина: {leave.rejection_reason}"
    return notify_many(
        [leave.user],
        actor=actor,
        kind=Notification.Kind.LEAVE_REQUEST_DECISION,
        title=f"Заявка {decision}",
        body=body,
        url=url,
        leave_request=leave,
    )


def notify_substitution_assigned(task: DepartmentTask, actor) -> int:
    recipient = task.assigned_to or task.taken_by
    if not recipient:
        return 0
    url = _safe_reverse("employee-task-detail", args=[task.id])
    return notify_many(
        [recipient],
        actor=actor,
        kind=Notification.Kind.SUBSTITUTION_ASSIGNED,
        title="Назначена замена: задача переходит к вам",
        body=task.title,
        url=url,
        task=task,
    )


# ── status history ──────────────────────────────────────────────────────


@transaction.atomic
def record_status_change(
    task: DepartmentTask,
    *,
    from_status: str,
    to_status: str,
    actor=None,
    comment: str = "",
) -> Optional[TaskStatusHistory]:
    if from_status == to_status:
        return None
    return TaskStatusHistory.objects.create(
        task=task,
        actor=actor,
        from_status=from_status or "",
        to_status=to_status,
        comment=comment or "",
    )
