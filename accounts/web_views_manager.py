import json
from datetime import date, time, timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from .models import (
    Department,
    DepartmentTask,
    EmployeeAbsence,
    EmployeeProfile,
    LeaveRequest,
    Sprint,
    Substitution,
    TaskReassignment,
    TaskSubmission,
)
from .web_views_shared import (
    _build_task_form_context,
    _ensure_role,
    _get_manager_department,
    _render_manager_page,
    _resolve_back_url,
)

@login_required
def manager_dashboard(request):
    _ensure_role(request, 'manager')
    manager = request.user
    department = _get_manager_department(manager)
    today = timezone.localdate()

    task_stats = {
        'total': 0, 'in_progress': 0, 'no_assignee': 0,
        'on_review': 0, 'completed': 0, 'overdue': 0,
    }
    active_sprint = None
    team_load = []
    absences = {'current': [], 'upcoming': []}
    notifications = []

    if department:
        # ── Task stats ──────────────────────────────────────────────────
        qs_all = DepartmentTask.objects.filter(department=department)
        task_stats['total']       = qs_all.count()
        task_stats['in_progress'] = qs_all.filter(status__in=['in_progress', 'confirmed']).count()
        task_stats['no_assignee'] = qs_all.filter(status='awaiting_confirmation').count()
        task_stats['on_review']   = qs_all.filter(status='on_review').count()
        task_stats['completed']   = qs_all.filter(status='completed').count()
        task_stats['overdue']     = qs_all.exclude(
            status__in=['completed', 'returned', 'conflict']
        ).filter(date__lt=today).count()

        # ── Active sprint ───────────────────────────────────────────────
        sprint = Sprint.objects.filter(department=department, status='active').first()
        if sprint:
            st = DepartmentTask.objects.filter(sprint=sprint)
            st_total = st.count()
            st_done = st.filter(status='completed').count()
            st_no_assignee = st.filter(status='awaiting_confirmation').count()
            total_days = max((sprint.end_date - sprint.start_date).days, 1)
            days_gone = max(min((today - sprint.start_date).days, total_days), 0)
            active_sprint = {
                'id': sprint.id,
                'title': sprint.title,
                'start': sprint.start_date.strftime('%d.%m'),
                'end': sprint.end_date.strftime('%d.%m.%Y'),
                'total': st_total,
                'done': st_done,
                'no_assignee': st_no_assignee,
                'progress': int(st_done / st_total * 100) if st_total else 0,
                'time_progress': int(days_gone / total_days * 100),
                'url': reverse('manager-sprint-detail', args=[sprint.id]),
            }

        # ── Team load ───────────────────────────────────────────────────
        profiles = list(
            EmployeeProfile.objects.select_related('user')
            .filter(department=department, user__role='employee')
            .order_by('user__last_name', 'user__first_name')
        )
        user_ids = [p.user_id for p in profiles]

        active_task_counts = {}
        if user_ids:
            for row in DepartmentTask.objects.filter(
                taken_by_id__in=user_ids,
                status__in=['confirmed', 'in_progress', 'on_review'],
            ).values('taken_by_id'):
                uid = row['taken_by_id']
                active_task_counts[uid] = active_task_counts.get(uid, 0) + 1

        current_absent_ids = set()
        if user_ids:
            for row in EmployeeAbsence.objects.filter(
                user_id__in=user_ids, start_date__lte=today, end_date__gte=today
            ).values('user_id'):
                current_absent_ids.add(row['user_id'])

        for profile in profiles:
            user = profile.user
            full_name = user.get_full_name().strip() or user.username
            tasks = active_task_counts.get(user.id, 0)
            if user.id in current_absent_ids:
                load_code, load_label = 'absent', 'Отсутствует'
            elif tasks == 0:
                load_code, load_label = 'free', 'Свободен'
            elif tasks <= 2:
                load_code, load_label = 'normal', 'Нормальная загрузка'
            elif tasks <= 4:
                load_code, load_label = 'high', 'Высокая загрузка'
            else:
                load_code, load_label = 'overloaded', 'Перегружен'
            team_load.append({
                'id': user.id,
                'name': full_name,
                'position': (profile.position or '').strip() or '—',
                'tasks': tasks,
                'load_code': load_code,
                'load_label': load_label,
                'percent': min(tasks * 25, 100),
            })

        # ── Absences ────────────────────────────────────────────────────
        if user_ids:
            soon = today + timedelta(days=14)
            type_labels = {'vacation': 'Отпуск', 'sick': 'Больничный'}
            for a in EmployeeAbsence.objects.select_related('user').filter(
                user_id__in=user_ids, end_date__gte=today, start_date__lte=soon,
            ).order_by('start_date'):
                name = a.user.get_full_name().strip() or a.user.username
                item = {
                    'name': name,
                    'type': a.absence_type,
                    'type_label': type_labels.get(a.absence_type, ''),
                    'start': a.start_date.strftime('%d.%m'),
                    'end': a.end_date.strftime('%d.%m'),
                }
                if a.start_date <= today:
                    absences['current'].append(item)
                else:
                    absences['upcoming'].append(item)

        # ── Notifications ───────────────────────────────────────────────
        if user_ids:
            pending_count = LeaveRequest.objects.filter(
                user_id__in=user_ids, status='pending'
            ).count()
            if pending_count:
                notifications.append({
                    'kind': 'warning',
                    'title': f'Новых заявок: {pending_count}',
                    'text': 'Заявки на отпуск или больничный ожидают рассмотрения.',
                    'url': reverse('manager-leave-requests'),
                    'action': 'Посмотреть',
                })

        if task_stats['no_assignee']:
            notifications.append({
                'kind': 'info',
                'title': f'Задач без исполнителя: {task_stats["no_assignee"]}',
                'text': 'Назначьте сотрудников на открытые задачи.',
                'url': reverse('manager-tasks'),
                'action': 'Назначить',
            })

        if task_stats['overdue']:
            notifications.append({
                'kind': 'danger',
                'title': f'Просрочено: {task_stats["overdue"]}',
                'text': 'Сроки выполнения некоторых задач истекли.',
                'url': reverse('manager-tasks'),
                'action': 'Посмотреть',
            })

        if user_ids:
            soon_threshold = today + timedelta(days=14)
            need_sub = []
            for lr in LeaveRequest.objects.select_related('user').filter(
                user_id__in=user_ids, status='approved',
                start_date__lte=soon_threshold, end_date__gte=today,
            ):
                has_sub = Substitution.objects.filter(
                    absent_user_id=lr.user_id,
                    start_date__lte=lr.end_date,
                    end_date__gte=lr.start_date,
                ).exists()
                if not has_sub:
                    need_sub.append(lr.user.get_full_name().strip() or lr.user.username)
            if need_sub:
                names = ', '.join(need_sub[:3])
                if len(need_sub) > 3:
                    names += f' и ещё {len(need_sub) - 3}'
                notifications.append({
                    'kind': 'warning',
                    'title': 'Нужны замены',
                    'text': f'{names} — уходит в отпуск без назначенной замены.',
                    'url': reverse('manager-substitutions'),
                    'action': 'Назначить замену',
                })

    return render(request, 'dashboard/manager/dashboard.html', {
        'active_tab': 'dashboard',
        'page_title': 'Дашборд',
        'page_subtitle': department.name if department else 'Отдел не назначен',
        'department': department,
        'task_stats': task_stats,
        'active_sprint': active_sprint,
        'team_load': team_load,
        'absences': absences,
        'notifications': notifications,
    })


