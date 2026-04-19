import json
from datetime import date, time, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from .models import (
    Department,
    DepartmentTask,
    EmployeeAbsence,
    EmployeeAvailability,
    EmployeeProfile,
    EmployeeShiftRequest,
    GlobalSettings,
    TaskSubmission,
    UserRole,
)
from .web_views_shared import (
    _build_task_form_context,
    _ensure_role,
    _get_manager_department,
    _parse_time_value,
    _render_manager_page,
    _resolve_back_url,
)

@login_required
def manager_dashboard(request):
    return manager_calendar(request)


@login_required
@require_http_methods(["GET"])
def manager_calendar(request):
    _ensure_role(request, 'manager')
    manager = request.user
    department = _get_manager_department(manager)

    settings_obj = GlobalSettings.objects.first()
    if not settings_obj:
        settings_obj = GlobalSettings.objects.create()

    today = timezone.localdate()
    week_param = request.GET.get("week")
    if week_param is None:
        week_offset = 1
    elif week_param == "current":
        week_offset = 0
    elif week_param == "next":
        week_offset = 1
    else:
        try:
            week_offset = int(week_param)
        except (TypeError, ValueError):
            week_offset = 1

    current_week_start = today - timedelta(days=today.weekday())
    week_start = current_week_start + timedelta(weeks=week_offset)
    week_end = week_start + timedelta(days=6)

    month_names = [
        "января",
        "февраля",
        "марта",
        "апреля",
        "мая",
        "июня",
        "июля",
        "августа",
        "сентября",
        "октября",
        "ноября",
        "декабря",
    ]
    weekday_short = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

    visible_dates = [week_start + timedelta(days=offset) for offset in range(7)]

    if visible_dates:
        period_start = visible_dates[0]
        period_end = visible_dates[-1]
        if period_start.month == period_end.month:
            period_label = f"{period_start.day}–{period_end.day} {month_names[period_end.month - 1]}"
        else:
            period_label = (
                f"{period_start.day} {month_names[period_start.month - 1]} — "
                f"{period_end.day} {month_names[period_end.month - 1]}"
            )
    else:
        period_label = "Неделя"

    days = [
        {
            "date": day_date.isoformat(),
            "label": f"{weekday_short[day_date.weekday()]} {day_date.day}",
            "weekday": day_date.weekday(),
        }
        for day_date in visible_dates
    ]

    department_name = department.name if department else "—"
    employees = []
    user_ids = []
    if department:
        profiles = (
            EmployeeProfile.objects.select_related("user")
            .filter(department=department, user__role="employee")
            .order_by("user__last_name", "user__first_name", "user__username")
        )
        for profile in profiles:
            user = profile.user
            full_name = user.get_full_name().strip()
            if not full_name:
                full_name = user.username
            short_name = full_name
            if user.last_name and user.first_name:
                short_name = f"{user.last_name} {user.first_name[:1]}."
            elif user.last_name:
                short_name = user.last_name
            elif user.first_name:
                short_name = user.first_name
            employees.append(
                {
                    "id": user.id,
                    "name": full_name,
                    "short_name": short_name,
                }
            )
            user_ids.append(user.id)

    absence_by_user_date = {}
    if user_ids and visible_dates:
        range_start = visible_dates[0]
        range_end = visible_dates[-1]
        visible_set = set(visible_dates)
        absences = EmployeeAbsence.objects.filter(
            user_id__in=user_ids,
            start_date__lte=range_end,
            end_date__gte=range_start,
        )
        for absence in absences:
            start_date = max(absence.start_date, range_start)
            end_date = min(absence.end_date, range_end)
            current = start_date
            while current <= end_date:
                if current in visible_set:
                    key = (absence.user_id, current)
                    if absence.absence_type == "sick" or key not in absence_by_user_date:
                        absence_by_user_date[key] = absence.absence_type
                current += timedelta(days=1)

    priority_rank = {"high": 3, "mid": 2, "low": 1}
    priority_labels = {
        "high": "Очень хочу",
        "mid": "Ок",
        "low": "Не хочу",
    }

    daily_norm_minutes = None
    if visible_dates and settings_obj.weekly_hours_norm:
        daily_norm_minutes = (settings_obj.weekly_hours_norm * 60) / len(visible_dates)

    def add_slot(slots_map, start_value, end_value):
        if not start_value or not end_value:
            return None
        start_minutes = start_value.hour * 60 + start_value.minute
        end_minutes = end_value.hour * 60 + end_value.minute
        if end_minutes <= start_minutes:
            return None
        duration_minutes = end_minutes - start_minutes
        duration_hours = duration_minutes / 60
        key = f"{start_value:%H%M}-{end_value:%H%M}"
        if key in slots_map:
            return key
        slots_map[key] = {
            "key": key,
            "start": start_value.strftime("%H:%M"),
            "end": end_value.strftime("%H:%M"),
            "label": f"{start_value:%H:%M}-{end_value:%H:%M}",
            "start_minutes": start_minutes,
            "duration_minutes": duration_minutes,
            "duration_label": f"{duration_hours:.1f}".replace(".", ",") + " ч",
        }
        return key

    slots_map = {}

    candidate_map = {day_date: {} for day_date in visible_dates}
    availability_count = 0
    approved_entries = EmployeeAvailability.objects.none()
    availability_entries = EmployeeAvailability.objects.none()
    schedule_approved = False

    if user_ids and visible_dates:
        availability_entries = list(
            EmployeeAvailability.objects.select_related("user")
            .filter(user_id__in=user_ids, date__in=visible_dates, is_available=True)
        )
        if absence_by_user_date:
            availability_entries = [
                entry
                for entry in availability_entries
                if not absence_by_user_date.get((entry.user_id, entry.date))
            ]
        availability_count = len(availability_entries)
        for entry in availability_entries:
            add_slot(slots_map, entry.start_time, entry.end_time)
            slot_key = f"{entry.start_time:%H%M}-{entry.end_time:%H%M}"
            candidate_map.setdefault(entry.date, {}).setdefault(slot_key, []).append(
                {
                    "user_id": entry.user_id,
                    "name": entry.user.get_full_name().strip() or entry.user.username,
                    "short_name": (
                        f"{entry.user.last_name} {entry.user.first_name[:1]}."
                        if entry.user.last_name and entry.user.first_name
                        else (entry.user.last_name or entry.user.first_name or entry.user.username)
                    ),
                    "priority": entry.priority,
                    "priority_label": priority_labels.get(entry.priority, "Ок"),
                }
            )

        schedule_approved = EmployeeAvailability.objects.filter(
            user_id__in=user_ids,
            date__in=visible_dates,
            is_approved=True,
        ).exists()
        approved_entries = list(
            EmployeeAvailability.objects.select_related("user")
            .filter(
                user_id__in=user_ids,
                date__in=visible_dates,
                is_available=True,
                is_approved=True,
            )
        )
        if absence_by_user_date:
            approved_entries = [
                entry
                for entry in approved_entries
                if not absence_by_user_date.get((entry.user_id, entry.date))
            ]

    for day_date, slots in candidate_map.items():
        for slot_key, candidates in slots.items():
            candidates.sort(
                key=lambda item: (-priority_rank.get(item["priority"], 0), item["name"])
            )

    shift_slots = sorted(slots_map.values(), key=lambda item: item["start_minutes"])
    assigned_minutes = {item["id"]: 0 for item in employees}
    has_approved = schedule_approved

    def choose_candidate(candidates):
        if not candidates:
            return None
        sorted_candidates = sorted(
            candidates,
            key=lambda item: (
                -priority_rank.get(item["priority"], 0),
                assigned_minutes.get(item["user_id"], 0),
                item["name"],
            ),
        )
        return sorted_candidates[0]

    assigned_map = {day_date: {} for day_date in visible_dates}

    if has_approved:
        for entry in approved_entries:
            slot_key = f"{entry.start_time:%H%M}-{entry.end_time:%H%M}"
            assigned_map.setdefault(entry.date, {}).setdefault(slot_key, set()).add(entry.user_id)
    else:
        for day_date in visible_dates:
            for slot in shift_slots:
                candidates = candidate_map.get(day_date, {}).get(slot["key"], [])
                if not candidates:
                    continue
                assigned = choose_candidate(candidates)
                if assigned:
                    assigned_map[day_date].setdefault(slot["key"], set()).add(assigned["user_id"])
                    assigned_minutes[assigned["user_id"]] = (
                        assigned_minutes.get(assigned["user_id"], 0) + slot["duration_minutes"]
                    )

    required_slot_keys = [slot["key"] for slot in shift_slots]
    required_slot_keys.sort(key=lambda key: slots_map[key]["start_minutes"] if key in slots_map else 0)

    availability_by_date = {}
    for entry in availability_entries:
        availability_by_date.setdefault(entry.date, 0)
        availability_by_date[entry.date] += 1

    time_options = []
    for hour in range(24):
        for minute in (0, 30):
            time_options.append(f"{hour:02d}:{minute:02d}")

    missing_days = []
    empty_slots = 0
    assigned_slots = 0
    for day_date in visible_dates:
        missing_slots = []
        has_any = availability_by_date.get(day_date, 0) > 0
        for slot_key in required_slot_keys:
            candidates = candidate_map.get(day_date, {}).get(slot_key, [])
            if not candidates:
                empty_slots += 1
                if not has_any:
                    missing_slots.append(slots_map[slot_key]["label"])
            if assigned_map.get(day_date, {}).get(slot_key):
                assigned_slots += 1
        missing_days.append(
            {
                "date": day_date.isoformat(),
                "slots": missing_slots if not has_any else [],
                "has_any": has_any,
            }
        )

    availability_map = {(entry.user_id, entry.date): entry for entry in availability_entries}
    employee_minutes = {item["id"]: 0 for item in employees}
    for entry in availability_entries:
        start_minutes = entry.start_time.hour * 60 + entry.start_time.minute
        end_minutes = entry.end_time.hour * 60 + entry.end_time.minute
        duration = max(0, end_minutes - start_minutes)
        employee_minutes[entry.user_id] = employee_minutes.get(entry.user_id, 0) + duration

    absence_labels = {
        "vacation": "Отпуск",
        "sick": "Больничный",
    }
    employee_rows = []
    for employee in employees:
        employee_id = employee["id"]
        minutes = employee_minutes.get(employee_id, 0)
        hours_label = f"{minutes / 60:.1f}".replace(".", ",")
        cells = []
        for day_date in visible_dates:
            absence_type = absence_by_user_date.get((employee_id, day_date))
            if absence_type:
                cells.append(
                    {
                        "date": day_date.isoformat(),
                        "is_available": False,
                        "is_absent": True,
                        "absence_type": absence_type,
                        "absence_label": absence_labels.get(absence_type, "Отсутствует"),
                    }
                )
                continue
            entry = availability_map.get((employee_id, day_date))
            if entry and entry.is_available:
                slot_key = f"{entry.start_time:%H%M}-{entry.end_time:%H%M}"
                start_minutes = entry.start_time.hour * 60 + entry.start_time.minute
                end_minutes = entry.end_time.hour * 60 + entry.end_time.minute
                duration_minutes = max(0, end_minutes - start_minutes)
                is_overtime = (
                    daily_norm_minutes is not None
                    and duration_minutes > daily_norm_minutes
                )
                is_undertime = (
                    daily_norm_minutes is not None
                    and duration_minutes < daily_norm_minutes
                )
                assigned_users = assigned_map.get(day_date, {}).get(slot_key, set())
                is_assigned = (employee_id in assigned_users) or has_approved
                cells.append(
                    {
                        "date": day_date.isoformat(),
                        "start": entry.start_time.strftime("%H:%M"),
                        "end": entry.end_time.strftime("%H:%M"),
                        "label": f"{entry.start_time:%H:%M}-{entry.end_time:%H:%M}",
                        "priority": entry.priority,
                        "priority_label": priority_labels.get(entry.priority, "Ок"),
                        "is_assigned": is_assigned,
                        "slot_key": slot_key,
                        "is_available": True,
                        "is_overtime": is_overtime,
                        "is_undertime": is_undertime,
                    }
                )
            else:
                cells.append(
                    {
                        "date": day_date.isoformat(),
                        "is_available": False,
                        "is_absent": False,
                    }
                )
        employee_rows.append(
            {
                "id": employee_id,
                "name": employee["name"],
                "short_name": employee["short_name"],
                "hours_label": hours_label,
                "cells": cells,
            }
        )

    total_slots = len(required_slot_keys) * len(visible_dates)
    coverage_percent = int(round((assigned_slots / total_slots) * 100)) if total_slots else 0
    coverage_label = f"{coverage_percent}%" if total_slots else "—"

    shift_requests = []
    if user_ids:
        requests_queryset = (
            EmployeeShiftRequest.objects.select_related("user")
            .filter(user_id__in=user_ids, date__range=(week_start, week_end))
            .order_by("-created_at")
        )
        status_map = {
            "pending": "warning",
            "approved": "success",
            "rejected": "danger",
        }
        for request_item in requests_queryset[:120]:
            request_date = request_item.date
            date_label = f"{request_date.day} {month_names[request_date.month - 1]}"
            if request_item.start_time and request_item.end_time:
                time_label = f"{request_item.start_time:%H:%M}-{request_item.end_time:%H:%M}"
            else:
                time_label = "—"
            user_name = request_item.user.get_full_name().strip() or request_item.user.username
            shift_requests.append(
                {
                    "id": request_item.id,
                    "user_id": request_item.user_id,
                    "user_name": user_name,
                    "date_label": date_label,
                    "date": request_item.date.isoformat(),
                    "time_label": time_label,
                    "start_time": request_item.start_time.strftime("%H:%M") if request_item.start_time else "",
                    "end_time": request_item.end_time.strftime("%H:%M") if request_item.end_time else "",
                    "request_type": request_item.request_type,
                    "request_type_label": request_item.get_request_type_display(),
                    "reason": request_item.reason,
                    "status": request_item.status,
                    "status_label": request_item.get_status_display(),
                    "status_tone": status_map.get(request_item.status, "muted"),
                }
            )

    return render(
        request,
        'dashboard/manager/calendar.html',
        {
            'active_tab': 'calendar',
            'page_title': 'Календарь отдела',
            'page_subtitle': 'Планирование слотов доступности и контроль нагрузки',
            'department_name': department_name,
            'period_label': period_label,
            'days': days,
            'employee_rows': employee_rows,
            'missing_days': missing_days,
            'availability_count': availability_count,
            'empty_slots': empty_slots,
            'coverage_label': coverage_label,
            'week_start': week_start.isoformat(),
            'week_end': week_end.isoformat(),
            'current_week_start': current_week_start.isoformat(),
            'week_offset': week_offset,
            'week_prev': week_offset - 1,
            'week_next': week_offset + 1,
            'has_department': bool(department),
            'has_schedule': bool(employee_rows),
            'schedule_approved': has_approved,
            'time_options': time_options,
            'shift_requests': shift_requests,
            'weekly_hours_norm': settings_obj.weekly_hours_norm,
        },
    )


