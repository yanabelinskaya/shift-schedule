from datetime import date, time, timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.contrib.auth import get_user_model
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from .models import (
    Department,
    DepartmentPosition,
    EmployeeAvailability,
    EmployeeAbsence,
    EmployeeProfile,
    SystemBackup,
    SystemLogEntry,
)
from .system_utils import collect_runtime_metrics, format_bytes

def _ensure_role(request, role):
    if getattr(request.user, 'role', None) != role:
        raise PermissionDenied


def _resolve_back_url(request, fallback_url):
    next_url = (request.GET.get("next") or "").strip()
    if next_url and url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return fallback_url


def _render_admin_page(request, template_name, active_tab, page_title, page_subtitle):
    _ensure_role(request, 'admin')
    return render(
        request,
        template_name,
        {
            'active_tab': active_tab,
            'page_title': page_title,
            'page_subtitle': page_subtitle,
        },
    )


def _render_employee_page(request, template_name, active_tab, page_title, page_subtitle):
    _ensure_role(request, 'employee')
    next_slot_context = _get_employee_next_slot_context(request.user)
    return render(
        request,
        template_name,
        {
            'active_tab': active_tab,
            'page_title': page_title,
            'page_subtitle': page_subtitle,
            **next_slot_context,
        },
    )


def _render_manager_page(request, template_name, active_tab, page_title, page_subtitle):
    _ensure_role(request, 'manager')
    return render(
        request,
        template_name,
        {
            'active_tab': active_tab,
            'page_title': page_title,
            'page_subtitle': page_subtitle,
        },
    )


def _get_employee_next_slot_context(user):
    today = timezone.localdate()
    now_time = timezone.localtime().time()
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
    weekday_names = [
        "Понедельник",
        "Вторник",
        "Среда",
        "Четверг",
        "Пятница",
        "Суббота",
        "Воскресенье",
    ]

    def format_full_name(manager):
        if not manager:
            return "—"
        full_name = " ".join(
            part for part in [manager.last_name, manager.first_name] if part
        ).strip()
        return full_name or manager.username

    profile = getattr(user, "profile", None)
    department_manager = None
    if profile and profile.department and profile.department.manager:
        department_manager = profile.department.manager
    department_manager_label = format_full_name(department_manager)
    has_department_manager = bool(department_manager)
    default_manager_line = f"Менеджер: {department_manager_label}" if has_department_manager else ""
    base_context = {
        "department_manager_label": department_manager_label,
        "has_department_manager": has_department_manager,
    }

    def format_day_label(day_date):
        if day_date == today:
            return "Сегодня"
        if day_date == today + timedelta(days=1):
            return "Завтра"
        return f"{weekday_names[day_date.weekday()]}, {day_date.day} {month_names[day_date.month - 1]}"

    current_absences = EmployeeAbsence.objects.filter(
        user=user,
        start_date__lte=today,
        end_date__gte=today,
    )
    current_absence = None
    if current_absences.exists():
        current_absence = (
            current_absences.filter(absence_type="sick").first()
            or current_absences.first()
        )

    upcoming_absences = list(
        EmployeeAbsence.objects.filter(user=user, end_date__gte=today).order_by("start_date")
    )

    def absence_for_date(day_date):
        for absence in upcoming_absences:
            if absence.start_date <= day_date <= absence.end_date:
                return absence
        return None

    next_absence = (
        EmployeeAbsence.objects.filter(user=user, start_date__gt=today)
        .order_by("start_date")
        .first()
    )

    candidate_entries = (
        EmployeeAvailability.objects.select_related("approved_by")
        .filter(user=user, is_available=True, date__gte=today)
        .order_by("date", "start_time")
    )
    next_entry = None
    for entry in candidate_entries:
        if entry.date == today and entry.end_time and entry.end_time <= now_time:
            continue
        if absence_for_date(entry.date):
            continue
        next_entry = entry
        break

    if next_entry and (not next_absence or next_entry.date < next_absence.start_date):
        event_date = next_entry.date
        day_label = format_day_label(event_date)
        time_label = f"{next_entry.start_time:%H:%M}-{next_entry.end_time:%H:%M}"
        week_start = event_date - timedelta(days=event_date.weekday())
        week_end = week_start + timedelta(days=6)
        if profile and profile.department_id:
            week_has_approved = EmployeeAvailability.objects.filter(
                user__profile__department_id=profile.department_id,
                date__range=(week_start, week_end),
                is_approved=True,
            ).exists()
        else:
            week_has_approved = EmployeeAvailability.objects.filter(
                user=user,
                date__range=(week_start, week_end),
                is_approved=True,
            ).exists()
        status_label = (
            "Подтверждена"
            if (next_entry.is_approved or week_has_approved)
            else "На согласовании"
        )
        manager_label = format_full_name(next_entry.approved_by or department_manager)
        return {
            "next_slot_title": f"{day_label}, {time_label}",
            "next_slot_status": f"Статус: {status_label}",
            "next_slot_manager": f"Менеджер: {manager_label}",
            "next_slot_is_empty": False,
            **base_context,
        }

    if current_absence:
        event_date = today
        absence_type = current_absence.absence_type
        absence_label = "Больничный" if absence_type == "sick" else "Отпуск"
        return {
            "next_slot_title": f"{format_day_label(event_date)}, {absence_label}",
            "next_slot_status": absence_label,
            "next_slot_manager": default_manager_line,
            "next_slot_is_empty": False,
            **base_context,
        }

    if next_absence:
        event_date = next_absence.start_date
        absence_type = next_absence.absence_type
        absence_label = "Больничный" if absence_type == "sick" else "Отпуск"
        return {
            "next_slot_title": f"{format_day_label(event_date)}, {absence_label}",
            "next_slot_status": absence_label,
            "next_slot_manager": default_manager_line,
            "next_slot_is_empty": False,
            **base_context,
        }

    return {
        "next_slot_title": "Пока нет слотов",
        "next_slot_status": "Нет данных",
        "next_slot_manager": default_manager_line,
        "next_slot_is_empty": True,
        **base_context,
    }