@login_required
def manager_tasks(request):
    _ensure_role(request, 'manager')
    manager = request.user
    department = _get_manager_department(manager)

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
        }
        for day_date in visible_dates
    ]

    employees = []
    profiles = []
    department_name = department.name if department else "—"
    if department:
        profiles = list(
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

    employee_filter = request.GET.get("employee")
    status_filter = request.GET.get("status")
    priority_filter = request.GET.get("priority")
    type_filter = request.GET.get("type")

    if status_filter not in {
        DepartmentTask.TaskStatus.AWAITING_CONFIRMATION,
        DepartmentTask.TaskStatus.CONFIRMED,
        DepartmentTask.TaskStatus.CONFLICT,
        DepartmentTask.TaskStatus.IN_PROGRESS,
        DepartmentTask.TaskStatus.COMPLETED,
    }:
        status_filter = "all"
    if priority_filter not in {"high", "mid", "low"}:
        priority_filter = "all"
    if type_filter not in {"employee", "department"}:
        type_filter = "all"

    employee_filter_id = None
    if employee_filter and employee_filter != "all":
        try:
            employee_filter_id = int(employee_filter)
        except (TypeError, ValueError):
            employee_filter_id = None

    if employee_filter_id:
        employees = [emp for emp in employees if emp["id"] == employee_filter_id]
    employee_ids = [item["id"] for item in employees]

    # Build absence map: (user_id, date) → absence_type
    absence_by_user_date = {}
    if employee_ids:
        for absence in EmployeeAbsence.objects.filter(
            user_id__in=employee_ids,
            start_date__lte=week_end,
            end_date__gte=week_start,
        ):
            cur = max(absence.start_date, week_start)
            end = min(absence.end_date, week_end)
            while cur <= end:
                key = (absence.user_id, cur)
                if absence.absence_type == "sick" or key not in absence_by_user_date:
                    absence_by_user_date[key] = absence.absence_type
                cur += timedelta(days=1)

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
        if type_filter == "department":
            tasks_queryset = tasks_queryset.filter(task_type="department")
        elif type_filter == "employee":
            tasks_queryset = tasks_queryset.filter(assigned_to_id=employee_filter_id)
        else:
            tasks_queryset = tasks_queryset.filter(
                Q(assigned_to_id=employee_filter_id) | Q(task_type="department")
            )

    tasks_queryset = tasks_queryset.select_related("assigned_to", "created_by")

    priority_rank = {"high": 3, "mid": 2, "low": 1}
    priority_labels = {"high": "Высокий", "mid": "Средний", "low": "Низкий"}
    status_labels = {
        DepartmentTask.TaskStatus.AWAITING_CONFIRMATION: "Ожидает подтверждения",
        DepartmentTask.TaskStatus.CONFIRMED: "Подтверждено",
        DepartmentTask.TaskStatus.CONFLICT: "Конфликт",
        DepartmentTask.TaskStatus.IN_PROGRESS: "В процессе",
        DepartmentTask.TaskStatus.COMPLETED: "Выполнено",
    }
    status_tones = {
        DepartmentTask.TaskStatus.AWAITING_CONFIRMATION: "muted",
        DepartmentTask.TaskStatus.CONFIRMED: "success",
        DepartmentTask.TaskStatus.CONFLICT: "danger",
        DepartmentTask.TaskStatus.IN_PROGRESS: "warning",
        DepartmentTask.TaskStatus.COMPLETED: "success",
    }

    tasks_by_employee_date = {}
    for task in tasks_queryset:
        if task.task_type == "employee":
            tasks_by_employee_date.setdefault((task.assigned_to_id, task.date), []).append(task)

    def serialize_task(task):
        return {
            "id": task.id,
            "title": task.title,
            "priority": task.priority,
            "priority_label": priority_labels.get(task.priority, "Средний"),
            "status": task.status,
            "status_label": status_labels.get(task.status, "Ожидает подтверждения"),
            "tone": status_tones.get(task.status, "muted"),
        }

    absence_labels = {
        "vacation": "Отпуск",
        "sick": "Больничный",
    }

    employee_rows = []
    for employee in employees:
        employee_id = employee["id"]
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

            employee_tasks = tasks_by_employee_date.get((employee_id, day_date), [])
            combined_tasks = [serialize_task(t) for t in employee_tasks]
            combined_tasks.sort(
                key=lambda item: (
                    -priority_rank.get(item["priority"], 0),
                    item["status"] == DepartmentTask.TaskStatus.COMPLETED,
                    item["title"],
                )
            )
            preview = combined_tasks[:3]
            extra_count = max(0, len(combined_tasks) - len(preview))
            cells.append(
                {
                    "date": day_date.isoformat(),
                    "date_label": date_label,
                    "is_absent": False,
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
                "cells": cells,
            }
        )

    tasks_list = []
    shared_tasks_list = []
    now = timezone.localtime()
    for task in tasks_queryset:
        date_label = f"{task.date.day} {month_names[task.date.month - 1]}"
        due_label = "—"
        if task.due_time:
            due_label = f"{task.date.day} {month_names[task.date.month - 1]}, {task.due_time:%H:%M}"
        is_overdue = False
        if task.status != DepartmentTask.TaskStatus.COMPLETED:
            if task.date < now.date():
                is_overdue = True
            elif task.due_time and task.date == now.date() and task.due_time < now.time():
                is_overdue = True

        assignee_label = "—"
        if task.task_type == "department":
            assignee_label = "Все сотрудники"
        if task.assigned_to:
            assignee_label = task.assigned_to.get_full_name().strip() or task.assigned_to.username

        tone = status_tones.get(task.status, "muted")
        if is_overdue:
            tone = "danger"

        due_time_value = task.due_time.strftime("%H:%M") if task.due_time else ""

        task_type_label = "Сотрудник"
        if task.task_type == "department":
            task_type_label = "Отдел"

        payload = {
            "id": task.id,
            "title": task.title,
            "description": task.description,
            "assignee": assignee_label,
            "date_label": date_label,
            "date_value": task.date.isoformat(),
            "due_label": due_label,
            "due_time_value": due_time_value,
            "priority_label": priority_labels.get(task.priority, "Средний"),
            "priority": task.priority,
            "status_label": status_labels.get(task.status, "Ожидает подтверждения"),
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
    tasks_done = tasks_week_qs.filter(status=DepartmentTask.TaskStatus.COMPLETED).count()
    tasks_progress = tasks_week_qs.filter(status=DepartmentTask.TaskStatus.IN_PROGRESS).count()
    tasks_todo = tasks_week_qs.filter(status=DepartmentTask.TaskStatus.AWAITING_CONFIRMATION).count()
    tasks_overdue = 0
    for task in tasks_week_qs:
        if task.status == DepartmentTask.TaskStatus.COMPLETED:
            continue
        if task.date < now.date():
            tasks_overdue += 1
        elif task.due_time and task.date == now.date() and task.due_time < now.time():
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
            'current_week_start': current_week_start.isoformat(),
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
            "priority": (request.GET.get("priority") or "mid").strip().lower(),
            "title": request.GET.get("title") or "",
            "description": request.GET.get("description") or "",
        }
        if initial["task_type"] not in {"employee", "department"}:
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
            "priority": (payload.get("priority") or "mid").strip().lower(),
            "title": payload.get("title") or "",
            "description": payload.get("description") or "",
        }
        if initial["task_type"] not in {"employee", "department"}:
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
    if task_type not in {"employee", "department"}:
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

    due_time = parse_time_value(payload.get("due_time"))

    priority = (payload.get("priority") or "mid").strip().lower()
    if priority not in {"high", "mid", "low"}:
        priority = "mid"

    description = (payload.get("description") or "").strip()

    task = DepartmentTask.objects.create(
        department=department,
        created_by=manager,
        assigned_to=assigned_to,
        date=task_date,
        due_time=due_time,
        title=title,
        description=description,
        task_type=task_type,
        priority=priority,
        status=DepartmentTask.TaskStatus.AWAITING_CONFIRMATION,
    )

    if is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"ok": True, "task_id": task.id})

    current_week_start = today - timedelta(days=today.weekday())
    task_week_start = task_date - timedelta(days=task_date.weekday())
    week_offset = (task_week_start - current_week_start).days // 7
    return redirect(f"{reverse('manager-tasks')}?week={week_offset}")


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
            if task.status == DepartmentTask.TaskStatus.COMPLETED:
                task.status = DepartmentTask.TaskStatus.IN_PROGRESS
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
        DepartmentTask.TaskStatus.AWAITING_CONFIRMATION: "Ожидает подтверждения",
        DepartmentTask.TaskStatus.CONFIRMED: "Подтверждено",
        DepartmentTask.TaskStatus.CONFLICT: "Конфликт",
        DepartmentTask.TaskStatus.IN_PROGRESS: "В процессе",
        DepartmentTask.TaskStatus.COMPLETED: "Выполнено",
    }
    status_tones = {
        DepartmentTask.TaskStatus.AWAITING_CONFIRMATION: "muted",
        DepartmentTask.TaskStatus.CONFIRMED: "success",
        DepartmentTask.TaskStatus.CONFLICT: "danger",
        DepartmentTask.TaskStatus.IN_PROGRESS: "warning",
        DepartmentTask.TaskStatus.COMPLETED: "success",
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
    if task.status != DepartmentTask.TaskStatus.COMPLETED:
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
            "task_status_label": status_labels.get(task.status, "Ожидает подтверждения"),
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
            "due_time": task.due_time.strftime("%H:%M") if task.due_time else "",
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
            "priority": (payload.get("priority") or "mid").strip().lower(),
            "title": payload.get("title") or "",
            "description": payload.get("description") or "",
        }
        if initial["task_type"] not in {"employee", "department"}:
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
    if task_type not in {"employee", "department"}:
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

    due_time = parse_time_value(payload.get("due_time"))

    priority = (payload.get("priority") or "mid").strip().lower()
    if priority not in {"high", "mid", "low"}:
        priority = "mid"

    description = (payload.get("description") or "").strip()

    task.title = title
    task.description = description
    task.task_type = task_type
    task.assigned_to = assigned_to
    task.date = task_date
    task.due_time = due_time
    task.priority = priority
    task.save(
        update_fields=[
            "title",
            "description",
            "task_type",
            "assigned_to",
            "date",
            "due_time",
            "priority",
            "updated_at",
        ]
    )

    return redirect(_resolve_back_url(request, reverse("manager-tasks")))