@login_required
def manager_requests(request):
    return _render_manager_page(
        request,
        'dashboard/manager/requests.html',
        'requests',
        'Запросы сотрудников',
        'Изменения слотов и подтверждения',
    )


@login_required
def manager_tasks(request):
    _ensure_role(request, 'manager')
    manager = request.user
    department = _get_manager_department(manager)

    settings_obj = GlobalSettings.objects.first()
    if not settings_obj:
        settings_obj = GlobalSettings.objects.create()

    today = timezone.localdate()
    week_param = request.GET.get("week")
    if week_param is None:
        week_offset = 0
    elif week_param == "current":
        week_offset = 0
    elif week_param == "next":
        week_offset = 1
    elif week_param == "prev":
        week_offset = -1
    else:
        try:
            week_offset = int(week_param)
        except (TypeError, ValueError):
            week_offset = 0

    week_start = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)
    week_end = week_start + timedelta(days=6)

    month_names = [
        "января",
        "февраля",
        "марта",
        "апреля",
        "мая",
        "июня",
        "июля",
        "августа",
        "сентября",
        "октября",
        "ноября",
        "декабря",
    ]
    weekday_short = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

    visible_dates = [week_start + timedelta(days=offset) for offset in range(7)]

    if visible_dates:
        period_start = visible_dates[0]
        period_end = visible_dates[-1]
        if period_start.month == period_end.month:
            period_label = f"{period_start.day}–{period_end.day} {month_names[period_end.month - 1]}"
        else:
            period_label = (
                f"{period_start.day} {month_names[period_start.month - 1]} — "
                f"{period_end.day} {month_names[period_end.month - 1]}"
            )
    else:
        period_label = "Неделя"

    days = [
        {
            "date": day_date.isoformat(),
            "label": f"{weekday_short[day_date.weekday()]} {day_date.day}",
        }
        for day_date in visible_dates
    ]

    employees = []
    user_ids = []
    department_name = department.name if department else "—"
    if department:
        profiles = (
            EmployeeProfile.objects.select_related("user")
            .filter(department=department, user__role="employee")
            .order_by("user__last_name", "user__first_name", "user__username")
        )
        for profile in profiles:
            user = profile.user
            full_name = user.get_full_name().strip() or user.username
            short_name = full_name
            if user.last_name and user.first_name:
                short_name = f"{user.last_name} {user.first_name[:1]}."
            elif user.last_name:
                short_name = user.last_name
            elif user.first_name:
                short_name = user.first_name
            employees.append(
                {
                    "id": user.id,
                    "name": full_name,
                    "short_name": short_name,
                }
            )
            user_ids.append(user.id)

    employee_filter = request.GET.get("employee")
    status_filter = request.GET.get("status")
    priority_filter = request.GET.get("priority")
    type_filter = request.GET.get("type")

    if status_filter not in {"todo", "in_progress", "done"}:
        status_filter = "all"
    if priority_filter not in {"high", "mid", "low"}:
        priority_filter = "all"
    if type_filter not in {"employee", "slot", "department"}:
        type_filter = "all"

    employee_filter_id = None
    if employee_filter and employee_filter != "all":
        try:
            employee_filter_id = int(employee_filter)
        except (TypeError, ValueError):
            employee_filter_id = None

    if employee_filter_id:
        employees = [emp for emp in employees if emp["id"] == employee_filter_id]
        user_ids = [emp["id"] for emp in employees]

    absence_by_user_date = {}
    if user_ids and visible_dates:
        range_start = visible_dates[0]
        range_end = visible_dates[-1]
        visible_set = set(visible_dates)
        absences = EmployeeAbsence.objects.filter(
            user_id__in=user_ids,
            start_date__lte=range_end,
            end_date__gte=range_start,
        )
        for absence in absences:
            start_date = max(absence.start_date, range_start)
            end_date = min(absence.end_date, range_end)
            current = start_date
            while current <= end_date:
                if current in visible_set:
                    key = (absence.user_id, current)
                    if absence.absence_type == "sick" or key not in absence_by_user_date:
                        absence_by_user_date[key] = absence.absence_type
                current += timedelta(days=1)

    availability_entries = []
    availability_map = {}
    employee_minutes = {item["id"]: 0 for item in employees}
    if user_ids and visible_dates:
        availability_entries = list(
            EmployeeAvailability.objects.select_related("user")
            .filter(user_id__in=user_ids, date__in=visible_dates, is_available=True)
        )
        if absence_by_user_date:
            availability_entries = [
                entry
                for entry in availability_entries
                if not absence_by_user_date.get((entry.user_id, entry.date))
            ]
        availability_map = {(entry.user_id, entry.date): entry for entry in availability_entries}
        for entry in availability_entries:
            start_minutes = entry.start_time.hour * 60 + entry.start_time.minute
            end_minutes = entry.end_time.hour * 60 + entry.end_time.minute
            duration = max(0, end_minutes - start_minutes)
            employee_minutes[entry.user_id] = employee_minutes.get(entry.user_id, 0) + duration

    tasks_week_qs = DepartmentTask.objects.filter(
        department=department, date__range=(week_start, week_end)
    ) if department else DepartmentTask.objects.none()

    tasks_queryset = tasks_week_qs
    if status_filter != "all":
        tasks_queryset = tasks_queryset.filter(status=status_filter)
    if priority_filter != "all":
        tasks_queryset = tasks_queryset.filter(priority=priority_filter)
    if type_filter != "all":
        tasks_queryset = tasks_queryset.filter(task_type=type_filter)

    if employee_filter_id:
        if type_filter == "slot":
            tasks_queryset = tasks_queryset.filter(task_type="slot")
        elif type_filter == "department":
            tasks_queryset = tasks_queryset.filter(task_type="department")
        elif type_filter == "employee":
            tasks_queryset = tasks_queryset.filter(assigned_to_id=employee_filter_id)
        else:
            tasks_queryset = tasks_queryset.filter(
                Q(assigned_to_id=employee_filter_id) | Q(task_type="slot") | Q(task_type="department")
            )

    tasks_queryset = tasks_queryset.select_related("assigned_to", "created_by")

    priority_rank = {"high": 3, "mid": 2, "low": 1}
    priority_labels = {"high": "Высокий", "mid": "Средний", "low": "Низкий"}
    status_labels = {
        "todo": "Назначена",
        "in_progress": "В работе",
        "done": "Выполнено",
    }
    status_tones = {
        "todo": "muted",
        "in_progress": "warning",
        "done": "success",
    }

    tasks_by_employee_date = {}
    slot_tasks_by_time = {}
    for task in tasks_queryset:
        if task.task_type == "slot":
            slot_key = (task.date, task.start_time, task.end_time)
            slot_tasks_by_time.setdefault(slot_key, []).append(task)
        elif task.task_type == "employee":
            tasks_by_employee_date.setdefault((task.assigned_to_id, task.date), []).append(task)

    def serialize_task(task, is_slot=False):
        return {
            "id": task.id,
            "title": task.title,
            "priority": task.priority,
            "priority_label": priority_labels.get(task.priority, "Средний"),
            "status": task.status,
            "status_label": status_labels.get(task.status, "Назначена"),
            "tone": status_tones.get(task.status, "muted"),
            "is_slot": is_slot,
        }

    absence_labels = {
        "vacation": "Отпуск",
        "sick": "Больничный",
    }

    employee_rows = []
    for employee in employees:
        employee_id = employee["id"]
        minutes = employee_minutes.get(employee_id, 0)
        hours_label = f"{minutes / 60:.1f}".replace(".", ",") if minutes else "0"
        cells = []
        for day_date in visible_dates:
            date_label = f"{day_date.day} {month_names[day_date.month - 1]}"
            absence_type = absence_by_user_date.get((employee_id, day_date))
            if absence_type:
                cells.append(
                    {
                        "date": day_date.isoformat(),
                        "date_label": date_label,
                        "is_absent": True,
                        "absence_type": absence_type,
                        "absence_label": absence_labels.get(absence_type, "Отсутствует"),
                        "tasks_count": 0,
                        "tasks_preview": [],
                        "extra_count": 0,
                    }
                )
                continue
            entry = availability_map.get((employee_id, day_date))
            slot_tasks = []
            if entry:
                slot_tasks = slot_tasks_by_time.get((day_date, entry.start_time, entry.end_time), [])
            employee_tasks = tasks_by_employee_date.get((employee_id, day_date), [])
            combined_tasks = [
                *[serialize_task(task, is_slot=False) for task in employee_tasks],
                *[serialize_task(task, is_slot=True) for task in slot_tasks],
            ]
            combined_tasks.sort(
                key=lambda item: (
                    -priority_rank.get(item["priority"], 0),
                    item["status"] == "done",
                    item["title"],
                )
            )
            preview = combined_tasks[:3]
            extra_count = max(0, len(combined_tasks) - len(preview))
            if entry and entry.is_available:
                cells.append(
                    {
                        "date": day_date.isoformat(),
                        "date_label": date_label,
                        "is_absent": False,
                        "has_slot": True,
                        "slot_label": f"{entry.start_time:%H:%M}-{entry.end_time:%H:%M}",
                        "start": entry.start_time.strftime("%H:%M"),
                        "end": entry.end_time.strftime("%H:%M"),
                        "tasks_count": len(combined_tasks),
                        "tasks_preview": preview,
                        "extra_count": extra_count,
                    }
                )
            else:
                cells.append(
                    {
                        "date": day_date.isoformat(),
                        "date_label": date_label,
                        "is_absent": False,
                        "has_slot": False,
                        "tasks_count": len(combined_tasks),
                        "tasks_preview": preview,
                        "extra_count": extra_count,
                    }
                )
        employee_rows.append(
            {
                "id": employee_id,
                "name": employee["name"],
                "short_name": employee["short_name"],
                "hours_label": hours_label,
                "cells": cells,
            }
        )

    tasks_list = []
    shared_tasks_list = []
    now = timezone.localtime()
    for task in tasks_queryset:
        date_label = f"{task.date.day} {month_names[task.date.month - 1]}"
        slot_label = "—"
        if task.start_time and task.end_time:
            slot_label = f"{task.start_time:%H:%M}-{task.end_time:%H:%M}"
        due_label = "—"
        if task.due_time:
            due_label = f"{task.date.day} {month_names[task.date.month - 1]}, {task.due_time:%H:%M}"
        elif task.end_time:
            due_label = f"{task.date.day} {month_names[task.date.month - 1]}, {task.end_time:%H:%M}"
        is_overdue = False
        if task.status != "done":
            due_time = task.due_time or task.end_time
            if task.date < now.date():
                is_overdue = True
            elif due_time and task.date == now.date() and due_time < now.time():
                is_overdue = True

        assignee_label = "—"
        if task.task_type == "slot":
            assignee_label = "Все в слоте"
        elif task.task_type == "department":
            assignee_label = "Все сотрудники"
        if task.assigned_to:
            assignee_label = task.assigned_to.get_full_name().strip() or task.assigned_to.username

        tone = status_tones.get(task.status, "muted")
        if is_overdue:
            tone = "danger"

        due_time_value = ""
        if task.due_time:
            due_time_value = task.due_time.strftime("%H:%M")
        elif task.end_time:
            due_time_value = task.end_time.strftime("%H:%M")

        task_type_label = "Сотрудник"
        if task.task_type == "slot":
            task_type_label = "Слот"
        elif task.task_type == "department":
            task_type_label = "Отдел"

        payload = {
            "id": task.id,
            "title": task.title,
            "description": task.description,
            "assignee": assignee_label,
            "date_label": date_label,
            "date_value": task.date.isoformat(),
            "slot_label": slot_label,
            "due_label": due_label,
            "due_time_value": due_time_value,
            "priority_label": priority_labels.get(task.priority, "Средний"),
            "priority": task.priority,
            "status_label": status_labels.get(task.status, "Назначена"),
            "status": task.status,
            "status_tone": tone,
            "is_overdue": is_overdue,
            "task_type": task.task_type,
            "task_type_label": task_type_label,
        }
        if task.task_type == "employee":
            tasks_list.append(payload)
        else:
            shared_tasks_list.append(payload)

    tasks_total = tasks_week_qs.count()
    tasks_done = tasks_week_qs.filter(status="done").count()
    tasks_progress = tasks_week_qs.filter(status="in_progress").count()
    tasks_todo = tasks_week_qs.filter(status="todo").count()
    tasks_overdue = 0
    for task in tasks_week_qs:
        if task.status == "done":
            continue
        due_time = task.due_time or task.end_time
        if task.date < now.date():
            tasks_overdue += 1
        elif due_time and task.date == now.date() and due_time < now.time():
            tasks_overdue += 1

    extend_date_options = []
    for offset in range(0, 31):
        day_date = today + timedelta(days=offset)
        extend_date_options.append(
            {
                "value": day_date.isoformat(),
                "label": f"{weekday_short[day_date.weekday()]} {day_date.day} {month_names[day_date.month - 1]}",
            }
        )

    week_options = []
    for offset in range(-2, 3):
        option_start = today - timedelta(days=today.weekday()) + timedelta(weeks=offset)
        option_end = option_start + timedelta(days=6)
        if option_start.month == option_end.month:
            label = f"Неделя {option_start.day}–{option_end.day} {month_names[option_end.month - 1]}"
        else:
            label = (
                f"Неделя {option_start.day} {month_names[option_start.month - 1]} — "
                f"{option_end.day} {month_names[option_end.month - 1]}"
            )
        week_options.append({"value": offset, "label": label})

    time_options = []
    for hour in range(24):
        for minute in (0, 30):
            time_options.append(f"{hour:02d}:{minute:02d}")

    return render(
        request,
        'dashboard/manager/tasks.html',
        {
            'active_tab': 'tasks',
            'page_title': 'Задачи отдела',
            'page_subtitle': 'Постановка и контроль выполнения',
            'department_name': department_name,
            'period_label': period_label,
            'days': days,
            'employee_rows': employee_rows,
            'week_start': week_start.isoformat(),
            'week_end': week_end.isoformat(),
            'week_offset': week_offset,
            'week_prev': week_offset - 1,
            'week_next': week_offset + 1,
            'week_options': week_options,
            'employees': employees,
            'tasks': tasks_list,
            'shared_tasks': shared_tasks_list,
            'tasks_total': tasks_total,
            'tasks_done': tasks_done,
            'tasks_progress': tasks_progress,
            'tasks_todo': tasks_todo,
            'tasks_overdue': tasks_overdue,
            'extend_date_options': extend_date_options,
            'filters': {
                'employee': employee_filter_id or "all",
                'status': status_filter or "all",
                'priority': priority_filter or "all",
                'type': type_filter or "all",
                'week': week_offset,
            },
            'time_options': time_options,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def manager_task_create(request):
    _ensure_role(request, 'manager')
    manager = request.user
    department = _get_manager_department(manager)
    if not department:
        return JsonResponse({"detail": "Менеджер не привязан к отделу."}, status=400)

    def parse_time_value(value):
        if not value:
            return None
        try:
            hours, minutes = str(value).split(":")[:2]
            return time(int(hours), int(minutes))
        except (TypeError, ValueError):
            return None

    today = timezone.localdate()

    def resolve_base_date(source):
        date_raw = source.get("date")
        if date_raw:
            try:
                return date.fromisoformat(str(date_raw))
            except (TypeError, ValueError):
                pass
        week_param = source.get("week")
        week_offset = 0
        if week_param is None:
            week_offset = 0
        elif week_param == "current":
            week_offset = 0
        elif week_param == "next":
            week_offset = 1
        elif week_param == "prev":
            week_offset = -1
        else:
            try:
                week_offset = int(week_param)
            except (TypeError, ValueError):
                week_offset = 0
        return today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)

    if request.method == "GET":
        base_date = resolve_base_date(request.GET)
        initial = {
            "task_type": (request.GET.get("type") or "employee").strip().lower(),
            "employee": request.GET.get("employee") or "",
            "date": request.GET.get("date") or base_date.isoformat(),
            "start_time": request.GET.get("start") or "",
            "end_time": request.GET.get("end") or "",
            "priority": (request.GET.get("priority") or "mid").strip().lower(),
            "title": request.GET.get("title") or "",
            "description": request.GET.get("description") or "",
        }
        if initial["task_type"] not in {"employee", "slot", "department"}:
            initial["task_type"] = "employee"
        return render(
            request,
            "dashboard/manager/task_create.html",
            _build_task_form_context(
                department,
                base_date,
                initial,
                page_title="Новая задача",
                page_subtitle="Назначьте задачу и параметры исполнения",
                form_title="Новая задача",
                form_subtitle="Заполните детали и назначьте исполнителя.",
                submit_label="Создать задачу",
                back_url=_resolve_back_url(request, reverse("manager-tasks")),
            ),
        )

    payload = {}
    is_json = request.content_type and "application/json" in request.content_type
    if is_json:
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return JsonResponse({"detail": "Некорректный формат данных."}, status=400)
    else:
        payload = request.POST

    def fail(message):
        if is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"detail": message}, status=400)
        base_date = resolve_base_date(payload)
        initial = {
            "task_type": (payload.get("task_type") or "employee").strip().lower(),
            "employee": payload.get("assigned_to") or "",
            "date": payload.get("date") or base_date.isoformat(),
            "start_time": payload.get("start_time") or "",
            "end_time": payload.get("end_time") or "",
            "priority": (payload.get("priority") or "mid").strip().lower(),
            "title": payload.get("title") or "",
            "description": payload.get("description") or "",
        }
        if initial["task_type"] not in {"employee", "slot", "department"}:
            initial["task_type"] = "employee"
        return render(
            request,
            "dashboard/manager/task_create.html",
            _build_task_form_context(
                department,
                base_date,
                initial,
                message,
                page_title="Новая задача",
                page_subtitle="Назначьте задачу и параметры исполнения",
                form_title="Новая задача",
                form_subtitle="Заполните детали и назначьте исполнителя.",
                submit_label="Создать задачу",
                back_url=_resolve_back_url(request, reverse("manager-tasks")),
            ),
        )

    title = (payload.get("title") or "").strip()
    if not title:
        return fail("Укажите название задачи.")

    date_raw = payload.get("date")
    try:
        task_date = date.fromisoformat(str(date_raw))
    except (TypeError, ValueError):
        return fail("Некорректная дата.")

    task_type = (payload.get("task_type") or "employee").strip().lower()
    if task_type not in {"employee", "slot", "department"}:
        task_type = "employee"

    assigned_to = None
    assigned_to_id = payload.get("assigned_to")
    if task_type == "employee":
        try:
            assigned_to_id = int(assigned_to_id)
        except (TypeError, ValueError):
            assigned_to_id = None
        if not assigned_to_id:
            return fail("Выберите сотрудника.")
        assigned_to = (
            get_user_model()
            .objects.filter(id=assigned_to_id, role="employee", is_active=True)
            .first()
        )
        if not assigned_to or not EmployeeProfile.objects.filter(
            user=assigned_to, department=department
        ).exists():
            return fail("Сотрудник не найден.")

    start_time = parse_time_value(payload.get("start_time"))
    end_time = parse_time_value(payload.get("end_time"))
    due_time = parse_time_value(payload.get("due_time")) or end_time

    if start_time and end_time and start_time >= end_time:
        return fail("Время окончания должно быть позже начала.")

    if task_type == "slot" and (start_time is None or end_time is None):
        return fail("Для слота укажите время.")

    priority = (payload.get("priority") or "mid").strip().lower()
    if priority not in {"high", "mid", "low"}:
        priority = "mid"

    status = "todo"

    description = (payload.get("description") or "").strip()

    task = DepartmentTask.objects.create(
        department=department,
        created_by=manager,
        assigned_to=assigned_to,
        date=task_date,
        start_time=start_time,
        end_time=end_time,
        due_time=due_time,
        title=title,
        description=description,
        task_type=task_type,
        priority=priority,
        status=status,
    )

    if is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"ok": True, "task_id": task.id})

    current_week_start = today - timedelta(days=today.weekday())
    task_week_start = task_date - timedelta(days=task_date.weekday())
    week_offset = (task_week_start - current_week_start).days // 7
    return redirect(f"{reverse('manager-tasks')}?week={week_offset}")