def _get_manager_department(manager):
    department = Department.objects.filter(manager=manager, is_archived=False).first()
    if not department:
        profile = getattr(manager, 'profile', None)
        if profile and profile.department:
            department = profile.department
    return department


def _build_task_form_context(
    department,
    base_date,
    initial=None,
    error_message="",
    page_title="",
    page_subtitle="",
    form_title="",
    form_subtitle="",
    submit_label="",
    back_url="",
):
    today_local = timezone.localdate()

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

    week_start = base_date - timedelta(days=base_date.weekday())
    week_end = week_start + timedelta(days=6)
    current_week_start = today_local - timedelta(days=today_local.weekday())
    week_offset = (week_start - current_week_start).days // 7

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
    profiles = (
        EmployeeProfile.objects.select_related("user")
        .filter(department=department, user__role="employee")
        .order_by("user__last_name", "user__first_name", "user__username")
        if department
        else EmployeeProfile.objects.none()
    )
    for profile in profiles:
        user = profile.user
        full_name = user.get_full_name().strip() or user.username
        employees.append({"id": user.id, "name": full_name})

    time_options = []
    for hour in range(24):
        for minute in (0, 30):
            time_options.append(f"{hour:02d}:{minute:02d}")

    return {
        "active_tab": "tasks",
        "page_title": page_title or "Новая задача",
        "page_subtitle": page_subtitle or "Назначьте задачу и параметры исполнения",
        "form_title": form_title or "Новая задача",
        "form_subtitle": form_subtitle or "Заполните детали и назначьте исполнителя.",
        "submit_label": submit_label or "Создать задачу",
        "department_name": department.name if department else "—",
        "period_label": period_label,
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "week_offset": week_offset,
        "days": days,
        "employees": employees,
        "time_options": time_options,
        "initial": initial or {},
        "error_message": error_message,
        "back_url": back_url or reverse("manager-tasks"),
    }