@ensure_csrf_cookie
@login_required
def manager_employee_detail(request, user_id):
    _ensure_role(request, 'manager')
    manager = request.user
    department = _get_manager_department(manager)
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
    corporate_phone = profile.corporate_phone or '—'
    personal_phone = profile.personal_phone or '—'
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

    status_labels = {'active': 'Активен', 'vacation': 'В отпуске', 'sick': 'Больничный', 'inactive': 'Неактивен'}
    status_classes = {'active': 'success', 'vacation': 'warning', 'sick': 'danger', 'inactive': 'muted'}

    month_names = ['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря']
    priority_labels = {'high': 'Высокий', 'mid': 'Средний', 'low': 'Низкий'}
    task_status_labels = {
        'awaiting_confirmation': 'Ожидает',
        'confirmed': 'Подтверждено',
        'in_progress': 'В процессе',
        'on_review': 'На проверке',
        'completed': 'Выполнено',
        'returned': 'Возвращено',
        'conflict': 'Конфликт',
    }
    task_status_tones = {
        'awaiting_confirmation': 'muted',
        'confirmed': 'success',
        'in_progress': 'warning',
        'on_review': 'info',
        'completed': 'success',
        'returned': 'danger',
        'conflict': 'danger',
    }

    def fmt_date(d):
        return f"{d.day} {month_names[d.month - 1]}"

    current_tasks = []
    active_count = 0
    for task in DepartmentTask.objects.filter(
        assigned_to=user,
        status__in=['awaiting_confirmation', 'confirmed', 'in_progress', 'on_review'],
    ).order_by('date'):
        active_count += 1
        current_tasks.append({
            'id': task.id,
            'title': task.title,
            'date': fmt_date(task.date),
            'priority': task.priority,
            'priority_label': priority_labels.get(task.priority, 'Средний'),
            'status': task.status,
            'status_label': task_status_labels.get(task.status, task.status),
            'status_tone': task_status_tones.get(task.status, 'muted'),
        })

    completed_tasks = []
    for task in DepartmentTask.objects.filter(
        assigned_to=user, status='completed',
    ).order_by('-date')[:10]:
        completed_tasks.append({
            'id': task.id,
            'title': task.title,
            'date': fmt_date(task.date),
            'priority_label': priority_labels.get(task.priority, 'Средний'),
        })

    lr_type_labels = {'vacation': 'Отпуск', 'sick': 'Больничный'}
    lr_status_labels = {'pending': 'На рассмотрении', 'approved': 'Одобрено', 'rejected': 'Отклонено', 'cancelled': 'Отменено'}
    lr_status_tones = {'pending': 'warning', 'approved': 'success', 'rejected': 'danger', 'cancelled': 'muted'}
    leave_requests = []
    for lr in LeaveRequest.objects.filter(user=user).order_by('-created_at')[:20]:
        leave_requests.append({
            'id': lr.id,
            'type_label': lr_type_labels.get(lr.request_type, lr.request_type),
            'start': lr.start_date.strftime('%d.%m.%Y'),
            'end': lr.end_date.strftime('%d.%m.%Y'),
            'status': lr.status,
            'status_label': lr_status_labels.get(lr.status, lr.status),
            'status_tone': lr_status_tones.get(lr.status, 'muted'),
            'comment': lr.comment or '',
        })

    sprint_status_labels = {'planning': 'Планирование', 'active': 'Активен', 'completed': 'Завершён'}
    sprint_status_tones = {'planning': 'neutral', 'active': 'success', 'completed': 'muted'}
    sprint_ids = list(DepartmentTask.objects.filter(
        assigned_to=user, sprint__isnull=False,
    ).values_list('sprint_id', flat=True).distinct())
    sprints_data = []
    for sprint in Sprint.objects.filter(id__in=sprint_ids).order_by('-start_date')[:10]:
        s, e = sprint.start_date, sprint.end_date
        if s.month == e.month:
            period = f'{s.day}–{e.day} {month_names[e.month - 1]} {e.year}'
        else:
            period = f'{s.day} {month_names[s.month - 1]} — {e.day} {month_names[e.month - 1]} {e.year}'
        sprint_task_count = DepartmentTask.objects.filter(assigned_to=user, sprint=sprint).count()
        sprint_done = DepartmentTask.objects.filter(assigned_to=user, sprint=sprint, status='completed').count()
        sprints_data.append({
            'id': sprint.id,
            'title': sprint.title,
            'period': period,
            'status': sprint.status,
            'status_label': sprint_status_labels.get(sprint.status, sprint.status),
            'status_tone': sprint_status_tones.get(sprint.status, ''),
            'task_count': sprint_task_count,
            'done_count': sprint_done,
        })

    if status_code in ('vacation', 'sick', 'inactive'):
        load_code, load_label = 'absent', 'Отсутствует'
    elif active_count == 0:
        load_code, load_label = 'free', 'Свободен'
    elif active_count <= 2:
        load_code, load_label = 'normal', 'Нормально'
    elif active_count <= 4:
        load_code, load_label = 'high', 'Высокая'
    else:
        load_code, load_label = 'overloaded', 'Перегружен'
    load_percent = min(active_count * 25, 100) if status_code == 'active' else 0

    create_task_url = (
        f"{reverse('manager-task-create')}?employee={user.id}"
        f"&next={reverse('manager-employee-detail', args=[user.id])}"
    )

    back_url = _resolve_back_url(request, reverse("manager-team"))
    return render(
        request,
        'dashboard/manager/employee_detail.html',
        {
            'active_tab': 'team',
            'back_url': back_url,
            'employee': {
                'id': user.id,
                'full_name': full_name,
                'position': position,
                'department_name': profile.department.name if profile.department else '—',
                'corporate_phone': corporate_phone,
                'personal_phone': personal_phone,
                'email': email,
                'salary_display': salary_display,
                'status_code': status_code,
                'status_label': status_labels.get(status_code, 'Активен'),
                'status_class': status_classes.get(status_code, 'success'),
                'is_active': user.is_active,
            },
            'load_code': load_code,
            'load_label': load_label,
            'load_percent': load_percent,
            'active_count': active_count,
            'current_tasks': current_tasks,
            'completed_tasks': completed_tasks,
            'leave_requests': leave_requests,
            'sprints': sprints_data,
            'create_task_url': create_task_url,
            'substitute_url': reverse('manager-substitutions'),
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


@login_required
def manager_sprints(request):
    _ensure_role(request, 'manager')
    manager = request.user
    department = _get_manager_department(manager)

    active_sprints = []
    upcoming_sprints = []
    completed_sprints = []

    if department:
        today = timezone.localdate()
        month_names = ['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря']
        status_labels = {'planning': 'Планирование', 'active': 'Активен', 'completed': 'Завершён'}
        status_tones = {'planning': 'neutral', 'active': 'success', 'completed': 'muted'}

        for sprint in Sprint.objects.filter(department=department).order_by('-start_date'):
            s, e = sprint.start_date, sprint.end_date
            if s.month == e.month:
                period = f'{s.day}–{e.day} {month_names[e.month - 1]} {e.year}'
            else:
                period = f'{s.day} {month_names[s.month - 1]} — {e.day} {month_names[e.month - 1]} {e.year}'

            tasks_qs = DepartmentTask.objects.filter(sprint=sprint)
            task_count = tasks_qs.count()
            done_count = tasks_qs.filter(status='completed').count()
            progress = int(done_count / task_count * 100) if task_count else 0

            total_days = max((sprint.end_date - sprint.start_date).days + 1, 1)
            days_gone = max(min((today - sprint.start_date).days + 1, total_days), 0)
            time_progress = int(days_gone / total_days * 100)

            data = {
                'id': sprint.id,
                'title': sprint.title,
                'goal': sprint.goal,
                'period': period,
                'status': sprint.status,
                'status_label': status_labels.get(sprint.status, sprint.status),
                'status_tone': status_tones.get(sprint.status, ''),
                'task_count': task_count,
                'done_count': done_count,
                'progress': progress,
                'time_progress': time_progress,
                'start_date': sprint.start_date,
                'end_date': sprint.end_date,
            }

            if sprint.status == 'active' or (sprint.status == 'planning' and sprint.start_date <= today <= sprint.end_date):
                active_sprints.append(data)
            elif sprint.status == 'completed' or sprint.end_date < today:
                completed_sprints.append(data)
            else:
                upcoming_sprints.append(data)

    return render(request, 'dashboard/manager/sprints.html', {
        'active_tab': 'sprints',
        'page_title': 'Спринты',
        'page_subtitle': department.name if department else 'Отдел не назначен',
        'active_sprints': active_sprints,
        'upcoming_sprints': upcoming_sprints,
        'completed_sprints': completed_sprints,
        'sprints_total': len(active_sprints) + len(upcoming_sprints) + len(completed_sprints),
        'department': department,
    })


@login_required
@require_http_methods(["GET", "POST"])
def manager_sprint_create(request):
    _ensure_role(request, 'manager')
    manager = request.user
    department = _get_manager_department(manager)
    error = ''
    initial = {'title': '', 'goal': '', 'start_date': '', 'end_date': '', 'task_ids': []}

    if request.method == 'POST' and department:
        title = request.POST.get('title', '').strip()
        goal = request.POST.get('goal', '').strip()
        start_date_str = request.POST.get('start_date', '').strip()
        end_date_str = request.POST.get('end_date', '').strip()
        task_ids = request.POST.getlist('task_ids')
        initial.update({
            'title': title, 'goal': goal,
            'start_date': start_date_str, 'end_date': end_date_str,
            'task_ids': [int(i) for i in task_ids if i.isdigit()],
        })

        if not title:
            error = 'Введите название спринта.'
        elif not start_date_str or not end_date_str:
            error = 'Укажите даты начала и окончания.'
        else:
            try:
                start = date.fromisoformat(start_date_str)
                end = date.fromisoformat(end_date_str)
                if end < start:
                    error = 'Дата окончания не может быть раньше начала.'
                else:
                    sprint = Sprint.objects.create(
                        department=department,
                        title=title,
                        goal=goal,
                        start_date=start,
                        end_date=end,
                        status='planning',
                        created_by=manager,
                    )
                    if task_ids:
                        clean_ids = [int(i) for i in task_ids if i.isdigit()]
                        DepartmentTask.objects.filter(
                            id__in=clean_ids,
                            department=department,
                            sprint__isnull=True,
                        ).update(sprint=sprint)
                    return redirect('manager-sprint-detail', sprint_id=sprint.id)
            except (ValueError, TypeError):
                error = 'Неверный формат даты.'

    priority_labels = {'high': 'Высокий', 'mid': 'Средний', 'low': 'Низкий'}
    available_tasks = []
    if department:
        for task in DepartmentTask.objects.filter(
            department=department,
            sprint__isnull=True,
            status__in=['awaiting_confirmation', 'confirmed', 'in_progress', 'on_review'],
        ).select_related('taken_by').order_by('priority', 'title'):
            taken_name = ''
            if task.taken_by:
                taken_name = task.taken_by.get_full_name().strip() or task.taken_by.username
            available_tasks.append({
                'id': task.id,
                'title': task.title,
                'priority': task.priority,
                'priority_label': priority_labels.get(task.priority, task.priority),
                'taken_name': taken_name,
            })

    return render(request, 'dashboard/manager/sprint_create.html', {
        'active_tab': 'sprints',
        'page_title': 'Новый спринт',
        'page_subtitle': 'Создание спринта',
        'error': error,
        'department': department,
        'available_tasks': available_tasks,
        'initial': initial,
    })


@login_required
@require_http_methods(["GET", "POST"])
def manager_sprint_detail(request, sprint_id):
    _ensure_role(request, 'manager')
    manager = request.user
    department = _get_manager_department(manager)
    sprint = get_object_or_404(Sprint, id=sprint_id, department=department)

    error = ''
    success = ''

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add_task':
            task_id = request.POST.get('task_id')
            if task_id:
                try:
                    task = DepartmentTask.objects.get(id=task_id, department=department)
                    task.sprint = sprint
                    task.save(update_fields=['sprint'])
                    messages.success(request, 'Задача добавлена в спринт.')
                except DepartmentTask.DoesNotExist:
                    messages.error(request, 'Задача не найдена.')

        elif action == 'remove_task':
            task_id = request.POST.get('task_id')
            if task_id:
                try:
                    task = DepartmentTask.objects.get(id=task_id, sprint=sprint)
                    task.sprint = None
                    task.save(update_fields=['sprint'])
                    messages.success(request, 'Задача убрана из спринта.')
                except DepartmentTask.DoesNotExist:
                    messages.error(request, 'Задача не найдена.')

        elif action == 'assign_task':
            task_id = request.POST.get('task_id')
            assignee_id = request.POST.get('assignee_id', '').strip()
            if task_id:
                try:
                    task = DepartmentTask.objects.get(id=task_id, sprint=sprint)
                    if not assignee_id:
                        previous = task.taken_by_id
                        task.taken_by = None
                        task.assigned_to = None
                        task.save(update_fields=['taken_by', 'assigned_to'])
                        messages.success(request, 'Исполнитель снят.')
                    else:
                        try:
                            assignee_id_int = int(assignee_id)
                            dept_ids = set(
                                EmployeeProfile.objects.filter(department=department, user__role='employee')
                                .values_list('user_id', flat=True)
                            )
                            if assignee_id_int not in dept_ids:
                                messages.error(request, 'Сотрудник не из вашего отдела.')
                            else:
                                previous = task.taken_by_id
                                task.assigned_to_id = assignee_id_int
                                task.taken_by_id = assignee_id_int
                                if task.status == 'awaiting_confirmation':
                                    task.status = 'confirmed'
                                task.save(update_fields=['assigned_to', 'taken_by', 'status'])
                                if previous != assignee_id_int:
                                    TaskReassignment.objects.create(
                                        task=task,
                                        previous_user_id=previous,
                                        new_user_id=assignee_id_int,
                                        reason='manual',
                                        note='Переназначение в спринте',
                                        created_by=manager,
                                    )
                                messages.success(request, 'Исполнитель назначен.')
                        except (ValueError, TypeError):
                            messages.error(request, 'Неверный сотрудник.')
                except DepartmentTask.DoesNotExist:
                    messages.error(request, 'Задача не найдена.')

        elif action in ('set_active', 'set_completed', 'set_planning'):
            status_map = {'set_active': 'active', 'set_completed': 'completed', 'set_planning': 'planning'}
            sprint.status = status_map[action]
            sprint.save(update_fields=['status'])
            messages.success(request, 'Статус спринта обновлён.')

        elif action == 'update_goal':
            sprint.goal = request.POST.get('goal', '').strip()
            sprint.save(update_fields=['goal'])
            messages.success(request, 'Цель спринта обновлена.')

        return redirect('manager-sprint-detail', sprint_id=sprint.id)

    task_status_labels = {
        'awaiting_confirmation': 'Новая', 'confirmed': 'Взята',
        'conflict': 'Конфликт', 'in_progress': 'В работе',
        'on_review': 'На проверке', 'completed': 'Выполнена',
        'returned': 'На доработку',
    }
    task_status_tones = {
        'awaiting_confirmation': 'neutral', 'confirmed': 'info',
        'conflict': 'danger', 'in_progress': 'warning',
        'on_review': 'warning', 'completed': 'success',
        'returned': 'danger',
    }
    priority_labels = {'high': 'Высокий', 'mid': 'Средний', 'low': 'Низкий'}

    employees = list(
        EmployeeProfile.objects.select_related('user')
        .filter(department=department, user__role='employee')
        .order_by('user__last_name', 'user__first_name')
    )
    employee_options = [
        {'id': p.user_id, 'name': p.user.get_full_name().strip() or p.user.username}
        for p in employees
    ]

    kanban_columns = [
        {'key': 'awaiting_confirmation', 'title': 'Не назначено', 'tasks': []},
        {'key': 'confirmed', 'title': 'Обсуждается / Взята', 'tasks': []},
        {'key': 'in_progress', 'title': 'В работе', 'tasks': []},
        {'key': 'on_review', 'title': 'На проверке', 'tasks': []},
        {'key': 'completed', 'title': 'Выполнено', 'tasks': []},
    ]
    column_index = {col['key']: idx for idx, col in enumerate(kanban_columns)}

    sprint_tasks = []
    unassigned_tasks = []
    load_per_user = {}

    for task in DepartmentTask.objects.filter(sprint=sprint).select_related('taken_by').order_by('priority', 'title'):
        taken_name = ''
        if task.taken_by:
            taken_name = task.taken_by.get_full_name().strip() or task.taken_by.username
            if task.status != 'completed':
                load_per_user[task.taken_by_id] = load_per_user.get(task.taken_by_id, 0) + 1

        item = {
            'id': task.id,
            'title': task.title,
            'priority': task.priority,
            'priority_label': priority_labels.get(task.priority, task.priority),
            'status': task.status,
            'status_label': task_status_labels.get(task.status, task.status),
            'status_tone': task_status_tones.get(task.status, ''),
            'taken_id': task.taken_by_id,
            'taken_name': taken_name,
        }
        sprint_tasks.append(item)
        if not task.taken_by_id:
            unassigned_tasks.append(item)

        col_key = task.status
        if col_key in ('returned',):
            col_key = 'in_progress'
        if col_key in column_index:
            kanban_columns[column_index[col_key]]['tasks'].append(item)
        else:
            kanban_columns[0]['tasks'].append(item)

    team_load = []
    for profile in employees:
        user = profile.user
        full_name = user.get_full_name().strip() or user.username
        count = load_per_user.get(user.id, 0)
        team_load.append({
            'id': user.id,
            'name': full_name,
            'position': (profile.position or '').strip() or '—',
            'tasks': count,
            'percent': min(count * 20, 100),
        })

    available_tasks = []
    for task in DepartmentTask.objects.filter(
        department=department,
        sprint__isnull=True,
        status__in=['awaiting_confirmation', 'confirmed', 'in_progress', 'on_review'],
    ).order_by('priority', 'title'):
        available_tasks.append({
            'id': task.id,
            'title': task.title,
            'priority': task.priority,
            'priority_label': priority_labels.get(task.priority, task.priority),
        })

    month_names = ['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря']
    s, e = sprint.start_date, sprint.end_date
    sprint_period = (
        f'{s.day}–{e.day} {month_names[e.month - 1]} {e.year}'
        if s.month == e.month
        else f'{s.day} {month_names[s.month - 1]} — {e.day} {month_names[e.month - 1]} {e.year}'
    )

    today = timezone.localdate()
    total_days = max((sprint.end_date - sprint.start_date).days + 1, 1)
    days_gone = max(min((today - sprint.start_date).days + 1, total_days), 0)

    tasks_total = len(sprint_tasks)
    tasks_done = sum(1 for t in sprint_tasks if t['status'] == 'completed')
    progress = int(tasks_done / tasks_total * 100) if tasks_total else 0
    time_progress = int(days_gone / total_days * 100)

    sprint_status_labels = {'planning': 'Планирование', 'active': 'Активен', 'completed': 'Завершён'}
    sprint_status_tones = {'planning': 'neutral', 'active': 'success', 'completed': 'muted'}

    return render(request, 'dashboard/manager/sprint_detail.html', {
        'active_tab': 'sprints',
        'page_title': sprint.title,
        'page_subtitle': sprint_period,
        'sprint': sprint,
        'sprint_period': sprint_period,
        'sprint_status_label': sprint_status_labels.get(sprint.status, sprint.status),
        'sprint_status_tone': sprint_status_tones.get(sprint.status, ''),
        'sprint_tasks': sprint_tasks,
        'unassigned_tasks': unassigned_tasks,
        'kanban_columns': kanban_columns,
        'team_load': team_load,
        'employee_options': employee_options,
        'available_tasks': available_tasks,
        'tasks_total': tasks_total,
        'tasks_done': tasks_done,
        'progress': progress,
        'time_progress': time_progress,
        'days_gone': days_gone,
        'total_days': total_days,
        'error': error,
        'success': success,
    })


def _approve_leave_request(lr, manager):
    """Approve a leave request and auto-create an EmployeeAbsence record."""
    lr.status = 'approved'
    lr.reviewed_by = manager
    lr.reviewed_at = timezone.now()
    lr.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])
    EmployeeAbsence.objects.get_or_create(
        user_id=lr.user_id,
        absence_type=lr.request_type,
        start_date=lr.start_date,
        end_date=lr.end_date,
        defaults={'created_by': manager},
    )