@login_required
def manager_payroll(request):
    return _render_manager_page(
        request,
        'dashboard/manager/payroll.html',
        'payroll',
        'Отчеты и нагрузка',
        'Нагрузка, время и переработки',
    )


@login_required
def manager_task_detail(request, task_id):
    _ensure_role(request, 'manager')
    manager = request.user
    department = _get_manager_department(manager)
    if not department:
        return JsonResponse({"detail": "Менеджер не привязан к отделу."}, status=400)

    task = get_object_or_404(DepartmentTask, id=task_id, department=department)
    submission_error = ""

    if request.method == "POST":
        comment = (request.POST.get("comment") or "").strip()
        attachments = request.FILES.getlist("attachments")
        if not comment and not attachments:
            submission_error = "Добавьте комментарий или файл."
        else:
            if attachments:
                for attachment in attachments:
                    TaskSubmission.objects.create(
                        task=task,
                        author=request.user,
                        comment=comment,
                        attachment=attachment,
                    )
            else:
                TaskSubmission.objects.create(
                    task=task,
                    author=request.user,
                    comment=comment,
                )
            if task.status == "done":
                task.status = "in_progress"
                task.save(update_fields=["status", "updated_at"])
            return redirect(request.get_full_path())
    month_names = [
        "января",
        "февраля",
        "марта",
        "апреля",
        "мая",
        "июня",
        "июля",
        "августа",
        "сентября",
        "октября",
        "ноября",
        "декабря",
    ]
    priority_labels = {"high": "Высокий", "mid": "Средний", "low": "Низкий"}
    status_labels = {
        "todo": "Назначена",
        "in_progress": "В работе",
        "done": "Выполнено",
    }
    status_tones = {
        "todo": "muted",
        "in_progress": "warning",
        "done": "success",
    }

    slot_label = "—"
    if task.start_time and task.end_time:
        slot_label = f"{task.start_time:%H:%M}-{task.end_time:%H:%M}"

    due_label = "—"
    if task.due_time:
        due_label = f"{task.date.day} {month_names[task.date.month - 1]}, {task.due_time:%H:%M}"
    elif task.end_time:
        due_label = f"{task.date.day} {month_names[task.date.month - 1]}, {task.end_time:%H:%M}"
    else:
        due_label = f"{task.date.day} {month_names[task.date.month - 1]}"

    is_overdue = False
    now = timezone.localtime()
    if task.status != "done":
        due_time = task.due_time or task.end_time
        if task.date < now.date():
            is_overdue = True
        elif due_time and task.date == now.date() and due_time < now.time():
            is_overdue = True

    status_tone = status_tones.get(task.status, "muted")
    if is_overdue:
        status_tone = "danger"

    task_type_label = "Сотрудник"
    if task.task_type == "slot":
        task_type_label = "Слот"
    elif task.task_type == "department":
        task_type_label = "Отдел"

    assignee_label = "—"
    if task.task_type == "slot":
        assignee_label = "Все в слоте"
    elif task.task_type == "department":
        assignee_label = "Все сотрудники"
    if task.assigned_to:
        assignee_label = task.assigned_to.get_full_name().strip() or task.assigned_to.username

    submissions = list(
        TaskSubmission.objects.select_related("author")
        .filter(task=task)
        .order_by("-created_at")
    )
    submissions_payload = []
    for submission in submissions:
        author_label = "—"
        if submission.author:
            author_label = submission.author.get_full_name().strip() or submission.author.username
        local_created = timezone.localtime(submission.created_at)
        submissions_payload.append(
            {
                "id": submission.id,
                "author": author_label,
                "comment": submission.comment,
                "file_url": submission.attachment.url if submission.attachment else "",
                "file_name": Path(submission.attachment.name).name if submission.attachment else "",
                "created_label": f"{local_created.day} {month_names[local_created.month - 1]}, {local_created:%H:%M}",
            }
        )

    back_url = _resolve_back_url(request, reverse("manager-tasks"))
    return render(
        request,
        "dashboard/manager/task_detail.html",
        {
            "active_tab": "tasks",
            "page_title": "Задача",
            "page_subtitle": "Подробная информация и сдачи сотрудников",
            "task": task,
            "task_due_label": due_label,
            "task_status_label": status_labels.get(task.status, "Назначена"),
            "task_status_tone": status_tone,
            "task_priority_label": priority_labels.get(task.priority, "Средний"),
            "task_type_label": task_type_label,
            "task_slot_label": slot_label,
            "assignee_label": assignee_label,
            "department_label": department.name,
            "submissions": submissions_payload,
            "submission_error": submission_error,
            "back_url": back_url,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def manager_task_edit(request, task_id):
    _ensure_role(request, 'manager')
    manager = request.user
    department = _get_manager_department(manager)
    if not department:
        return JsonResponse({"detail": "Менеджер не привязан к отделу."}, status=400)

    task = get_object_or_404(DepartmentTask, id=task_id, department=department)

    def parse_time_value(value):
        if not value:
            return None
        try:
            hours, minutes = str(value).split(":")[:2]
            return time(int(hours), int(minutes))
        except (TypeError, ValueError):
            return None

    def resolve_base_date(source):
        date_raw = source.get("date")
        if date_raw:
            try:
                return date.fromisoformat(str(date_raw))
            except (TypeError, ValueError):
                pass
        return task.date

    if request.method == "GET":
        base_date = resolve_base_date(request.GET)
        initial = {
            "task_type": task.task_type,
            "employee": task.assigned_to_id or "",
            "date": task.date.isoformat(),
            "start_time": task.start_time.strftime("%H:%M") if task.start_time else "",
            "end_time": task.end_time.strftime("%H:%M") if task.end_time else "",
            "priority": task.priority,
            "title": task.title,
            "description": task.description,
        }
        return render(
            request,
            "dashboard/manager/task_create.html",
            _build_task_form_context(
                department,
                base_date,
                initial,
                page_title="Редактировать задачу",
                page_subtitle="Обновите детали и сохраните изменения",
                form_title="Редактирование задачи",
                form_subtitle="Проверьте параметры и обновите детали.",
                submit_label="Сохранить изменения",
                back_url=_resolve_back_url(
                    request,
                    reverse("manager-task-detail", args=[task.id]),
                ),
            ),
        )

    payload = request.POST

    def fail(message):
        base_date = resolve_base_date(payload)
        initial = {
            "task_type": (payload.get("task_type") or "employee").strip().lower(),
            "employee": payload.get("assigned_to") or "",
            "date": payload.get("date") or base_date.isoformat(),
            "start_time": payload.get("start_time") or "",
            "end_time": payload.get("end_time") or "",
            "priority": (payload.get("priority") or "mid").strip().lower(),
            "title": payload.get("title") or "",
            "description": payload.get("description") or "",
        }
        if initial["task_type"] not in {"employee", "slot", "department"}:
            initial["task_type"] = "employee"
        return render(
            request,
            "dashboard/manager/task_create.html",
            _build_task_form_context(
                department,
                base_date,
                initial,
                message,
                page_title="Редактировать задачу",
                page_subtitle="Обновите детали и сохраните изменения",
                form_title="Редактирование задачи",
                form_subtitle="Проверьте параметры и обновите детали.",
                submit_label="Сохранить изменения",
                back_url=_resolve_back_url(
                    request,
                    reverse("manager-task-detail", args=[task.id]),
                ),
            ),
        )

    title = (payload.get("title") or "").strip()
    if not title:
        return fail("Укажите название задачи.")

    date_raw = payload.get("date")
    try:
        task_date = date.fromisoformat(str(date_raw))
    except (TypeError, ValueError):
        return fail("Некорректная дата.")

    task_type = (payload.get("task_type") or "employee").strip().lower()
    if task_type not in {"employee", "slot", "department"}:
        task_type = "employee"

    assigned_to = None
    assigned_to_id = payload.get("assigned_to")
    if task_type == "employee":
        try:
            assigned_to_id = int(assigned_to_id)
        except (TypeError, ValueError):
            assigned_to_id = None
        if not assigned_to_id:
            return fail("Выберите сотрудника.")
        assigned_to = (
            get_user_model()
            .objects.filter(id=assigned_to_id, role="employee", is_active=True)
            .first()
        )
        if not assigned_to or not EmployeeProfile.objects.filter(
            user=assigned_to, department=department
        ).exists():
            return fail("Сотрудник не найден.")

    start_time = parse_time_value(payload.get("start_time"))
    end_time = parse_time_value(payload.get("end_time"))

    if start_time and end_time and start_time >= end_time:
        return fail("Время окончания должно быть позже начала.")

    if task_type == "slot" and (start_time is None or end_time is None):
        return fail("Для слота укажите время.")

    priority = (payload.get("priority") or "mid").strip().lower()
    if priority not in {"high", "mid", "low"}:
        priority = "mid"

    description = (payload.get("description") or "").strip()

    task.title = title
    task.description = description
    task.task_type = task_type
    task.assigned_to = assigned_to
    task.date = task_date
    task.start_time = start_time
    task.end_time = end_time
    task.due_time = end_time
    task.priority = priority
    task.save(
        update_fields=[
            "title",
            "description",
            "task_type",
            "assigned_to",
            "date",
            "start_time",
            "end_time",
            "due_time",
            "priority",
            "updated_at",
        ]
    )

    return redirect(_resolve_back_url(request, reverse("manager-tasks")))


@ensure_csrf_cookie
@login_required
def manager_employees(request):
    _ensure_role(request, 'manager')
    manager = request.user
    department = (
        Department.objects.filter(manager=manager, is_archived=False).first()
    )
    if not department:
        profile = getattr(manager, 'profile', None)
        if profile and profile.department:
            department = profile.department

    profiles = (
        EmployeeProfile.objects.select_related('user', 'department')
        .filter(department=department, user__role='employee')
        .order_by('user__last_name', 'user__first_name', 'user__username')
        if department
        else EmployeeProfile.objects.none()
    )

    employees = []
    positions = set()
    today = timezone.localdate()
    user_ids = [profile.user_id for profile in profiles]
    current_absences = {}
    if user_ids:
        for absence in EmployeeAbsence.objects.filter(
            user_id__in=user_ids,
            start_date__lte=today,
            end_date__gte=today,
        ):
            if absence.absence_type == 'sick':
                current_absences[absence.user_id] = 'sick'
            elif absence.user_id not in current_absences:
                current_absences[absence.user_id] = absence.absence_type

    for profile in profiles:
        user = profile.user
        middle_name = profile.middle_name or ''
        full_name = " ".join(
            part for part in [user.last_name, user.first_name, middle_name] if part
        ).strip() or user.username
        position = (profile.position or '').strip()
        if position:
            positions.add(position)
        else:
            position = 'Без должности'
        salary_display = '—'
        if profile.monthly_salary is not None:
            salary_display = f"{profile.monthly_salary:.0f} руб/мес"
        phone = profile.corporate_phone or profile.personal_phone or '—'
        email = user.email or '—'
        absence_status = current_absences.get(user.id)
        if not user.is_active:
            status_code = 'inactive'
        elif absence_status:
            status_code = absence_status
        else:
            status_code = 'active'

        status_labels = {
            'active': 'Активен',
            'vacation': 'В отпуске',
            'sick': 'Больничный',
            'inactive': 'Неактивен',
        }
        status_classes = {
            'active': 'success',
            'vacation': 'warning',
            'sick': 'danger',
            'inactive': 'muted',
        }
        status_label = status_labels.get(status_code, 'Активен')
        status_class = status_classes.get(status_code, 'success')
        employees.append(
            {
                'id': user.id,
                'full_name': full_name,
                'position': position,
                'phone': phone,
                'email': email,
                'salary_display': salary_display,
                'status_code': status_code,
                'status_label': status_label,
                'status_class': status_class,
            }
        )

    employees_total = len(employees)
    active_count = sum(1 for employee in employees if employee['status_code'] != 'inactive')
    inactive_count = employees_total - active_count
    department_name = department.name if department else 'Отдел не назначен'
    positions_list = sorted(positions)

    return render(
        request,
        'dashboard/manager/employees.html',
        {
            'active_tab': 'employees',
            'page_title': 'Сотрудники отдела',
            'page_subtitle': 'Карточки сотрудников, статусы и отметки отсутствий',
            'employees': employees,
            'positions': positions_list,
            'department_name': department_name,
            'stats': {
                'total': employees_total,
                'active': active_count,
                'inactive': inactive_count,
            },
        },
    )


@ensure_csrf_cookie
@login_required
def manager_employee_detail(request, user_id):
    _ensure_role(request, 'manager')
    manager = request.user
    department = (
        Department.objects.filter(manager=manager, is_archived=False).first()
    )
    if not department:
        profile = getattr(manager, 'profile', None)
        if profile and profile.department:
            department = profile.department

    if not department:
        raise PermissionDenied

    profile = (
        EmployeeProfile.objects.select_related('user', 'department')
        .filter(user_id=user_id, user__role='employee')
        .first()
    )
    if not profile or profile.department_id != department.id:
        raise PermissionDenied

    user = profile.user
    middle_name = profile.middle_name or ''
    full_name = " ".join(
        part for part in [user.last_name, user.first_name, middle_name] if part
    ).strip() or user.username
    position = (profile.position or '').strip() or 'Без должности'
    salary_display = '—'
    if profile.monthly_salary is not None:
        salary_display = f"{profile.monthly_salary:.0f} руб/мес"
    phone = profile.corporate_phone or profile.personal_phone or '—'
    email = user.email or '—'

    today = timezone.localdate()
    current_absence = (
        EmployeeAbsence.objects.filter(
            user=user,
            start_date__lte=today,
            end_date__gte=today,
        )
        .order_by('-start_date')
        .first()
    )
    if not user.is_active:
        status_code = 'inactive'
    elif current_absence:
        status_code = current_absence.absence_type
    else:
        status_code = 'active'

    status_labels = {
        'active': 'Активен',
        'vacation': 'В отпуске',
        'sick': 'Больничный',
        'inactive': 'Неактивен',
    }
    status_classes = {
        'active': 'success',
        'vacation': 'warning',
        'sick': 'danger',
        'inactive': 'muted',
    }

    back_url = _resolve_back_url(request, reverse("manager-employees"))
    return render(
        request,
        'dashboard/manager/employee_detail.html',
        {
            'active_tab': 'employees',
            'back_url': back_url,
            'employee': {
                'id': user.id,
                'full_name': full_name,
                'position': position,
                'department_name': profile.department.name if profile.department else '—',
                'phone': phone,
                'email': email,
                'salary_display': salary_display,
                'status_code': status_code,
                'status_label': status_labels.get(status_code, 'Активен'),
                'status_class': status_classes.get(status_code, 'success'),
                'is_active': user.is_active,
            },
        },
    )


@login_required
def manager_chat(request):
    return _render_manager_page(
        request,
        'dashboard/manager/chat.html',
        'chat',
        'Чат с сотрудниками',
        'Внутренние обсуждения и быстрые ответы',
    )
