import json
from datetime import date, time, timedelta

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
    return redirect('manager-tasks')


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


@login_required
def manager_sprints(request):
    _ensure_role(request, 'manager')
    manager = request.user
    department = _get_manager_department(manager)

    sprints = []
    if department:
        month_names = ['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря']
        status_labels = {'planning': 'Планирование', 'active': 'Активен', 'completed': 'Завершён'}
        status_tones = {'planning': 'neutral', 'active': 'success', 'completed': 'muted'}

        for sprint in Sprint.objects.filter(department=department).order_by('-start_date'):
            s, e = sprint.start_date, sprint.end_date
            if s.month == e.month:
                period = f'{s.day}–{e.day} {month_names[e.month - 1]} {e.year}'
            else:
                period = f'{s.day} {month_names[s.month - 1]} — {e.day} {month_names[e.month - 1]} {e.year}'
            task_count = DepartmentTask.objects.filter(sprint=sprint).count()
            done_count = DepartmentTask.objects.filter(sprint=sprint, status='completed').count()
            sprints.append({
                'id': sprint.id,
                'title': sprint.title,
                'period': period,
                'status': sprint.status,
                'status_label': status_labels.get(sprint.status, sprint.status),
                'status_tone': status_tones.get(sprint.status, ''),
                'task_count': task_count,
                'done_count': done_count,
            })

    return render(request, 'dashboard/manager/sprints.html', {
        'active_tab': 'sprints',
        'page_title': 'Спринты',
        'page_subtitle': department.name if department else 'Отдел не назначен',
        'sprints': sprints,
        'department': department,
    })