def _parse_time_value(value):
    if not value:
        return None
    try:
        hours, minutes = str(value).strip().split(":")[:2]
        return time(int(hours), int(minutes))
    except (ValueError, TypeError):
        return None


def _system_monitoring_snapshot():
    now = timezone.now()
    online_window = now - timedelta(minutes=5)
    rpm_window = now - timedelta(minutes=1)
    errors_window = now - timedelta(hours=24)
    User = get_user_model()

    online_users = (
        SystemLogEntry.objects.filter(created_at__gte=online_window, user__isnull=False)
        .values("user_id")
        .distinct()
        .count()
    )
    requests_per_minute = SystemLogEntry.objects.filter(created_at__gte=rpm_window).count()
    errors_24h = SystemLogEntry.objects.filter(created_at__gte=errors_window, level="error").count()

    last_login = User.objects.exclude(last_login__isnull=True).order_by("-last_login").first()
    last_login_time = last_login.last_login if last_login else None
    last_login_user = last_login.get_full_name() if last_login else ""
    if last_login and not last_login_user:
        last_login_user = last_login.username

    runtime_metrics = collect_runtime_metrics()
    last_backup = SystemBackup.objects.filter(status="ready").order_by("-created_at").first()

    return {
        "online_users": online_users,
        "requests_per_minute": requests_per_minute,
        "errors_24h": errors_24h,
        "last_login": last_login_time,
        "last_login_user": last_login_user,
        "cpu_load_percent_1m": runtime_metrics.get("cpu_load_percent_1m"),
        "memory_used_percent": runtime_metrics.get("memory_used_percent"),
        "memory_used_bytes": runtime_metrics.get("memory_used_bytes"),
        "memory_total_bytes": runtime_metrics.get("memory_total_bytes"),
        "disk_used_percent": runtime_metrics.get("disk_used_percent"),
        "disk_used_bytes": runtime_metrics.get("disk_used_bytes"),
        "disk_total_bytes": runtime_metrics.get("disk_total_bytes"),
        "last_backup": last_backup,
        "updated_at": now,
    }


def _serialize_backup(backup):
    if not backup:
        return None
    return {
        "id": backup.id,
        "file_name": backup.file_name,
        "created_at": timezone.localtime(backup.created_at).isoformat(),
        "file_size": backup.file_size,
        "size_display": format_bytes(backup.file_size),
        "status": backup.status,
        "status_label": backup.get_status_display(),
        "source": backup.source,
        "source_label": backup.get_source_display(),
        "download_url": f"/dashboard/admin/system/backups/{backup.id}/download/",
        "restore_url": f"/dashboard/admin/system/backups/{backup.id}/restore/",
    }


def _serialize_log_entry(entry):
    if not entry:
        return None
    user_name = "Гость"
    if entry.user:
        user_name = entry.user.get_full_name() or entry.user.username
    return {
        "id": entry.id,
        "created_at": timezone.localtime(entry.created_at).isoformat(),
        "action": entry.action,
        "level": entry.level,
        "level_label": entry.get_level_display(),
        "status_code": entry.status_code,
        "method": entry.method,
        "path": entry.path,
        "user_name": user_name,
        "duration_ms": entry.duration_ms,
    }


def _get_department_positions():
    if DepartmentPosition.objects.exists():
        department_positions = {}
        positions = DepartmentPosition.objects.select_related('department').order_by('department__name', 'title')
        for position in positions:
            department_positions.setdefault(position.department.name, []).append(position.title)
        return department_positions
    department_positions = getattr(settings, 'DEPARTMENT_POSITIONS', None)
    if isinstance(department_positions, dict) and department_positions:
        return department_positions
    department_positions = {}
    profile_positions = (
        EmployeeProfile.objects.select_related('department')
        .exclude(department__isnull=True)
        .exclude(position='')
        .values_list('department__name', 'position')
    )
    for department_name, position in profile_positions:
        department_positions.setdefault(department_name, set()).add(position)
    return {
        department_name: sorted(list(positions))
        for department_name, positions in department_positions.items()
    }