def _conflicting_tasks_for_leave(lr):
    return DepartmentTask.objects.filter(
        Q(taken_by_id=lr.user_id) | Q(assigned_to_id=lr.user_id),
        date__gte=lr.start_date,
        date__lte=lr.end_date,
    ).exclude(status__in=['completed', 'returned']).select_related('taken_by', 'assigned_to')


@login_required
@require_http_methods(["GET", "POST"])
def manager_leave_requests(request):
    _ensure_role(request, 'manager')
    manager = request.user
    department = _get_manager_department(manager)

    error = ''
    success = ''
    warning_url = ''

    if request.method == 'POST' and department:
        lr_id = request.POST.get('lr_id')
        action = request.POST.get('action')
        rejection_reason = request.POST.get('rejection_reason', '').strip()

        if lr_id and action in ('approve', 'reject'):
            try:
                lr = LeaveRequest.objects.select_related('user').get(id=lr_id)
                dept_ids = set(
                    EmployeeProfile.objects.filter(department=department)
                    .values_list('user_id', flat=True)
                )
                if lr.user_id not in dept_ids:
                    error = 'Нет доступа к этой заявке.'
                elif lr.status != 'pending':
                    error = 'Заявка уже рассмотрена.'
                else:
                    if action == 'approve':
                        _approve_leave_request(lr, manager)
                        if _conflicting_tasks_for_leave(lr).exists():
                            success = 'Заявка одобрена. У сотрудника есть задачи на период отсутствия — требуется замещение.'
                            warning_url = reverse('manager-leave-request-detail', args=[lr.id])
                        else:
                            success = 'Заявка одобрена.'
                    else:
                        lr.status = 'rejected'
                        lr.reviewed_by = manager
                        lr.reviewed_at = timezone.now()
                        lr.rejection_reason = rejection_reason
                        lr.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'rejection_reason'])
                        success = 'Заявка отклонена.'
            except LeaveRequest.DoesNotExist:
                error = 'Заявка не найдена.'

    status_filter = request.GET.get('status', 'pending')
    if status_filter not in ('pending', 'approved', 'rejected', 'all'):
        status_filter = 'pending'
    type_filter = request.GET.get('type', 'all')
    if type_filter not in ('all', 'vacation', 'sick'):
        type_filter = 'all'

    leave_requests = []
    counts = {'pending': 0, 'approved': 0, 'rejected': 0, 'all': 0, 'vacation': 0, 'sick': 0}
    if department:
        dept_user_ids = list(
            EmployeeProfile.objects.filter(department=department, user__role='employee')
            .values_list('user_id', flat=True)
        )
        base_qs = LeaveRequest.objects.select_related('user').filter(user_id__in=dept_user_ids)

        counts['all'] = base_qs.count()
        counts['pending'] = base_qs.filter(status='pending').count()
        counts['approved'] = base_qs.filter(status='approved').count()
        counts['rejected'] = base_qs.filter(status='rejected').count()
        counts['vacation'] = base_qs.filter(request_type='vacation').count()
        counts['sick'] = base_qs.filter(request_type='sick').count()

        qs = base_qs
        if status_filter != 'all':
            qs = qs.filter(status=status_filter)
        if type_filter != 'all':
            qs = qs.filter(request_type=type_filter)
        qs = qs.order_by('-created_at')

        type_labels = {'vacation': 'Отпуск', 'sick': 'Больничный'}
        status_labels = {'pending': 'На рассмотрении', 'approved': 'Одобрено', 'rejected': 'Отклонено', 'cancelled': 'Отменено'}
        status_tones = {'pending': 'warning', 'approved': 'success', 'rejected': 'danger', 'cancelled': 'muted'}

        for lr in qs:
            employee_name = lr.user.get_full_name().strip() or lr.user.username
            has_conflict = lr.status == 'approved' and _conflicting_tasks_for_leave(lr).exists()
            has_substitution = lr.status == 'approved' and Substitution.objects.filter(
                absent_user_id=lr.user_id,
                start_date__lte=lr.end_date,
                end_date__gte=lr.start_date,
            ).exists()
            leave_requests.append({
                'id': lr.id,
                'employee_name': employee_name,
                'request_type': lr.request_type,
                'type_label': type_labels.get(lr.request_type, lr.request_type),
                'start_display': lr.start_date.strftime('%d.%m.%Y'),
                'end_display': lr.end_date.strftime('%d.%m.%Y'),
                'comment': lr.comment or '',
                'status': lr.status,
                'status_label': status_labels.get(lr.status, lr.status),
                'status_tone': status_tones.get(lr.status, ''),
                'rejection_reason': lr.rejection_reason or '',
                'has_attachment': bool(lr.attachment),
                'attachment_url': lr.attachment.url if lr.attachment else '',
                'has_conflict': has_conflict,
                'has_substitution': has_substitution,
            })

    return render(request, 'dashboard/manager/leave_requests.html', {
        'active_tab': 'leave-requests',
        'page_title': 'Заявки сотрудников',
        'page_subtitle': 'Отпуска и больничные',
        'leave_requests': leave_requests,
        'status_filter': status_filter,
        'type_filter': type_filter,
        'counts': counts,
        'error': error,
        'success': success,
        'warning_url': warning_url,
        'department': department,
    })


