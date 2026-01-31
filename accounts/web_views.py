from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.db.models import Count
from django.views.decorators.csrf import ensure_csrf_cookie

from .models import Department, DepartmentPosition, EmployeeProfile, PasswordResetRequest


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
    managers = User.objects.filter(role='manager').order_by('last_name', 'first_name', 'username')
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
    managers = User.objects.filter(role='manager').order_by('last_name', 'first_name', 'username')
    users = (
        User.objects.exclude(role='admin')
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
        User.objects.exclude(role='admin')
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
def admin_settings(request):
    return _render_admin_page(
        request,
        'dashboard/admin/settings.html',
        'settings',
        'Глобальные настройки',
        'Рабочее время и уведомления',
    )


@login_required
def admin_reports(request):
    return _render_admin_page(
        request,
        'dashboard/admin/reports.html',
        'reports',
        'Отчеты компании',
        'Затраты, загрузка, аналитика',
    )


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
def admin_system(request):
    return _render_admin_page(
        request,
        'dashboard/admin/system.html',
        'system',
        'Система',
        'Логи, бэкапы, мониторинг',
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


@login_required
def manager_employees(request):
    return _render_manager_page(
        request,
        'dashboard/manager/employees.html',
        'employees',
        'Сотрудники отдела',
        'Роли, лимиты и комментарии',
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
def employee_availability(request):
    return _render_employee_page(
        request,
        'dashboard/employee/availability.html',
        'availability',
        'Доступность',
        'Укажите, когда и как хотите работать',
    )


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
