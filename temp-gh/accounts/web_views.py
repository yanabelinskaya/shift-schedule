import csv
import io
import json
from datetime import date, time, timedelta
from pathlib import Path
from decimal import Decimal, InvalidOperation

from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.core.management import call_command
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Avg, Count, Max
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from .models import (
    Department,
    DepartmentPosition,
    EmployeeAvailability,
    EmployeeAbsence,
    EmployeeProfile,
    EmployeeShiftRequest,
    GlobalSettings,
    GlobalSettingsChange,
    PasswordResetRequest,
    SystemBackup,
    SystemLogEntry,
)
from .system_utils import create_backup, format_bytes, log_system_event, maybe_create_daily_backup


def _dashboard_name_for_role(role):
    return {
        'admin': 'admin-dashboard',
        'manager': 'manager-dashboard',
        'employee': 'employee-dashboard',
    }.get(role, 'login')


def login_view(request):
    if request.method == 'GET':
        error = request.session.pop('login_error', None)
        return render(request, 'auth/login.html', {'error': error} if error else {})

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        user = authenticate(request, username=username, password=password)
        if user is None:
            User = get_user_model()
            inactive_user = User.objects.filter(username__iexact=username, is_active=False).first()
            if inactive_user:
                request.session['login_error'] = 'Ваш аккаунт деактивирован.'
                return redirect('login')
            request.session['login_error'] = 'Неверный логин или пароль.'
            return redirect('login')
        if not user.is_active:
            request.session['login_error'] = 'Ваш аккаунт деактивирован.'
            return redirect('login')
        login(request, user)
        return redirect(_dashboard_name_for_role(getattr(user, 'role', None)))
    return render(request, 'auth/login.html')


@login_required
def logout_view(request):
    logout(request)
    return redirect('login')