@login_required
@require_http_methods(["GET", "POST"])
def manager_leave_request_detail(request, lr_id):
    _ensure_role(request, 'manager')
    manager = request.user
    department = _get_manager_department(manager)

    lr = get_object_or_404(LeaveRequest.objects.select_related('user', 'user__profile'), id=lr_id)
    dept_ids = set(
        EmployeeProfile.objects.filter(department=department)
        .values_list('user_id', flat=True)
    )
    if lr.user_id not in dept_ids:
        raise PermissionDenied

    error = ''
    success = ''

    if request.method == 'POST':
        action = request.POST.get('action')
        rejection_reason = request.POST.get('rejection_reason', '').strip()
        if action == 'approve':
            if lr.status != 'pending':
                error = 'Заявка уже рассмотрена.'
            else:
                _approve_leave_request(lr, manager)
                if _conflicting_tasks_for_leave(lr).exists():
                    success = 'Заявка одобрена. У сотрудника есть задачи на период отсутствия — требуется замещение.'
                else:
                    success = 'Заявка одобрена.'
        elif action == 'reject':
            if lr.status != 'pending':
                error = 'Заявка уже рассмотрена.'
            else:
                lr.status = 'rejected'
                lr.reviewed_by = manager
                lr.reviewed_at = timezone.now()
                lr.rejection_reason = rejection_reason
                lr.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'rejection_reason'])
                success = 'Заявка отклонена.'

    type_labels = {'vacation': 'Отпуск', 'sick': 'Больничный'}
    status_labels = {'pending': 'На рассмотрении', 'approved': 'Одобрено', 'rejected': 'Отклонено', 'cancelled': 'Отменено'}
    status_tones = {'pending': 'warning', 'approved': 'success', 'rejected': 'danger', 'cancelled': 'muted'}
    task_status_labels = {
        'awaiting_confirmation': 'Новая', 'confirmed': 'Взята',
        'conflict': 'Конфликт', 'in_progress': 'В работе',
        'on_review': 'На проверке', 'completed': 'Выполнена',
        'returned': 'На доработку',
    }
    task_status_tones = {
        'awaiting_confirmation': 'neutral', 'confirmed': 'info',
        'conflict': 'danger', 'in_progress': 'warning',
        'on_review': 'warning', 'completed': 'success', 'returned': 'danger',
    }

    employee_name = lr.user.get_full_name().strip() or lr.user.username
    profile = getattr(lr.user, 'profile', None)
    employee_position = (profile.position if profile else '') or '—'
    employee_phone = (profile.corporate_phone or profile.personal_phone) if profile else ''

    period_tasks = []
    for task in _conflicting_tasks_for_leave(lr).order_by('date', 'priority'):
        period_tasks.append({
            'id': task.id,
            'title': task.title,
            'date': task.date.strftime('%d.%m.%Y'),
            'priority': task.priority,
            'status_label': task_status_labels.get(task.status, task.status),
            'status_tone': task_status_tones.get(task.status, ''),
        })

    today = timezone.localdate()
    current_tasks = []
    for task in DepartmentTask.objects.filter(
        Q(taken_by_id=lr.user_id) | Q(assigned_to_id=lr.user_id),
    ).exclude(status__in=['completed', 'returned']).order_by('date'):
        current_tasks.append({
            'id': task.id,
            'title': task.title,
            'date': task.date.strftime('%d.%m.%Y'),
            'priority': task.priority,
            'status_label': task_status_labels.get(task.status, task.status),
            'status_tone': task_status_tones.get(task.status, ''),
            'in_period': lr.start_date <= task.date <= lr.end_date,
        })

    has_substitution = Substitution.objects.filter(
        absent_user_id=lr.user_id,
        start_date__lte=lr.end_date,
        end_date__gte=lr.start_date,
    ).exists()

    return render(request, 'dashboard/manager/leave_request_detail.html', {
        'active_tab': 'leave-requests',
        'page_title': f'Заявка #{lr.id}',
        'page_subtitle': f'{type_labels.get(lr.request_type, lr.request_type)} — {employee_name}',
        'lr': lr,
        'employee_name': employee_name,
        'employee_position': employee_position,
        'employee_phone': employee_phone,
        'type_label': type_labels.get(lr.request_type, lr.request_type),
        'status_label': status_labels.get(lr.status, lr.status),
        'status_tone': status_tones.get(lr.status, ''),
        'start_display': lr.start_date.strftime('%d.%m.%Y'),
        'end_display': lr.end_date.strftime('%d.%m.%Y'),
        'duration_days': (lr.end_date - lr.start_date).days + 1,
        'period_tasks': period_tasks,
        'current_tasks': current_tasks,
        'has_substitution': has_substitution,
        'error': error,
        'success': success,
        'department': department,
    })