@login_required
@require_http_methods(["GET", "POST"])
def manager_sprint_create(request):
    _ensure_role(request, 'manager')
    manager = request.user
    department = _get_manager_department(manager)
    error = ''

    if request.method == 'POST' and department:
        title = request.POST.get('title', '').strip()
        goal = request.POST.get('goal', '').strip()
        start_date_str = request.POST.get('start_date', '').strip()
        end_date_str = request.POST.get('end_date', '').strip()

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
                    return redirect('manager-sprint-detail', sprint_id=sprint.id)
            except (ValueError, TypeError):
                error = 'Неверный формат даты.'

    return render(request, 'dashboard/manager/sprint_create.html', {
        'active_tab': 'sprints',
        'page_title': 'Новый спринт',
        'page_subtitle': 'Создание спринта',
        'error': error,
        'department': department,
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
                    success = 'Задача добавлена в спринт.'
                except DepartmentTask.DoesNotExist:
                    error = 'Задача не найдена.'

        elif action == 'remove_task':
            task_id = request.POST.get('task_id')
            if task_id:
                try:
                    task = DepartmentTask.objects.get(id=task_id, sprint=sprint)
                    task.sprint = None
                    task.save(update_fields=['sprint'])
                    success = 'Задача убрана из спринта.'
                except DepartmentTask.DoesNotExist:
                    error = 'Задача не найдена.'

        elif action in ('set_active', 'set_completed', 'set_planning'):
            status_map = {'set_active': 'active', 'set_completed': 'completed', 'set_planning': 'planning'}
            sprint.status = status_map[action]
            sprint.save(update_fields=['status'])
            success = 'Статус спринта обновлён.'

        elif action == 'update_goal':
            sprint.goal = request.POST.get('goal', '').strip()
            sprint.save(update_fields=['goal'])
            success = 'Цель спринта обновлена.'

        return redirect('manager-sprint-detail', sprint_id=sprint.id)

    task_status_labels = {
        'awaiting_confirmation': 'Ожидает', 'confirmed': 'Подтверждена',
        'conflict': 'Конфликт', 'in_progress': 'В работе', 'completed': 'Завершена',
    }
    task_status_tones = {
        'awaiting_confirmation': 'neutral', 'confirmed': 'info',
        'conflict': 'danger', 'in_progress': 'warning', 'completed': 'success',
    }
    priority_labels = {'high': 'Высокий', 'mid': 'Средний', 'low': 'Низкий'}

    sprint_tasks = []
    for task in DepartmentTask.objects.filter(sprint=sprint).select_related('taken_by').order_by('priority', 'title'):
        taken_name = task.taken_by.get_full_name().strip() or task.taken_by.username if task.taken_by else ''
        sprint_tasks.append({
            'id': task.id,
            'title': task.title,
            'priority': task.priority,
            'priority_label': priority_labels.get(task.priority, task.priority),
            'status': task.status,
            'status_label': task_status_labels.get(task.status, task.status),
            'status_tone': task_status_tones.get(task.status, ''),
            'taken_name': taken_name,
        })

    available_tasks = []
    for task in DepartmentTask.objects.filter(
        department=department,
        sprint__isnull=True,
        status__in=['awaiting_confirmation', 'confirmed'],
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
        'available_tasks': available_tasks,
        'tasks_total': len(sprint_tasks),
        'tasks_done': sum(1 for t in sprint_tasks if t['status'] == 'completed'),
        'error': error,
        'success': success,
    })


@login_required
@require_http_methods(["GET", "POST"])
def manager_leave_requests(request):
    _ensure_role(request, 'manager')
    manager = request.user
    department = _get_manager_department(manager)

    error = ''
    success = ''

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
                    lr.reviewed_by = manager
                    lr.reviewed_at = timezone.now()
                    if action == 'approve':
                        lr.status = 'approved'
                        lr.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])
                        success = 'Заявка одобрена.'
                    else:
                        lr.status = 'rejected'
                        lr.rejection_reason = rejection_reason
                        lr.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'rejection_reason'])
                        success = 'Заявка отклонена.'
            except LeaveRequest.DoesNotExist:
                error = 'Заявка не найдена.'

    status_filter = request.GET.get('status', 'pending')
    if status_filter not in ('pending', 'approved', 'rejected', 'all'):
        status_filter = 'pending'

    leave_requests = []
    if department:
        dept_user_ids = list(
            EmployeeProfile.objects.filter(department=department, user__role='employee')
            .values_list('user_id', flat=True)
        )
        qs = LeaveRequest.objects.select_related('user').filter(user_id__in=dept_user_ids)
        if status_filter != 'all':
            qs = qs.filter(status=status_filter)
        qs = qs.order_by('-created_at')

        type_labels = {'vacation': 'Отпуск', 'sick': 'Больничный'}
        status_labels = {'pending': 'На рассмотрении', 'approved': 'Одобрено', 'rejected': 'Отклонено'}
        status_tones = {'pending': 'warning', 'approved': 'success', 'rejected': 'danger'}

        for lr in qs:
            employee_name = lr.user.get_full_name().strip() or lr.user.username
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
            })

    return render(request, 'dashboard/manager/leave_requests.html', {
        'active_tab': 'leave-requests',
        'page_title': 'Заявки сотрудников',
        'page_subtitle': 'Отпуска и больничные',
        'leave_requests': leave_requests,
        'status_filter': status_filter,
        'error': error,
        'success': success,
        'department': department,
    })


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

        if action == 'create':
            absent_user_id = request.POST.get('absent_user_id', '').strip()
            substitute_user_id = request.POST.get('substitute_user_id', '').strip()
            start_date_str = request.POST.get('start_date', '').strip()
            end_date_str = request.POST.get('end_date', '').strip()
            note = request.POST.get('note', '').strip()

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
                        dept_ids = set(
                            EmployeeProfile.objects.filter(department=department, user__role='employee')
                            .values_list('user_id', flat=True)
                        )
                        absent_id, sub_id = int(absent_user_id), int(substitute_user_id)
                        if absent_id not in dept_ids or sub_id not in dept_ids:
                            error = 'Выбранные сотрудники не из вашего отдела.'
                        else:
                            Substitution.objects.create(
                                absent_user_id=absent_id,
                                substitute_user_id=sub_id,
                                start_date=start,
                                end_date=end,
                                created_by=manager,
                                note=note,
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

    employees = []
    substitutions = []
    if department:
        today = timezone.localdate()
        profiles = list(
            EmployeeProfile.objects.select_related('user')
            .filter(department=department, user__role='employee')
            .order_by('user__last_name', 'user__first_name')
        )
        employees = [
            {'id': p.user_id, 'name': p.user.get_full_name().strip() or p.user.username}
            for p in profiles
        ]
        user_ids = [p.user_id for p in profiles]

        for sub in Substitution.objects.filter(absent_user_id__in=user_ids).select_related(
            'absent_user', 'substitute_user'
        ).order_by('-start_date'):
            substitutions.append({
                'id': sub.id,
                'absent_name': sub.absent_user.get_full_name().strip() or sub.absent_user.username,
                'substitute_name': sub.substitute_user.get_full_name().strip() or sub.substitute_user.username,
                'start_display': sub.start_date.strftime('%d.%m.%Y'),
                'end_display': sub.end_date.strftime('%d.%m.%Y'),
                'note': sub.note or '',
                'is_active': sub.start_date <= today <= sub.end_date,
                'can_delete': sub.created_by_id == manager.id,
            })

    return render(request, 'dashboard/manager/substitutions.html', {
        'active_tab': 'substitutions',
        'page_title': 'Замещения',
        'page_subtitle': 'Управление заменами сотрудников',
        'substitutions': substitutions,
        'employees': employees,
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
        profiles = list(
            EmployeeProfile.objects.select_related('user')
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
                taken_by_id__in=user_ids,
                status__in=['confirmed', 'in_progress'],
            ).values('taken_by_id'):
                active_task_counts[row['taken_by_id']] = active_task_counts.get(row['taken_by_id'], 0) + 1

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
            employees.append({
                'id': user.id,
                'full_name': full_name,
                'position': (profile.position or '').strip() or 'Без должности',
                'status_code': status_code,
                'status_label': status_labels.get(status_code, status_code),
                'status_tone': status_tones.get(status_code, ''),
                'active_tasks': active_task_counts.get(user.id, 0),
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