def _ensure_role(request, role):
    if getattr(request.user, 'role', None) != role:
        raise PermissionDenied


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
    return render(
        request,
        template_name,
        {
            'active_tab': active_tab,
            'page_title': page_title,
            'page_subtitle': page_subtitle,
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

    last_backup = SystemBackup.objects.filter(status="ready").order_by("-created_at").first()

    return {
        "online_users": online_users,
        "requests_per_minute": requests_per_minute,
        "errors_24h": errors_24h,
        "last_login": last_login_time,
        "last_login_user": last_login_user,
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


@login_required
def admin_dashboard(request):
    return admin_users(request)


@login_required
def admin_users(request):
    _ensure_role(request, 'admin')
    User = get_user_model()
    users = list(
        User.objects.exclude(role='admin')
        .order_by('last_name', 'first_name', 'username')
    )
    profiles = EmployeeProfile.objects.select_related('department').filter(user__in=users).in_bulk(field_name='user_id')
    employees = []
    for user in users:
        profile = profiles.get(user.id)
        middle_name = profile.middle_name if profile else ''
        full_name = " ".join(
            part for part in [user.last_name, user.first_name, middle_name] if part
        ).strip() or user.username
        department_name = profile.department.name if profile and profile.department else ''
        employees.append(
            {
                'id': user.id,
                'full_name': full_name,
                'email': user.email or '—',
                'role_code': user.role,
                'role_display': user.get_role_display(),
                'department': department_name or '—',
                'department_value': department_name,
                'position': profile.position if profile else '',
                'corporate_phone': profile.corporate_phone if profile else '',
                'hourly_rate': profile.hourly_rate,
                'is_active': user.is_active,
                'status_label': 'Активен' if user.is_active else 'Деактивирован',
                'status_class': 'success' if user.is_active else 'warning',
            }
        )

    total_users = len(users)
    active_users = sum(1 for user in users if user.is_active)
    inactive_users = total_users - active_users
    departments = list(Department.objects.order_by('name'))
    reset_requests = list(
        PasswordResetRequest.objects.filter(status='pending')
        .select_related('user')
        .order_by('-created_at')
    )
    reset_request_items = [
        {
            'id': req.id,
            'email': req.email,
            'full_name': req.user.get_full_name() or req.user.username,
            'created_at': req.created_at,
        }
        for req in reset_requests
    ]

    department_positions = _get_department_positions()
    return render(
        request,
        'dashboard/admin/users.html',
        {
            'active_tab': 'users',
            'page_title': 'Пользователи',
            'page_subtitle': 'Управление ролями и доступом',
            'employees': employees,
            'departments': departments,
            'reset_requests': reset_request_items,
            'department_positions': department_positions,
            'stats': {
                'total_users': total_users,
                'active_users': active_users,
                'inactive_users': inactive_users,
                'departments_total': len(departments),
                'pending_requests': len(reset_request_items),
                'last_updated': timezone.localtime().strftime('%H:%M'),
            },
        },
    )


@login_required
@ensure_csrf_cookie
def admin_user_detail(request, user_id):
    _ensure_role(request, 'admin')
    User = get_user_model()
    user = get_object_or_404(User, id=user_id)
    if user.role == 'admin':
        raise PermissionDenied
    profile = getattr(user, 'profile', None)
    department = profile.department.name if profile and profile.department else '—'
    departments = Department.objects.order_by('name')
    department_positions = _get_department_positions()
    avatar_url = ''
    if profile and profile.avatar:
        avatar_url = request.build_absolute_uri(profile.avatar.url)
    employee = {
        'id': user.id,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'middle_name': getattr(profile, 'middle_name', '') if profile else '',
        'full_name': user.get_full_name() or user.username,
        'email': user.email,
        'role': user.role,
        'role_display': user.get_role_display(),
        'department': department,
        'department_id': profile.department_id if profile and profile.department_id else None,
        'position': getattr(profile, 'position', ''),
        'position_display': 'Менеджер' if user.role == 'manager' else getattr(profile, 'position', ''),
        'corporate_phone': getattr(profile, 'corporate_phone', ''),
        'personal_phone': getattr(profile, 'personal_phone', ''),
        'address': getattr(profile, 'address', ''),
        'hourly_rate': getattr(profile, 'hourly_rate', None),
        'hourly_rate_reason': getattr(profile, 'hourly_rate_reason', ''),
        'passport_series': getattr(profile, 'passport_series', ''),
        'passport_number': getattr(profile, 'passport_number', ''),
        'passport_issued_by': getattr(profile, 'passport_issued_by', ''),
        'passport_issue_date': profile.passport_issue_date.strftime('%d.%m.%Y')
        if profile and profile.passport_issue_date
        else '',
        'snils': getattr(profile, 'snils', ''),
        'inn': getattr(profile, 'inn', ''),
        'avatar_url': avatar_url,
        'status_label': 'Активен' if user.is_active else 'Неактивен',
        'status_class': 'success' if user.is_active else 'warning',
        'is_active': user.is_active,
    }
    return render(
        request,
        'dashboard/admin/user_detail.html',
        {
            'active_tab': 'users',
            'page_title': 'Карточка сотрудника',
            'employee': employee,
            'departments': departments,
            'department_positions': department_positions,
        },
    )


@login_required
@ensure_csrf_cookie
def profile_view(request):
    user = request.user
    profile = getattr(user, 'profile', None)
    department = profile.department.name if profile and profile.department else '—'
    avatar_url = ''
    if profile and profile.avatar:
        avatar_url = request.build_absolute_uri(profile.avatar.url)
    departments = Department.objects.order_by('name')
    department_positions = _get_department_positions()

    employee = {
        'id': user.id,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'middle_name': getattr(profile, 'middle_name', '') if profile else '',
        'full_name': user.get_full_name() or user.username,
        'email': user.email,
        'role': user.role,
        'role_display': user.get_role_display(),
        'department': department,
        'department_id': profile.department_id if profile and profile.department_id else None,
        'position': getattr(profile, 'position', ''),
        'position_display': 'Менеджер' if user.role == 'manager' else getattr(profile, 'position', ''),
        'corporate_phone': getattr(profile, 'corporate_phone', ''),
        'personal_phone': getattr(profile, 'personal_phone', ''),
        'address': getattr(profile, 'address', ''),
        'hourly_rate': getattr(profile, 'hourly_rate', None),
        'hourly_rate_reason': getattr(profile, 'hourly_rate_reason', ''),
        'passport_series': getattr(profile, 'passport_series', ''),
        'passport_number': getattr(profile, 'passport_number', ''),
        'passport_issued_by': getattr(profile, 'passport_issued_by', ''),
        'passport_issue_date': profile.passport_issue_date.strftime('%d.%m.%Y')
        if profile and profile.passport_issue_date
        else '',
        'snils': getattr(profile, 'snils', ''),
        'inn': getattr(profile, 'inn', ''),
        'avatar_url': avatar_url,
        'status_label': 'Активен' if user.is_active else 'Неактивен',
        'status_class': 'success' if user.is_active else 'warning',
        'is_active': user.is_active,
    }

    role = getattr(user, 'role', 'employee')
    base_template = {
        'admin': 'dashboard/admin.html',
        'manager': 'dashboard/manager.html',
        'employee': 'dashboard/employee.html',
    }.get(role, 'dashboard/employee.html')

    back_url = {
        'admin': 'admin-dashboard',
        'manager': 'manager-dashboard',
        'employee': 'employee-dashboard',
    }.get(role, 'employee-dashboard')

    return render(
        request,
        'dashboard/profile.html',
        {
            'active_tab': '',
            'page_title': 'Мой профиль',
            'employee': employee,
            'base_template': base_template,
            'back_url': back_url,
            'departments': departments,
            'department_positions': department_positions,
        },
    )


@login_required
def admin_departments(request):
    _ensure_role(request, 'admin')
    User = get_user_model()
    departments = (
        Department.objects.select_related('manager')
        .prefetch_related('positions')
        .annotate(
            employees_count=Count('employees', distinct=True),
            positions_count=Count('positions', distinct=True),
        )
        .order_by('name')
    )
    managers = User.objects.filter(role='manager', is_active=True).order_by(
        'last_name',
        'first_name',
        'username',
    )
    departments_data = [
        {
            'id': department.id,
            'name': department.name,
            'manager_id': department.manager_id,
            'manager_name': department.manager.get_full_name() or department.manager.username
            if department.manager
            else '',
            'positions': list(
                department.positions.order_by('title').values_list('title', flat=True)
            ),
            'positions_count': getattr(department, 'positions_count', 0) or 0,
            'employees_count': getattr(department, 'employees_count', 0) or 0,
            'is_archived': department.is_archived,
        }
        for department in departments
    ]
    total_departments = len(departments_data)
    total_positions = DepartmentPosition.objects.count()
    total_employees = EmployeeProfile.objects.exclude(user__role='admin').count()
    assigned_managers = sum(1 for department in departments_data if department['manager_id'])
    stats = {
        'total_departments': total_departments,
        'total_positions': total_positions,
        'total_employees': total_employees,
        'assigned_managers': assigned_managers,
        'last_updated': timezone.localtime().strftime('%d.%m.%Y %H:%M'),
    }
    return render(
        request,
        'dashboard/admin/departments.html',
        {
            'active_tab': 'departments',
            'page_title': 'Отделы',
            'page_subtitle': 'Правила и лимиты по командам',
            'departments': departments,
            'departments_data': departments_data,
            'managers': managers,
            'stats': stats,
        },
    )


@login_required
def admin_department_detail(request, department_id):
    _ensure_role(request, 'admin')
    department = get_object_or_404(
        Department.objects.select_related('manager'),
        id=department_id,
    )
    positions = list(department.positions.order_by('title'))
    if not positions:
        fallback_positions = (
            EmployeeProfile.objects.filter(department=department)
            .exclude(position='')
            .order_by('position')
            .values_list('position', flat=True)
            .distinct()
        )
        positions = [{'title': title} for title in fallback_positions]
    positions_values = [position['title'] if isinstance(position, dict) else position.title for position in positions]
    User = get_user_model()
    managers = User.objects.filter(role='manager', is_active=True).order_by(
        'last_name',
        'first_name',
        'username',
    )
    users = (
        User.objects.exclude(role__in=['admin', 'manager'])
        .select_related('profile')
        .filter(profile__department=department)
        .order_by('last_name', 'first_name', 'username')
    )
    employees = []
    for user in users:
        profile = getattr(user, 'profile', None)
        employees.append(
            {
                'id': user.id,
                'full_name': user.get_full_name() or user.username,
                'email': user.email,
                'role_display': user.get_role_display(),
                'position': getattr(profile, 'position', '') if profile else '',
                'hourly_rate': getattr(profile, 'hourly_rate', None) if profile else None,
                'is_active': user.is_active,
                'status_label': 'Активен' if user.is_active else 'Неактивен',
                'status_class': 'success' if user.is_active else 'warning',
            }
        )
    available_users = (
        User.objects.exclude(role__in=['admin', 'manager'])
        .exclude(profile__department=department)
        .order_by('last_name', 'first_name', 'username')
    )
    available_employees = [
        {
            'id': user.id,
            'full_name': user.get_full_name() or user.username,
            'email': user.email or '—',
        }
        for user in available_users
    ]
    return render(
        request,
        'dashboard/admin/department_detail.html',
        {
            'active_tab': 'departments',
            'page_title': department.name,
            'page_subtitle': 'Карточка отдела',
            'department': department,
            'positions': positions,
            'positions_values': positions_values,
            'employees': employees,
            'available_employees': available_employees,
            'managers': managers,
            'transfer_departments': Department.objects.exclude(id=department_id).order_by('name'),
        },
    )


@login_required
@ensure_csrf_cookie
def admin_settings(request):
    _ensure_role(request, 'admin')
    settings_obj = GlobalSettings.objects.first()
    if not settings_obj:
        settings_obj = GlobalSettings.objects.create()

    shift_templates = settings_obj.shift_templates or []
    work_days_label = dict(GlobalSettings.WORK_DAYS_CHOICES).get(
        settings_obj.work_days,
        "Ежедневно",
    )
    time_options = [f"{hour:02d}:00" for hour in range(24)]

    ot_coeff_value = f"{settings_obj.ot_coeff:.1f}"
    return render(
        request,
        'dashboard/admin/settings.html',
        {
            'active_tab': 'settings',
            'page_title': 'Глобальные настройки',
            'page_subtitle': 'Рабочее время и правила планирования',
            'settings_data': {
                'work_start': settings_obj.work_start.strftime("%H:%M"),
                'work_end': settings_obj.work_end.strftime("%H:%M"),
                'work_days': settings_obj.work_days,
                'work_days_label': work_days_label,
                'ot_threshold': settings_obj.ot_threshold,
                'ot_coeff': ot_coeff_value,
                'ot_coeff_display': ot_coeff_value.replace(".", ","),
                'shift_templates': shift_templates,
                'allow_custom_shifts': settings_obj.allow_custom_shifts,
                'updated_at': settings_obj.updated_at,
            },
            'time_options': time_options,
            'work_days_options': GlobalSettings.WORK_DAYS_CHOICES,
            'shift_templates_json': json.dumps(shift_templates, ensure_ascii=False),
        },
    )


@login_required
@require_http_methods(["POST"])
def admin_settings_update(request):
    _ensure_role(request, 'admin')
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": "Некорректный формат данных."}, status=400)

    settings_obj = GlobalSettings.objects.first()
    if not settings_obj:
        settings_obj = GlobalSettings.objects.create()

    def parse_time(value, fallback):
        if not value:
            return fallback
        try:
            hours, minutes = value.split(":")[:2]
            return time(int(hours), int(minutes))
        except (ValueError, TypeError):
            return fallback

    before = {
        "work_start": settings_obj.work_start.strftime("%H:%M"),
        "work_end": settings_obj.work_end.strftime("%H:%M"),
        "work_days": settings_obj.work_days,
        "ot_threshold": settings_obj.ot_threshold,
        "ot_coeff": f"{settings_obj.ot_coeff:.1f}",
        "shift_templates": settings_obj.shift_templates or [],
        "allow_custom_shifts": settings_obj.allow_custom_shifts,
    }

    settings_obj.work_start = parse_time(payload.get("work_start"), settings_obj.work_start)
    settings_obj.work_end = parse_time(payload.get("work_end"), settings_obj.work_end)
    work_days = payload.get("work_days") or settings_obj.work_days
    if work_days not in dict(GlobalSettings.WORK_DAYS_CHOICES):
        work_days = settings_obj.work_days
    settings_obj.work_days = work_days
    try:
        threshold = int(payload.get("ot_threshold") or settings_obj.ot_threshold)
        if threshold > 0:
            settings_obj.ot_threshold = threshold
    except (TypeError, ValueError):
        pass
    coeff_value = payload.get("ot_coeff")
    if coeff_value is not None:
        coeff_text = str(coeff_value).replace(",", ".").strip()
        if coeff_text:
            try:
                settings_obj.ot_coeff = Decimal(coeff_text)
            except (InvalidOperation, ValueError):
                pass
    shift_templates = payload.get("shift_templates")
    if isinstance(shift_templates, list):
        settings_obj.shift_templates = [str(item).strip() for item in shift_templates if str(item).strip()]
    allow_custom = payload.get("allow_custom_shifts")
    if isinstance(allow_custom, bool):
        settings_obj.allow_custom_shifts = allow_custom

    settings_obj.updated_by = request.user
    settings_obj.save()

    after = {
        "work_start": settings_obj.work_start.strftime("%H:%M"),
        "work_end": settings_obj.work_end.strftime("%H:%M"),
        "work_days": settings_obj.work_days,
        "ot_threshold": settings_obj.ot_threshold,
        "ot_coeff": f"{settings_obj.ot_coeff:.1f}",
        "shift_templates": settings_obj.shift_templates or [],
        "allow_custom_shifts": settings_obj.allow_custom_shifts,
    }

    changes = []
    if before["work_start"] != after["work_start"] or before["work_end"] != after["work_end"]:
        changes.append(f"{after['work_start']}-{after['work_end']}")
    if before["work_days"] != after["work_days"]:
        changes.append(dict(GlobalSettings.WORK_DAYS_CHOICES).get(after["work_days"], after["work_days"]))
    if before["ot_threshold"] != after["ot_threshold"] or before["ot_coeff"] != after["ot_coeff"]:
        changes.append(f">{after['ot_threshold']} ч/день · {after['ot_coeff']}x")
    if before["shift_templates"] != after["shift_templates"]:
        changes.append(f"Типы смен: {len(after['shift_templates'])}")
    if before["allow_custom_shifts"] != after["allow_custom_shifts"]:
        changes.append(
            "Разрешены кастомные смены" if after["allow_custom_shifts"] else "Кастомные смены отключены"
        )

    if changes:
        GlobalSettingsChange.objects.create(
            admin=request.user,
            summary="Обновлены глобальные настройки",
            details="Изменения сохранены через панель администратора.",
            changes=changes,
            payload=after,
        )

    return JsonResponse(
        {
            "ok": True,
            "updated_at": timezone.localtime(settings_obj.updated_at).isoformat(),
            "settings": after,
        }
    )


@login_required
@require_http_methods(["GET"])
def admin_settings_history(request):
    _ensure_role(request, 'admin')
    changes = GlobalSettingsChange.objects.filter(admin=request.user).order_by("-created_at")[:25]
    history = []
    for change in changes:
        history.append(
            {
                "date": timezone.localtime(change.created_at).isoformat(),
                "author": change.admin.get_full_name() or change.admin.username,
                "title": change.summary,
                "details": change.details,
                "changes": change.changes,
            }
        )
    return JsonResponse({"history": history})


@login_required
def admin_reports(request):
    _ensure_role(request, 'admin')
    User = get_user_model()
    departments = (
        Department.objects.select_related('manager')
        .annotate(
            employees_count=Count('employees', distinct=True),
            positions_count=Count('positions', distinct=True),
        )
        .order_by('name')
    )
    total_departments = departments.count()
    active_departments = departments.filter(is_archived=False).count()
    archived_departments = total_departments - active_departments
    assigned_managers = departments.filter(is_archived=False, manager__isnull=False).count()
    departments_without_manager = departments.filter(is_archived=False, manager__isnull=True).count()

    total_users = User.objects.exclude(role='admin').count()
    active_users = User.objects.exclude(role='admin').filter(is_active=True).count()
    inactive_users = total_users - active_users
    managers_total = User.objects.filter(role='manager').count()
    total_positions = DepartmentPosition.objects.count()

    rate_stats = (
        EmployeeProfile.objects.exclude(user__role='admin')
        .exclude(hourly_rate__isnull=True)
        .aggregate(avg_rate=Avg('hourly_rate'), max_rate=Max('hourly_rate'))
    )
    avg_rate = rate_stats.get('avg_rate')
    max_rate = rate_stats.get('max_rate')

    def format_rate(value):
        if value is None:
            return None
        try:
            return f"{value:.2f}"
        except (TypeError, ValueError):
            return None

    top_departments_qs = (
        departments.filter(is_archived=False)
        .order_by('-employees_count', 'name')
        .values('name', 'employees_count')[:5]
    )
    top_departments = list(top_departments_qs)

    top_positions_qs = (
        EmployeeProfile.objects.exclude(user__role='admin')
        .exclude(position='')
        .values('position')
        .annotate(total=Count('id'))
        .order_by('-total', 'position')[:5]
    )
    top_positions = list(top_positions_qs)

    bar_departments = (
        departments.filter(is_archived=False)
        .order_by('-employees_count', 'name')[:6]
    )
    max_employees = max([dept.employees_count for dept in bar_departments], default=0)
    department_bars = []
    for dept in bar_departments:
        if max_employees:
            height = int(round((dept.employees_count / max_employees) * 100))
        else:
            height = 0
        department_bars.append(
            {
                'name': dept.name,
                'employees_count': dept.employees_count,
                'height': height,
            }
        )

    stats = {
        'total_departments': total_departments,
        'active_departments': active_departments,
        'archived_departments': archived_departments,
        'assigned_managers': assigned_managers,
        'departments_without_manager': departments_without_manager,
        'total_users': total_users,
        'active_users': active_users,
        'inactive_users': inactive_users,
        'managers_total': managers_total,
        'total_positions': total_positions,
        'avg_rate': format_rate(avg_rate),
        'max_rate': format_rate(max_rate),
        'last_updated': timezone.localtime().strftime('%d.%m.%Y %H:%M'),
    }

    print_mode = request.GET.get('print') == '1'

    return render(
        request,
        'dashboard/admin/reports.html',
        {
            'active_tab': 'reports',
            'page_title': 'Отчеты компании',
            'page_subtitle': 'Затраты, загрузка, аналитика',
            'stats': stats,
            'top_departments': top_departments,
            'top_positions': top_positions,
            'department_bars': department_bars,
            'print_mode': print_mode,
        },
    )


@login_required
def admin_reports_export(request):
    _ensure_role(request, 'admin')
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="reports_departments.csv"'
    writer = csv.writer(response)
    writer.writerow(
        [
            'Отдел',
            'Статус',
            'Менеджер',
            'Сотрудников',
            'Должностей',
        ]
    )
    departments = (
        Department.objects.select_related('manager')
        .annotate(
            employees_count=Count('employees', distinct=True),
            positions_count=Count('positions', distinct=True),
        )
        .order_by('name')
    )
    for department in departments:
        manager_name = ''
        if department.manager:
            manager_name = department.manager.get_full_name() or department.manager.username
        status_label = 'Архивный' if department.is_archived else 'Активный'
        writer.writerow(
            [
                department.name,
                status_label,
                manager_name,
                department.employees_count,
                department.positions_count,
            ]
        )
    return response


@login_required
def admin_payroll(request):
    return _render_admin_page(
        request,
        'dashboard/admin/payroll.html',
        'payroll',
        'Payroll',
        'Ведомости и утверждение расчетов',
    )


@login_required
@ensure_csrf_cookie
def admin_system(request):
    _ensure_role(request, 'admin')
    maybe_create_daily_backup()
    monitoring = _system_monitoring_snapshot()

    logs = list(SystemLogEntry.objects.select_related('user').order_by('-created_at')[:12])
    log_entries = []
    for entry in logs:
        user_name = "Гость"
        if entry.user:
            user_name = entry.user.get_full_name() or entry.user.username
        log_entries.append(
            {
                "id": entry.id,
                "created_at": timezone.localtime(entry.created_at),
                "action": entry.action,
                "level": entry.level,
                "level_label": entry.get_level_display(),
                "status_code": entry.status_code,
                "method": entry.method,
                "path": entry.path,
                "user_name": user_name,
            }
        )

    backups = list(SystemBackup.objects.select_related('created_by', 'restored_by').order_by('-created_at')[:10])
    for backup in backups:
        backup.size_display = format_bytes(backup.file_size)

    return render(
        request,
        'dashboard/admin/system.html',
        {
            'active_tab': 'system',
            'page_title': 'Система',
            'page_subtitle': 'Логи, бэкапы, мониторинг',
            'system_logs': log_entries,
            'backups': backups,
            'monitoring': monitoring,
            'last_backup': monitoring.get('last_backup'),
        },
    )


@login_required
@require_http_methods(["POST"])
def admin_system_backup_create(request):
    _ensure_role(request, 'admin')
    try:
        backup = create_backup(created_by=request.user, source="manual")
    except Exception:
        return JsonResponse({"detail": "Не удалось создать бэкап."}, status=500)
    return JsonResponse({"backup": _serialize_backup(backup)})


@login_required
@require_http_methods(["POST"])
def admin_system_backup_restore(request, backup_id):
    _ensure_role(request, 'admin')
    backup = get_object_or_404(SystemBackup, id=backup_id)
    file_path = Path(backup.file_path)
    if not file_path.exists():
        return JsonResponse({"detail": "Файл бэкапа не найден."}, status=404)
    try:
        with transaction.atomic():
            call_command("loaddata", str(file_path), verbosity=0)
        backup.status = "restored"
        backup.restored_at = timezone.now()
        backup.restored_by = request.user
        backup.save(update_fields=["status", "restored_at", "restored_by"])
        log_system_event(
            f"Восстановлен бэкап {backup.file_name}",
            user=request.user,
            level="warning",
        )
        return JsonResponse({"detail": "Бэкап восстановлен.", "backup": _serialize_backup(backup)})
    except Exception as exc:
        log_system_event(
            f"Ошибка восстановления бэкапа {backup.file_name}",
            user=request.user,
            level="error",
            error=str(exc),
        )
        return JsonResponse({"detail": "Не удалось восстановить бэкап."}, status=500)


@login_required
@require_http_methods(["GET"])
def admin_system_backup_download(request, backup_id):
    _ensure_role(request, 'admin')
    backup = get_object_or_404(SystemBackup, id=backup_id)
    file_path = Path(backup.file_path)
    if not file_path.exists():
        raise Http404("Backup file not found.")
    return FileResponse(
        open(file_path, "rb"),
        as_attachment=True,
        filename=backup.file_name,
    )


@login_required
@require_http_methods(["GET"])
def admin_system_logs_export(request):
    _ensure_role(request, 'admin')
    export_format = (request.GET.get("format") or "csv").lower()
    logs = SystemLogEntry.objects.select_related("user").order_by("-created_at")[:2000]
    if export_format == "json":
        return JsonResponse({"logs": [_serialize_log_entry(entry) for entry in logs]})

    output = io.StringIO()
    writer = csv.writer(output)
    output.write("\ufeff")
    writer.writerow(["Время", "Пользователь", "Действие", "Метод", "Путь", "Статус", "Уровень", "IP", "Длительность (мс)"])
    for entry in logs:
        user_name = "Гость"
        if entry.user:
            user_name = entry.user.get_full_name() or entry.user.username
        writer.writerow(
            [
                timezone.localtime(entry.created_at).isoformat(),
                user_name,
                entry.action,
                entry.method,
                entry.path,
                entry.status_code or "",
                entry.get_level_display(),
                entry.ip_address or "",
                entry.duration_ms or "",
            ]
        )
    filename = f"system_logs_{timezone.localdate():%Y%m%d}.csv"
    response = HttpResponse(output.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
@require_http_methods(["GET"])
def admin_system_monitoring(request):
    _ensure_role(request, 'admin')
    snapshot = _system_monitoring_snapshot()
    last_login = snapshot.get("last_login")
    return JsonResponse(
        {
            "online_users": snapshot.get("online_users", 0),
            "requests_per_minute": snapshot.get("requests_per_minute", 0),
            "errors_24h": snapshot.get("errors_24h", 0),
            "last_login": timezone.localtime(last_login).isoformat() if last_login else None,
            "last_login_user": snapshot.get("last_login_user", ""),
            "last_backup": _serialize_backup(snapshot.get("last_backup")),
            "updated_at": timezone.localtime(snapshot.get("updated_at")).isoformat(),
        }
    )


@login_required
def manager_dashboard(request):
    return manager_calendar(request)


@login_required
def manager_calendar(request):
    return _render_manager_page(
        request,
        'dashboard/manager/calendar.html',
        'calendar',
        'Календарь отдела',
        'Планирование смен и контроль нагрузки',
    )


@login_required
def manager_requests(request):
    return _render_manager_page(
        request,
        'dashboard/manager/requests.html',
        'requests',
        'Запросы сотрудников',
        'Изменения смен и подтверждения',
    )


@login_required
def manager_tasks(request):
    return _render_manager_page(
        request,
        'dashboard/manager/tasks.html',
        'tasks',
        'Задачи отдела',
        'Постановка и контроль выполнения',
    )


@login_required
def manager_payroll(request):
    return _render_manager_page(
        request,
        'dashboard/manager/payroll.html',
        'payroll',
        'Зарплата и отчеты',
        'Нагрузка, ставки и выплаты',
    )


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
        rate_display = '—'
        if profile.hourly_rate is not None:
            rate_display = f"{profile.hourly_rate:.0f} руб/час"
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
                'rate_display': rate_display,
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
    rate_display = '—'
    if profile.hourly_rate is not None:
        rate_display = f"{profile.hourly_rate:.0f} руб/час"
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

    return render(
        request,
        'dashboard/manager/employee_detail.html',
        {
            'active_tab': 'employees',
            'employee': {
                'id': user.id,
                'full_name': full_name,
                'position': position,
                'department_name': profile.department.name if profile.department else '—',
                'phone': phone,
                'email': email,
                'rate_display': rate_display,
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
        'Быстрое общение по сменам и задачам',
    )


@login_required
def employee_dashboard(request):
    return employee_schedule(request)


@login_required
def employee_schedule(request):
    return _render_employee_page(
        request,
        'dashboard/employee/schedule.html',
        'schedule',
        'Мой график',
        'Неделя, месяц и детали смен',
    )


@login_required
@ensure_csrf_cookie
def employee_availability(request):
    _ensure_role(request, 'employee')
    today = timezone.localdate()
    week_param = (request.GET.get("week") or "").strip().lower()
    offset_param = (request.GET.get("offset") or "").strip()
    week_offset = 0
    if offset_param:
        try:
            week_offset = int(offset_param)
        except (TypeError, ValueError):
            week_offset = 0
    elif week_param == "next":
        week_offset = 1
    elif week_param == "current":
        week_offset = 0

    week_start = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)
    week_end = week_start + timedelta(days=6)
    is_current_week = week_offset == 0
    is_next_week = week_offset == 1
    current_week_start = today - timedelta(days=today.weekday())
    current_week_friday = current_week_start + timedelta(days=4)
    next_week_edit_closed = is_next_week and today > current_week_friday

    settings_obj = GlobalSettings.objects.first()
    if not settings_obj:
        settings_obj = GlobalSettings.objects.create()

    default_start = settings_obj.work_start
    default_end = settings_obj.work_end

    def is_default_available():
        return False

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

    def format_day_label(day_date):
        return f"{weekday_names[day_date.weekday()]}, {day_date.day} {month_names[day_date.month - 1]}"

    def format_date_short(day_date):
        return f"{day_date.day} {month_names[day_date.month - 1]}"

    if week_start.month == week_end.month:
        period_label = f"{week_start.day}–{week_end.day} {month_names[week_end.month - 1]}"
    else:
        period_label = (
            f"{week_start.day} {month_names[week_start.month - 1]} — "
            f"{week_end.day} {month_names[week_end.month - 1]}"
        )

    base_entries = list(
        EmployeeAvailability.objects.filter(
            user=request.user,
            date__range=(week_start, week_end),
        )
    )
    approved_entries = [entry for entry in base_entries if entry.is_approved]
    if is_current_week:
        entries = approved_entries
    else:
        entries = base_entries
    entries_map = {entry.date: entry for entry in entries}

    def to_minutes(value):
        return value.hour * 60 + value.minute if value else 0

    days = []
    for offset in range(7):
        day_date = week_start + timedelta(days=offset)
        entry = entries_map.get(day_date)
        if entry:
            is_available = entry.is_available
            start_time = entry.start_time
            end_time = entry.end_time
            priority = entry.priority
        else:
            is_available = is_default_available()
            start_time = default_start
            end_time = default_end
            priority = "mid"

        start_value = start_time.strftime("%H:%M") if start_time else ""
        end_value = end_time.strftime("%H:%M") if end_time else ""

        start_minutes = to_minutes(start_time)
        end_minutes = to_minutes(end_time)
        duration_minutes = max(0, end_minutes - start_minutes)

        days.append(
            {
                "date": day_date.isoformat(),
                "weekday": day_date.weekday(),
                "label": format_day_label(day_date),
                "date_label": format_date_short(day_date),
                "weekday_label": weekday_names[day_date.weekday()],
                "is_today": day_date == today,
                "is_available": is_available,
                "start_time": start_value,
                "end_time": end_value,
                "start_minutes": start_minutes,
                "end_minutes": end_minutes,
                "duration_minutes": duration_minutes,
                "priority": priority,
            }
        )

    last_saved = None
    if base_entries:
        last_saved_entry = max(base_entries, key=lambda item: item.updated_at)
        last_saved = timezone.localtime(last_saved_entry.updated_at)

    def format_datetime(dt):
        if not dt:
            return "Еще не сохранено"
        return f"{dt.day} {month_names[dt.month - 1]} {dt.year}, {dt:%H:%M}"

    time_options = []
    for hour in range(24):
        for minute in (0, 30):
            time_options.append(f"{hour:02d}:{minute:02d}")

    priority_options = [
        {"value": "high", "label": "Очень хочу", "tone": "high"},
        {"value": "mid", "label": "Ок", "tone": "mid"},
        {"value": "low", "label": "Не хочу", "tone": "low"},
    ]

    week_is_approved = bool(base_entries) and all(entry.is_approved for entry in base_entries)
    is_locked = is_current_week or week_is_approved or next_week_edit_closed
    show_requests = is_current_week or week_is_approved or next_week_edit_closed
    edit_hint = ""
    if is_next_week:
        edit_hint = (
            "Редактирование следующей недели доступно только до пятницы."
            if next_week_edit_closed
            else "Редактирование доступно до пятницы."
        )

    profile = getattr(request.user, "profile", None)
    hourly_rate = None
    if profile and profile.hourly_rate is not None:
        hourly_rate = profile.hourly_rate

    total_shifts = sum(1 for day in days if day["is_available"])
    total_minutes = sum(day["duration_minutes"] for day in days if day["is_available"])
    total_hours = total_minutes / 60 if total_minutes else 0
    total_hours_display = f"{total_hours:.1f}".replace(".", ",")
    if hourly_rate is not None:
        total_salary = (Decimal(total_minutes) / Decimal(60)) * Decimal(hourly_rate)
        net_salary = total_salary * Decimal("0.87")
        total_salary_display = f"{net_salary:.2f}".replace(".", ",")
    else:
        total_salary_display = "—"

    return render(
        request,
        'dashboard/employee/availability.html',
        {
            'active_tab': 'availability',
            'page_title': 'Доступность',
            'page_subtitle': 'Укажите, когда и как хотите работать',
            'days': days,
            'period_label': period_label,
            'week_offset': week_offset,
            'is_locked': is_locked,
            'show_requests': show_requests,
            'edit_hint': edit_hint,
            'default_start': default_start.strftime("%H:%M"),
            'default_end': default_end.strftime("%H:%M"),
            'time_options': time_options,
            'priority_options': priority_options,
            'last_saved_label': format_datetime(last_saved),
            'has_saved': bool(last_saved),
            'week_is_approved': week_is_approved,
            'summary_shifts': total_shifts,
            'summary_hours': total_hours_display,
            'summary_salary': total_salary_display,
            'hourly_rate': f"{hourly_rate:.2f}" if hourly_rate is not None else "",
        },
    )


@login_required
@require_http_methods(["POST"])
def employee_availability_update(request):
    _ensure_role(request, 'employee')
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": "Некорректный формат данных."}, status=400)

    days_payload = payload.get("days")
    if not isinstance(days_payload, list):
        return JsonResponse({"detail": "Некорректные данные доступности."}, status=400)

    valid_priorities = {choice[0] for choice in EmployeeAvailability.PRIORITY_CHOICES}

    def parse_time(value, fallback):
        if not value:
            return fallback
        try:
            hours, minutes = str(value).split(":")[:2]
            return time(int(hours), int(minutes))
        except (ValueError, TypeError):
            return fallback

    settings_obj = GlobalSettings.objects.first()
    if not settings_obj:
        settings_obj = GlobalSettings.objects.create()
    default_start = settings_obj.work_start
    default_end = settings_obj.work_end

    updates_map = {}
    for item in days_payload:
        if not isinstance(item, dict):
            continue
        date_value = (item.get("date") or "").strip()
        if not date_value:
            continue
        try:
            day_date = date.fromisoformat(date_value)
        except ValueError:
            continue
        is_available = bool(item.get("is_available"))
        start_time = parse_time(item.get("start_time"), default_start)
        end_time = parse_time(item.get("end_time"), default_end)
        priority = item.get("priority") or "mid"
        if priority not in valid_priorities:
            priority = "mid"
        updates_map[day_date] = {
            "date": day_date,
            "is_available": is_available,
            "start_time": start_time,
            "end_time": end_time,
            "priority": priority,
        }

    if not updates_map:
        return JsonResponse({"detail": "Нет данных для сохранения."}, status=400)

    dates = list(updates_map.keys())
    today = timezone.localdate()
    current_week_start = today - timedelta(days=today.weekday())
    current_week_end = current_week_start + timedelta(days=6)
    current_week_friday = current_week_start + timedelta(days=4)
    next_week_start = current_week_end + timedelta(days=1)
    next_week_end = next_week_start + timedelta(days=6)
    if any(current_week_start <= day <= current_week_end for day in dates):
        return JsonResponse({"detail": "Текущая неделя заблокирована для редактирования."}, status=403)

    if EmployeeAvailability.objects.filter(user=request.user, date__in=dates, is_approved=True).exists():
        return JsonResponse({"detail": "График уже подтвержден менеджером."}, status=403)

    if today > current_week_friday and any(next_week_start <= day <= next_week_end for day in dates):
        return JsonResponse(
            {"detail": "Редактирование следующей недели доступно только до пятницы."},
            status=403,
        )

    existing_entries = EmployeeAvailability.objects.filter(user=request.user, date__in=dates)
    existing_map = {entry.date: entry for entry in existing_entries}

    with transaction.atomic():
        for payload_item in updates_map.values():
            entry = existing_map.get(payload_item["date"])
            if entry:
                entry.is_available = payload_item["is_available"]
                entry.start_time = payload_item["start_time"]
                entry.end_time = payload_item["end_time"]
                entry.priority = payload_item["priority"]
                entry.is_approved = False
                entry.approved_by = None
                entry.approved_at = None
                entry.save(
                    update_fields=[
                        "is_available",
                        "start_time",
                        "end_time",
                        "priority",
                        "is_approved",
                        "approved_by",
                        "approved_at",
                        "updated_at",
                    ]
                )
            else:
                EmployeeAvailability.objects.create(
                    user=request.user,
                    date=payload_item["date"],
                    is_available=payload_item["is_available"],
                    start_time=payload_item["start_time"],
                    end_time=payload_item["end_time"],
                    priority=payload_item["priority"],
                )

    updated_at = timezone.localtime(timezone.now())
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
    updated_label = f"{updated_at.day} {month_names[updated_at.month - 1]} {updated_at.year}, {updated_at:%H:%M}"

    return JsonResponse({"ok": True, "updated_at": updated_at.isoformat(), "updated_label": updated_label})


@login_required
@require_http_methods(["POST"])
def employee_shift_request_create(request):
    _ensure_role(request, 'employee')
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": "Некорректный формат данных."}, status=400)

    request_type = (payload.get("request_type") or "").strip()
    valid_types = {choice[0] for choice in EmployeeShiftRequest.REQUEST_TYPES}
    if request_type not in valid_types:
        return JsonResponse({"detail": "Некорректный тип запроса."}, status=400)

    date_value = (payload.get("date") or "").strip()
    if not date_value:
        return JsonResponse({"detail": "Выберите дату."}, status=400)
    try:
        day_date = date.fromisoformat(date_value)
    except ValueError:
        return JsonResponse({"detail": "Некорректная дата."}, status=400)

    reason = (payload.get("reason") or "").strip()
    if not reason:
        return JsonResponse({"detail": "Укажите причину."}, status=400)

    def parse_time(value):
        if not value:
            return None
        try:
            hours, minutes = str(value).split(":")[:2]
            return time(int(hours), int(minutes))
        except (ValueError, TypeError):
            return None

    start_time = parse_time(payload.get("start_time"))
    end_time = parse_time(payload.get("end_time"))

    if request_type == "extra_hours" and (start_time is None or end_time is None):
        return JsonResponse({"detail": "Укажите время дополнительных часов."}, status=400)

    EmployeeShiftRequest.objects.create(
        user=request.user,
        date=day_date,
        request_type=request_type,
        start_time=start_time,
        end_time=end_time,
        reason=reason,
    )

    return JsonResponse({"ok": True})


@login_required
def employee_tasks(request):
    return _render_employee_page(
        request,
        'dashboard/employee/tasks.html',
        'tasks',
        'Задачи',
        'Контроль поручений и история выполнения',
    )


@login_required
def employee_payroll(request):
    return _render_employee_page(
        request,
        'dashboard/employee/payroll.html',
        'payroll',
        'Зарплата',
        'Часы, ставка и расчет выплат',
    )


@login_required
def employee_notifications(request):
    return _render_employee_page(
        request,
        'dashboard/employee/notifications.html',
        'notifications',
        'Уведомления',
        'Все важные события по сменам и задачам',
    )