def _build_employee_load_map(department, today):
    """Returns: dict[user_id] -> {tasks, absent, name, position}."""
    profiles = list(
        EmployeeProfile.objects.select_related('user')
        .filter(department=department, user__role='employee')
        .order_by('user__last_name', 'user__first_name')
    )
    user_ids = [p.user_id for p in profiles]

    task_counts = {}
    if user_ids:
        for row in DepartmentTask.objects.filter(
            taken_by_id__in=user_ids,
            status__in=['confirmed', 'in_progress', 'on_review'],
        ).values('taken_by_id'):
            task_counts[row['taken_by_id']] = task_counts.get(row['taken_by_id'], 0) + 1

    absent_now = set()
    if user_ids:
        for row in EmployeeAbsence.objects.filter(
            user_id__in=user_ids, start_date__lte=today, end_date__gte=today,
        ).values('user_id'):
            absent_now.add(row['user_id'])

    result = {}
    for profile in profiles:
        user = profile.user
        full_name = user.get_full_name().strip() or user.username
        result[user.id] = {
            'id': user.id,
            'name': full_name,
            'position': (profile.position or '').strip() or '—',
            'tasks': task_counts.get(user.id, 0),
            'absent': user.id in absent_now,
            'percent': min(task_counts.get(user.id, 0) * 20, 100),
        }
    return result, [r for r in result.values()]


@login_required
@require_http_methods(["GET", "POST"])
def manager_substitutions(request):
    _ensure_role(request, 'manager')
    manager = request.user
    department = _get_manager_department(manager)

    error = ''
    success = ''

    if request.method == 'POST' and department:
        action = request.POST.get('action')
        dept_ids = set(
            EmployeeProfile.objects.filter(department=department, user__role='employee')
            .values_list('user_id', flat=True)
        )

        if action == 'create':
            absent_user_id = request.POST.get('absent_user_id', '').strip()
            substitute_user_id = request.POST.get('substitute_user_id', '').strip()
            start_date_str = request.POST.get('start_date', '').strip()
            end_date_str = request.POST.get('end_date', '').strip()
            note = request.POST.get('note', '').strip()
            reassign_all = request.POST.get('reassign_all') == '1'

            if not absent_user_id or not substitute_user_id:
                error = 'Выберите отсутствующего сотрудника и замену.'
            elif absent_user_id == substitute_user_id:
                error = 'Замена не может быть тем же сотрудником.'
            elif not start_date_str or not end_date_str:
                error = 'Укажите даты.'
            else:
                try:
                    start = date.fromisoformat(start_date_str)
                    end = date.fromisoformat(end_date_str)
                    if end < start:
                        error = 'Дата окончания не может быть раньше начала.'
                    else:
                        absent_id, sub_id = int(absent_user_id), int(substitute_user_id)
                        if absent_id not in dept_ids or sub_id not in dept_ids:
                            error = 'Выбранные сотрудники не из вашего отдела.'
                        else:
                            sub_obj = Substitution.objects.create(
                                absent_user_id=absent_id,
                                substitute_user_id=sub_id,
                                start_date=start,
                                end_date=end,
                                created_by=manager,
                                note=note,
                            )
                            if reassign_all:
                                tasks = DepartmentTask.objects.filter(
                                    Q(taken_by_id=absent_id) | Q(assigned_to_id=absent_id),
                                    department=department,
                                    date__gte=start, date__lte=end,
                                ).exclude(status__in=['completed', 'returned'])
                                for t in tasks:
                                    prev = t.taken_by_id
                                    t.taken_by_id = sub_id
                                    t.assigned_to_id = sub_id
                                    t.save(update_fields=['taken_by', 'assigned_to'])
                                    if prev != sub_id:
                                        TaskReassignment.objects.create(
                                            task=t,
                                            previous_user_id=prev,
                                            new_user_id=sub_id,
                                            reason='substitution',
                                            note='Замещение всех задач периода',
                                            substitution=sub_obj,
                                            created_by=manager,
                                        )
                            success = 'Замещение создано.'
                except (ValueError, TypeError):
                    error = 'Неверный формат даты.'

        elif action == 'delete':
            sub_id = request.POST.get('sub_id', '').strip()
            if sub_id:
                try:
                    sub = Substitution.objects.get(id=sub_id, created_by=manager)
                    sub.delete()
                    success = 'Замещение удалено.'
                except Substitution.DoesNotExist:
                    error = 'Замещение не найдено или нет доступа.'

        elif action == 'reassign_task':
            task_id = request.POST.get('task_id', '').strip()
            new_user_id = request.POST.get('new_user_id', '').strip()
            reason = request.POST.get('reason', 'substitution')
            note = request.POST.get('note', '').strip()
            if not task_id or not new_user_id:
                error = 'Выберите задачу и сотрудника.'
            else:
                try:
                    task = DepartmentTask.objects.get(id=task_id, department=department)
                    new_user_id_int = int(new_user_id)
                    if new_user_id_int not in dept_ids:
                        error = 'Сотрудник не из вашего отдела.'
                    else:
                        previous = task.taken_by_id
                        task.taken_by_id = new_user_id_int
                        task.assigned_to_id = new_user_id_int
                        if task.status == 'awaiting_confirmation':
                            task.status = 'confirmed'
                        task.save(update_fields=['taken_by', 'assigned_to', 'status'])
                        if previous != new_user_id_int:
                            TaskReassignment.objects.create(
                                task=task,
                                previous_user_id=previous,
                                new_user_id=new_user_id_int,
                                reason=reason if reason in ('vacation', 'sick', 'substitution', 'manual') else 'manual',
                                note=note,
                                created_by=manager,
                            )
                        success = 'Задача переназначена.'
                except (DepartmentTask.DoesNotExist, ValueError, TypeError):
                    error = 'Задача не найдена.'

        elif action == 'unassign_task':
            task_id = request.POST.get('task_id', '').strip()
            if task_id:
                try:
                    task = DepartmentTask.objects.get(id=task_id, department=department)
                    previous = task.taken_by_id
                    task.taken_by = None
                    task.assigned_to = None
                    task.save(update_fields=['taken_by', 'assigned_to'])
                    if previous:
                        TaskReassignment.objects.create(
                            task=task,
                            previous_user_id=previous,
                            new_user_id=None,
                            reason='manual',
                            note='Снят исполнитель',
                            created_by=manager,
                        )
                    success = 'Исполнитель снят.'
                except DepartmentTask.DoesNotExist:
                    error = 'Задача не найдена.'

    today = timezone.localdate()
    employees = []
    substitutions_data = []
    situations = []
    selected_absent_id = None
    selected_start = ''
    selected_end = ''
    affected_tasks = []
    recommendations = []
    history = []
    employee_load_map = {}

    if department:
        employee_load_map, employees = _build_employee_load_map(department, today)
        user_ids = list(employee_load_map.keys())

        # ── Existing substitutions ─────────────────────────────────────
        for sub in Substitution.objects.filter(absent_user_id__in=user_ids).select_related(
            'absent_user', 'substitute_user'
        ).order_by('-start_date'):
            substitutions_data.append({
                'id': sub.id,
                'absent_id': sub.absent_user_id,
                'absent_name': sub.absent_user.get_full_name().strip() or sub.absent_user.username,
                'substitute_name': sub.substitute_user.get_full_name().strip() or sub.substitute_user.username,
                'start_display': sub.start_date.strftime('%d.%m.%Y'),
                'end_display': sub.end_date.strftime('%d.%m.%Y'),
                'note': sub.note or '',
                'is_active': sub.start_date <= today <= sub.end_date,
                'is_future': sub.start_date > today,
                'can_delete': sub.created_by_id == manager.id,
            })

        # ── Situations needing replacement ─────────────────────────────
        soon = today + timedelta(days=30)
        type_labels = {'vacation': 'Отпуск', 'sick': 'Больничный'}

        for lr in LeaveRequest.objects.select_related('user').filter(
            user_id__in=user_ids,
            status='approved',
            end_date__gte=today,
            start_date__lte=soon,
        ).order_by('start_date'):
            existing_subs = Substitution.objects.filter(
                absent_user_id=lr.user_id,
                start_date__lte=lr.end_date,
                end_date__gte=lr.start_date,
            )
            tasks_qs = DepartmentTask.objects.filter(
                Q(taken_by_id=lr.user_id) | Q(assigned_to_id=lr.user_id),
                department=department,
                date__gte=lr.start_date,
                date__lte=lr.end_date,
            ).exclude(status__in=['completed', 'returned'])
            tasks_count = tasks_qs.count()
            reassigned_count = tasks_qs.exclude(taken_by_id=lr.user_id).count()

            if existing_subs.exists() and tasks_count == 0:
                continue

            if not existing_subs.exists():
                status_code, status_label = 'needed', 'Требуется замена'
            elif reassigned_count == 0:
                status_code, status_label = 'partial', 'Назначена общая замена'
            elif reassigned_count < tasks_count:
                status_code, status_label = 'partial', 'Частично заменено'
            else:
                status_code, status_label = 'done', 'Замена назначена'

            employee_name = lr.user.get_full_name().strip() or lr.user.username
            situations.append({
                'id': lr.id,
                'absent_id': lr.user_id,
                'absent_name': employee_name,
                'reason': type_labels.get(lr.request_type, lr.request_type),
                'start': lr.start_date.strftime('%d.%m.%Y'),
                'end': lr.end_date.strftime('%d.%m.%Y'),
                'start_iso': lr.start_date.isoformat(),
                'end_iso': lr.end_date.isoformat(),
                'tasks_count': tasks_count,
                'reassigned_count': reassigned_count,
                'status_code': status_code,
                'status_label': status_label,
            })

        # ── Tasks without performer (additional situation type) ────────
        no_assignee = DepartmentTask.objects.filter(
            department=department,
            taken_by__isnull=True,
            date__gte=today,
        ).exclude(status__in=['completed', 'returned'])
        no_assignee_count = no_assignee.count()
        if no_assignee_count:
            situations.append({
                'id': f'no-assignee',
                'absent_id': None,
                'absent_name': 'Задачи без исполнителя',
                'reason': 'Назначения нет',
                'start': '—',
                'end': '—',
                'tasks_count': no_assignee_count,
                'reassigned_count': 0,
                'status_code': 'needed',
                'status_label': 'Нужен исполнитель',
                'is_no_assignee': True,
            })

        # ── Pre-fill from query: absent + start + end ──────────────────
        absent_param = request.GET.get('absent', '').strip()
        start_param = request.GET.get('start', '').strip()
        end_param = request.GET.get('end', '').strip()

        if absent_param.isdigit() and int(absent_param) in user_ids:
            selected_absent_id = int(absent_param)
        if start_param:
            selected_start = start_param
        if end_param:
            selected_end = end_param

        # ── Affected tasks for selected absentee ───────────────────────
        if selected_absent_id and selected_start and selected_end:
            try:
                start_d = date.fromisoformat(selected_start)
                end_d = date.fromisoformat(selected_end)
                priority_labels = {'high': 'Высокий', 'mid': 'Средний', 'low': 'Низкий'}
                task_status_labels = {
                    'awaiting_confirmation': 'Новая', 'confirmed': 'Взята',
                    'in_progress': 'В работе', 'on_review': 'На проверке',
                }
                task_status_tones = {
                    'awaiting_confirmation': 'neutral', 'confirmed': 'info',
                    'in_progress': 'warning', 'on_review': 'warning',
                }
                for t in DepartmentTask.objects.filter(
                    Q(taken_by_id=selected_absent_id) | Q(assigned_to_id=selected_absent_id),
                    department=department,
                    date__gte=start_d, date__lte=end_d,
                ).exclude(status__in=['completed', 'returned']).select_related('taken_by').order_by('date'):
                    affected_tasks.append({
                        'id': t.id,
                        'title': t.title,
                        'date': t.date.strftime('%d.%m.%Y'),
                        'priority': t.priority,
                        'priority_label': priority_labels.get(t.priority, t.priority),
                        'status_label': task_status_labels.get(t.status, t.status),
                        'status_tone': task_status_tones.get(t.status, ''),
                        'taken_id': t.taken_by_id,
                    })
            except (ValueError, TypeError):
                pass

            # ── Recommendations ────────────────────────────────────────
            absent_pos = (employee_load_map.get(selected_absent_id) or {}).get('position', '')
            for emp in employees:
                if emp['id'] == selected_absent_id or emp['absent']:
                    continue
                score = 100
                score -= emp['tasks'] * 15
                same_position = absent_pos and emp['position'].lower() == absent_pos.lower()
                if same_position:
                    score += 20
                emp_data = dict(emp)
                emp_data['score'] = score
                emp_data['same_position'] = same_position
                recommendations.append(emp_data)
            recommendations.sort(key=lambda r: -r['score'])

        # ── Recent reassignment history ────────────────────────────────
        for r in TaskReassignment.objects.filter(
            task__department=department,
        ).select_related('task', 'previous_user', 'new_user').order_by('-created_at')[:15]:
            prev_name = (r.previous_user.get_full_name().strip() or r.previous_user.username) if r.previous_user else '—'
            new_name = (r.new_user.get_full_name().strip() or r.new_user.username) if r.new_user else '—'
            history.append({
                'id': r.id,
                'task_id': r.task_id,
                'task_title': r.task.title,
                'previous_name': prev_name,
                'new_name': new_name,
                'reason_label': r.get_reason_display(),
                'note': r.note,
                'created_at': timezone.localtime(r.created_at).strftime('%d.%m.%Y %H:%M'),
            })

    return render(request, 'dashboard/manager/substitutions.html', {
        'active_tab': 'substitutions',
        'page_title': 'Замещения',
        'page_subtitle': 'Управление заменами сотрудников',
        'substitutions': substitutions_data,
        'employees': employees,
        'situations': situations,
        'selected_absent_id': selected_absent_id,
        'selected_start': selected_start,
        'selected_end': selected_end,
        'affected_tasks': affected_tasks,
        'recommendations': recommendations,
        'history': history,
        'error': error,
        'success': success,
        'department': department,
    })


@login_required
def manager_team(request):
    _ensure_role(request, 'manager')
    manager = request.user
    department = _get_manager_department(manager)

    employees = []
    if department:
        today = timezone.localdate()
        month_abbr = ['янв','фев','мар','апр','май','июн','июл','авг','сен','окт','ноя','дек']
        profiles = list(
            EmployeeProfile.objects.select_related('user', 'department')
            .filter(department=department, user__role='employee')
            .order_by('user__last_name', 'user__first_name')
        )
        user_ids = [p.user_id for p in profiles]

        current_absences = {}
        if user_ids:
            for absence in EmployeeAbsence.objects.filter(
                user_id__in=user_ids, start_date__lte=today, end_date__gte=today
            ):
                if absence.absence_type == 'sick':
                    current_absences[absence.user_id] = 'sick'
                elif absence.user_id not in current_absences:
                    current_absences[absence.user_id] = absence.absence_type

        active_task_counts = {}
        if user_ids:
            for row in DepartmentTask.objects.filter(
                assigned_to_id__in=user_ids,
                status__in=['confirmed', 'in_progress', 'on_review'],
            ).values('assigned_to_id'):
                uid = row['assigned_to_id']
                active_task_counts[uid] = active_task_counts.get(uid, 0) + 1

        next_deadlines = {}
        if user_ids:
            for task in DepartmentTask.objects.filter(
                assigned_to_id__in=user_ids,
                status__in=['confirmed', 'in_progress', 'on_review'],
                date__gte=today,
            ).order_by('date').values('assigned_to_id', 'date'):
                uid = task['assigned_to_id']
                if uid not in next_deadlines:
                    next_deadlines[uid] = task['date']

        status_labels = {'active': 'Работает', 'vacation': 'В отпуске', 'sick': 'Больничный', 'inactive': 'Неактивен'}
        status_tones = {'active': 'success', 'vacation': 'warning', 'sick': 'danger', 'inactive': 'neutral'}

        for profile in profiles:
            user = profile.user
            full_name = " ".join(
                p for p in [user.last_name, user.first_name, profile.middle_name or ''] if p
            ).strip() or user.username
            absence_status = current_absences.get(user.id)
            if not user.is_active:
                status_code = 'inactive'
            elif absence_status:
                status_code = absence_status
            else:
                status_code = 'active'

            tasks = active_task_counts.get(user.id, 0)
            if status_code in ('vacation', 'sick', 'inactive'):
                load_code, load_label = 'absent', 'Отсутствует'
            elif tasks == 0:
                load_code, load_label = 'free', 'Свободен'
            elif tasks <= 2:
                load_code, load_label = 'normal', 'Нормально'
            elif tasks <= 4:
                load_code, load_label = 'high', 'Высокая'
            else:
                load_code, load_label = 'overloaded', 'Перегружен'

            next_dl = next_deadlines.get(user.id)
            next_deadline_str = f'{next_dl.day} {month_abbr[next_dl.month - 1]}' if next_dl else '—'

            employees.append({
                'id': user.id,
                'full_name': full_name,
                'position': (profile.position or '').strip() or 'Без должности',
                'status_code': status_code,
                'status_label': status_labels.get(status_code, status_code),
                'status_tone': status_tones.get(status_code, ''),
                'active_tasks': tasks,
                'load_code': load_code,
                'load_label': load_label,
                'load_percent': min(tasks * 25, 100) if status_code == 'active' else 0,
                'next_deadline': next_deadline_str,
            })

    stats = {
        'total': len(employees),
        'active': sum(1 for e in employees if e['status_code'] == 'active'),
        'on_leave': sum(1 for e in employees if e['status_code'] in ('vacation', 'sick')),
        'inactive': sum(1 for e in employees if e['status_code'] == 'inactive'),
    }

    return render(request, 'dashboard/manager/team.html', {
        'active_tab': 'team',
        'page_title': 'Команда',
        'page_subtitle': department.name if department else 'Отдел не назначен',
        'employees': employees,
        'department': department,
        'stats': stats,
    })


@login_required
@require_http_methods(["GET"])
def manager_profile(request):
    _ensure_role(request, 'manager')
    manager = request.user
    department = _get_manager_department(manager)

    try:
        profile = EmployeeProfile.objects.get(user=manager)
    except EmployeeProfile.DoesNotExist:
        profile = None

    avatar_url = None
    if profile and profile.avatar:
        try:
            avatar_url = profile.avatar.url
        except Exception:
            pass

    team_size = 0
    tasks_created = 0
    sprints_active = 0
    sprints_completed = 0
    tasks_completed = 0
    team_active = 0
    team_on_leave = 0

    if department:
        today = timezone.localdate()
        profiles = list(
            EmployeeProfile.objects.select_related('user')
            .filter(department=department, user__role='employee')
        )
        user_ids = [p.user_id for p in profiles]
        team_size = len(profiles)

        tasks_created = DepartmentTask.objects.filter(created_by=manager).count()
        tasks_completed = DepartmentTask.objects.filter(
            department=department, status='completed'
        ).count()

        sprints_active = Sprint.objects.filter(department=department, status='active').count()
        sprints_completed = Sprint.objects.filter(department=department, status='completed').count()

        absent_ids = set()
        if user_ids:
            for row in EmployeeAbsence.objects.filter(
                user_id__in=user_ids, start_date__lte=today, end_date__gte=today
            ).values('user_id'):
                absent_ids.add(row['user_id'])

        team_active = sum(1 for p in profiles if p.user.is_active and p.user_id not in absent_ids)
        team_on_leave = len(absent_ids)

    return render(request, 'dashboard/manager/profile.html', {
        'active_tab': 'profile',
        'page_title': 'Мой профиль',
        'page_subtitle': department.name if department else 'Отдел не назначен',
        'user': manager,
        'profile': profile,
        'department': department,
        'department_name': department.name if department else '—',
        'avatar_url': avatar_url,
        'team_size': team_size,
        'team_active': team_active,
        'team_on_leave': team_on_leave,
        'tasks_created': tasks_created,
        'tasks_completed': tasks_completed,
        'sprints_active': sprints_active,
        'sprints_completed': sprints_completed,
    })
