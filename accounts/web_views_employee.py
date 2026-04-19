import calendar
from datetime import date, timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie

from .models import (
    DepartmentTask,
    EmployeeAbsence,
    EmployeeAvailability,
    EmployeeProfile,
    GlobalSettings,
    TaskSubmission,
    UserRole,
)
from .web_views_shared import (
    _ensure_role,
    _get_employee_next_slot_context,
    _render_employee_page,
    _resolve_back_url,
)

@login_required
def employee_dashboard(request):
    return employee_schedule(request)


@login_required
def employee_schedule(request):
    _ensure_role(request, 'employee')
    today = timezone.localdate()
    next_slot_context = _get_employee_next_slot_context(request.user)
    view = (request.GET.get("view") or "week").strip().lower()
    if view not in {"week", "month"}:
        view = "week"
    only_work = (request.GET.get("only_work") or "").strip() in {"1", "true", "yes"}

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

    month_offset_param = (request.GET.get("month_offset") or "").strip()
    month_offset = 0
    if month_offset_param:
        try:
            month_offset = int(month_offset_param)
        except (TypeError, ValueError):
            month_offset = 0

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
    month_titles = [
        "Январь",
        "Февраль",
        "Март",
        "Апрель",
        "Май",
        "Июнь",
        "Июль",
        "Август",
        "Сентябрь",
        "Октябрь",
        "Ноябрь",
        "Декабрь",
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
    weekday_short = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

    week_start = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)
    week_end = week_start + timedelta(days=6)
    if week_start.month == week_end.month:
        week_period_label = f"{week_start.day}–{week_end.day} {month_names[week_end.month - 1]}"
    else:
        week_period_label = (
            f"{week_start.day} {month_names[week_start.month - 1]} — "
            f"{week_end.day} {month_names[week_end.month - 1]}"
        )

    week_dates = [week_start + timedelta(days=offset) for offset in range(7)]
    week_entries = (
        EmployeeAvailability.objects.select_related("approved_by")
        .filter(user=request.user, date__range=(week_start, week_end))
    )
    week_entries_map = {entry.date: entry for entry in week_entries}

    def format_full_name(user):
        if not user:
            return "—"
        full_name = " ".join(part for part in [user.last_name, user.first_name] if part).strip()
        return full_name or user.username

    department_manager = None
    profile = getattr(request.user, "profile", None)
    if profile and profile.department and profile.department.manager:
        department_manager = profile.department.manager
    if profile and profile.department_id:
        week_has_approved = EmployeeAvailability.objects.filter(
            user__profile__department_id=profile.department_id,
            date__range=(week_start, week_end),
            is_approved=True,
        ).exists()
    else:
        week_has_approved = week_entries.filter(is_approved=True).exists()

    week_absences = EmployeeAbsence.objects.filter(
        user=request.user,
        start_date__lte=week_end,
        end_date__gte=week_start,
    )
    absence_by_date = {}
    for absence in week_absences:
        start_date = max(absence.start_date, week_start)
        end_date = min(absence.end_date, week_end)
        current = start_date
        while current <= end_date:
            if absence.absence_type == "sick" or current not in absence_by_date:
                absence_by_date[current] = absence.absence_type
            current += timedelta(days=1)

    week_rows = []
    for day_date in week_dates:
        absence_type = absence_by_date.get(day_date)
        entry = week_entries_map.get(day_date)
        if absence_type:
            absence_label = "Больничный" if absence_type == "sick" else "Отпуск"
            status_label = absence_label
            status_tone = "danger" if absence_type == "sick" else "warning"
            manager_label = format_full_name(department_manager)
            has_slot = False
            time_label = absence_label
        elif entry and entry.is_available:
            time_label = f"{entry.start_time:%H:%M}-{entry.end_time:%H:%M}"
            if entry.is_approved or week_has_approved:
                status_label = "Подтверждена"
                status_tone = "success"
            else:
                status_label = "На согласовании"
                status_tone = "warning"
            manager_label = format_full_name(entry.approved_by or department_manager)
            has_slot = True
        else:
            time_label = "Выходной"
            status_label = "Нет слота"
            status_tone = ""
            manager_label = format_full_name(department_manager)
            has_slot = False

        week_rows.append(
            {
                "date": day_date.isoformat(),
                "day_label": f"{weekday_names[day_date.weekday()]}, {day_date.day} {month_names[day_date.month - 1]}",
                "time_label": time_label,
                "status_label": status_label,
                "status_tone": status_tone,
                "tasks_label": "—",
                "manager_label": manager_label,
                "has_slot": has_slot,
            }
        )

    week_rows_display = week_rows
    if only_work:
        week_rows_display = [row for row in week_rows if row["has_slot"]]

    base_month_index = today.year * 12 + (today.month - 1) + month_offset
    month_year = base_month_index // 12
    month_month = base_month_index % 12 + 1
    month_start = date(month_year, month_month, 1)
    next_month_index = base_month_index + 1
    next_month_year = next_month_index // 12
    next_month_month = next_month_index % 12 + 1
    month_end = date(next_month_year, next_month_month, 1) - timedelta(days=1)
    month_label = f"{month_titles[month_month - 1]} {month_year}"

    month_entries = EmployeeAvailability.objects.filter(
        user=request.user,
        date__range=(month_start, month_end),
        is_available=True,
    )
    month_entries_map = {entry.date: entry for entry in month_entries}

    month_absences = EmployeeAbsence.objects.filter(
        user=request.user,
        start_date__lte=month_end,
        end_date__gte=month_start,
    )
    absence_by_date = {}
    for absence in month_absences:
        start_date = max(absence.start_date, month_start)
        end_date = min(absence.end_date, month_end)
        current = start_date
        while current <= end_date:
            if absence.absence_type == "sick" or current not in absence_by_date:
                absence_by_date[current] = absence.absence_type
            current += timedelta(days=1)

    month_weeks = []
    calendar_weeks = calendar.Calendar(firstweekday=calendar.MONDAY).monthdatescalendar(
        month_year,
        month_month,
    )
    for week in calendar_weeks:
        week_cells = []
        for day_date in week:
            is_current = day_date.month == month_month
            css_class = "is-empty" if not is_current else ""
            date_label = "-" if not is_current else str(day_date.day)
            shift_label = ""
            if is_current:
                absence_type = absence_by_date.get(day_date)
                if absence_type == "sick":
                    css_class = "is-sick"
                    shift_label = "Больничный"
                elif absence_type == "vacation":
                    css_class = "is-off"
                    shift_label = "Отпуск"
                elif day_date in month_entries_map:
                    entry = month_entries_map[day_date]
                    css_class = "is-work"
                    shift_label = f"{entry.start_time:%H:%M}-{entry.end_time:%H:%M}"
            week_cells.append(
                {
                    "date": day_date.isoformat(),
                    "date_label": date_label,
                    "css_class": css_class,
                    "shift_label": shift_label,
                }
            )
        month_weeks.append(week_cells)

    if view == "month":
        period_label = month_label
        prev_url = f"?view=month&month_offset={month_offset - 1}"
        next_url = f"?view=month&month_offset={month_offset + 1}"
    else:
        period_label = week_period_label
        work_param = "&only_work=1" if only_work else ""
        prev_url = f"?view=week&offset={week_offset - 1}{work_param}"
        next_url = f"?view=week&offset={week_offset + 1}{work_param}"

    return render(
        request,
        'dashboard/employee/schedule.html',
        {
            'active_tab': 'schedule',
            'page_title': 'Мой график',
            'page_subtitle': 'Неделя, месяц и детали слотов',
            'view': view,
            'period_label': period_label,
            'week_period_label': week_period_label,
            'month_label': month_label,
            'week_offset': week_offset,
            'month_offset': month_offset,
            'only_work': only_work,
            'prev_url': prev_url,
            'next_url': next_url,
            'week_rows': week_rows_display,
            'month_weeks': month_weeks,
            'weekday_short': weekday_short,
            **next_slot_context,
        },
    )


@login_required
@ensure_csrf_cookie
def employee_availability(request):
    _ensure_role(request, 'employee')
    today = timezone.localdate()
    next_slot_context = _get_employee_next_slot_context(request.user)
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

    week_is_approved = EmployeeAvailability.objects.filter(
        user=request.user,
        date__range=(week_start, week_end),
        is_approved=True,
    ).exists()
    is_locked = is_current_week or week_is_approved or next_week_edit_closed
    show_requests = is_current_week or week_is_approved or next_week_edit_closed
    edit_hint = ""
    if is_next_week:
        edit_hint = (
            "Редактирование следующей недели доступно только до пятницы."
            if next_week_edit_closed
            else "Редактирование доступно до пятницы."
        )

    weekly_hours_norm = settings_obj.weekly_hours_norm

    total_shifts = sum(1 for day in days if day["is_available"])
    total_minutes = sum(day["duration_minutes"] for day in days if day["is_available"])
    total_hours = total_minutes / 60 if total_minutes else 0
    total_hours_display = f"{total_hours:.1f}".replace(".", ",")
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
            'weekly_hours_norm': weekly_hours_norm,
            **next_slot_context,
        },
    )


def _employee_can_access_task(user, task):
    profile = EmployeeProfile.objects.filter(user=user).only("department_id").first()
    if not profile or profile.department_id != task.department_id:
        return False
    if task.task_type == "employee":
        return task.assigned_to_id == user.id
    if task.task_type == "department":
        return True
    if task.task_type == "slot":
        if not task.start_time or not task.end_time:
            return False
        return EmployeeAvailability.objects.filter(
            user=user,
            date=task.date,
            start_time=task.start_time,
            end_time=task.end_time,
            is_available=True,
        ).exists()
    return False


@login_required
def employee_tasks(request):
    _ensure_role(request, 'employee')
    user = request.user
    profile = (
        EmployeeProfile.objects.select_related("department", "department__manager")
        .filter(user=user)
        .first()
    )
    department = profile.department if profile else None
    department_name = department.name if department else "—"
    manager_label = "—"
    if department and department.manager:
        manager_label = department.manager.get_full_name().strip() or department.manager.username

    view_mode = (request.GET.get("view") or "active").strip().lower()
    if view_mode not in {"active", "archive"}:
        view_mode = "active"

    tasks_queryset = DepartmentTask.objects.none()
    availability_entries = []
    if department:
        base_tasks = DepartmentTask.objects.filter(department=department)
        assigned_tasks = base_tasks.filter(task_type="employee", assigned_to=user)
        department_tasks = base_tasks.filter(task_type="department")
        availability_entries = list(
            EmployeeAvailability.objects.filter(user=user, is_available=True)
        )
        slot_tasks = DepartmentTask.objects.none()
        if availability_entries:
            slot_filters = Q()
            for entry in availability_entries:
                slot_filters |= Q(
                    date=entry.date,
                    start_time=entry.start_time,
                    end_time=entry.end_time,
                )
            if slot_filters:
                slot_tasks = base_tasks.filter(task_type="slot").filter(slot_filters)
        tasks_queryset = (assigned_tasks | slot_tasks | department_tasks).distinct()

    submissions_qs = TaskSubmission.objects.select_related("author").order_by("-created_at")
    tasks_queryset = tasks_queryset.select_related("created_by").prefetch_related(
        Prefetch("submissions", queryset=submissions_qs)
    )

    now = timezone.localtime()
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

    def format_submission_date(value):
        if not value:
            return ""
        local = timezone.localtime(value)
        return f"{local.day} {month_names[local.month - 1]}, {local:%H:%M}"

    tasks_all = list(tasks_queryset)
    if view_mode == "archive":
        tasks_filtered = [task for task in tasks_all if task.status == "done"]
    else:
        tasks_filtered = [task for task in tasks_all if task.status != "done"]

    tasks_total = len(tasks_filtered)
    tasks_done = 0
    tasks_progress = 0
    tasks_todo = 0
    tasks_overdue = 0
    for task in tasks_filtered:
        if task.status == "done":
            tasks_done += 1
        elif task.status == "in_progress":
            tasks_progress += 1
        else:
            tasks_todo += 1
        if task.status != "done":
            due_time = task.due_time or task.end_time
            if task.date < now.date():
                tasks_overdue += 1
            elif due_time and task.date == now.date() and due_time < now.time():
                tasks_overdue += 1

    tasks_by_status = {"todo": [], "in_progress": [], "done": []}
    for task in tasks_filtered:
        slot_label = "—"
        if task.start_time and task.end_time:
            slot_label = f"{task.start_time:%H:%M}-{task.end_time:%H:%M}"

        due_label = "Срок не задан"
        if task.due_time:
            due_label = f"Срок: {task.date.day} {month_names[task.date.month - 1]}, {task.due_time:%H:%M}"
        elif task.end_time:
            due_label = f"Срок: {task.date.day} {month_names[task.date.month - 1]}, {task.end_time:%H:%M}"
        else:
            due_label = f"Дата: {task.date.day} {month_names[task.date.month - 1]}"

        is_overdue = False
        if task.status != "done":
            due_time = task.due_time or task.end_time
            if task.date < now.date():
                is_overdue = True
            elif due_time and task.date == now.date() and due_time < now.time():
                is_overdue = True

        status_tone = status_tones.get(task.status, "muted")
        if is_overdue:
            status_tone = "danger"

        created_by_label = manager_label
        if task.created_by:
            created_by_label = task.created_by.get_full_name().strip() or task.created_by.username

        submissions = list(task.submissions.all())
        needs_attention = False
        if task.status == "in_progress" and submissions:
            latest_author = submissions[0].author
            has_employee_submission = any(
                submission.author and submission.author.role == UserRole.EMPLOYEE
                for submission in submissions
            )
            if (
                latest_author
                and latest_author.role == UserRole.MANAGER
                and has_employee_submission
            ):
                needs_attention = True
        submissions_payload = []
        for submission in submissions[:3]:
            file_url = submission.attachment.url if submission.attachment else ""
            file_name = Path(submission.attachment.name).name if submission.attachment else ""
            submissions_payload.append(
                {
                    "id": submission.id,
                    "comment": submission.comment,
                    "file_url": file_url,
                    "file_name": file_name,
                    "created_label": format_submission_date(submission.created_at),
                }
            )
        extra_submissions = max(0, len(submissions) - len(submissions_payload))

        task_type_label = "Персональная"
        if task.task_type == "slot":
            task_type_label = "Слот"
        elif task.task_type == "department":
            task_type_label = "Общая"

        status_label = status_labels.get(task.status, "Назначена")
        if needs_attention:
            status_label = "Нужна доработка"
            status_tone = "danger"

        payload = {
            "id": task.id,
            "title": task.title,
            "description": task.description,
            "priority": task.priority,
            "priority_label": priority_labels.get(task.priority, "Средний"),
            "status": task.status,
            "status_label": status_label,
            "status_tone": status_tone,
            "task_type": task.task_type,
            "task_type_label": task_type_label,
            "slot_label": slot_label,
            "due_label": due_label,
            "is_overdue": is_overdue,
            "needs_attention": needs_attention,
            "created_by_label": created_by_label,
            "department_label": department_name,
            "submissions": submissions_payload,
            "extra_submissions": extra_submissions,
            "can_submit": task.status != "done",
        }
        tasks_by_status[task.status].append(payload)

    def sort_key(item):
        rank = priority_rank.get(item["priority"], 0)
        return (-rank, item["title"])

    for status in tasks_by_status:
        tasks_by_status[status].sort(key=sort_key)

    if view_mode == "archive":
        columns = [
            {
                "id": "done",
                "label": "Выполнено",
                "hint": "Сданные задачи и отчеты.",
                "tasks": tasks_by_status["done"],
            }
        ]
    else:
        columns = [
            {
                "id": "todo",
                "label": "Назначены",
                "hint": "Новые задачи и ожидание старта.",
                "tasks": tasks_by_status["todo"],
            },
            {
                "id": "in_progress",
                "label": "В работе",
                "hint": "Задачи в выполнении.",
                "tasks": tasks_by_status["in_progress"],
            },
        ]

    task_list = [
        *tasks_by_status["todo"],
        *tasks_by_status["in_progress"],
        *tasks_by_status["done"],
    ]

    return render(
        request,
        'dashboard/employee/tasks.html',
        {
            'active_tab': 'tasks',
            'page_title': 'Задачи',
            'page_subtitle': 'Контроль поручений и история выполнения',
            'task_view': view_mode,
            'department_name': department_name,
            'manager_label': manager_label,
            'tasks_total': tasks_total,
            'tasks_done': tasks_done,
            'tasks_progress': tasks_progress,
            'tasks_todo': tasks_todo,
            'tasks_overdue': tasks_overdue,
            'task_columns': columns,
            'task_list': task_list,
            **_get_employee_next_slot_context(request.user),
        },
    )


@login_required
def employee_task_detail(request, task_id):
    _ensure_role(request, 'employee')
    user = request.user
    task = get_object_or_404(DepartmentTask, id=task_id)
    if not _employee_can_access_task(user, task):
        raise PermissionDenied

    profile = (
        EmployeeProfile.objects.select_related("department", "department__manager")
        .filter(user=user)
        .first()
    )
    department = profile.department if profile else None
    department_label = department.name if department else "—"
    manager_label = "—"
    if task.created_by:
        manager_label = task.created_by.get_full_name().strip() or task.created_by.username
    elif department and department.manager:
        manager_label = department.manager.get_full_name().strip() or department.manager.username

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

    due_label = "Срок не задан"
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

    task_type_label = "Персональная"
    if task.task_type == "slot":
        task_type_label = "Слот"
    elif task.task_type == "department":
        task_type_label = "Общая"

    submissions = (
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

    needs_attention = False
    if task.status == "in_progress" and submissions:
        latest_author = submissions[0].author
        has_employee_submission = any(
            submission.author and submission.author.role == UserRole.EMPLOYEE
            for submission in submissions
        )
        if (
            latest_author
            and latest_author.role == UserRole.MANAGER
            and has_employee_submission
        ):
            needs_attention = True

    task_status_label = status_labels.get(task.status, "Назначена")
    if needs_attention:
        task_status_label = "Нужна доработка"
        status_tone = "danger"

    can_submit = task.status != "done" or task.task_type == "department"

    back_view = (request.GET.get("view") or "").strip()
    layout = (request.GET.get("layout") or "").strip()
    back_url = reverse("employee-tasks")
    query_parts = []
    if back_view in {"active", "archive"}:
        query_parts.append(f"view={back_view}")
    if layout in {"board", "list"}:
        query_parts.append(f"layout={layout}")
    if query_parts:
        back_url = f"{back_url}?{'&'.join(query_parts)}"
    back_url = _resolve_back_url(request, back_url)

    return render(
        request,
        "dashboard/employee/task_detail.html",
        {
            "active_tab": "tasks",
            "page_title": task.title,
            "page_subtitle": "Подробности задачи",
            "task": task,
            "task_due_label": due_label,
            "task_status_label": task_status_label,
            "task_status_tone": status_tone,
            "task_priority_label": priority_labels.get(task.priority, "Средний"),
            "task_type_label": task_type_label,
            "task_slot_label": slot_label,
            "manager_label": manager_label,
            "department_label": department_label,
            "submissions": submissions_payload,
            "can_submit": can_submit,
            "back_url": back_url,
            **_get_employee_next_slot_context(request.user),
        },
    )


@login_required
def employee_payroll(request):
    return _render_employee_page(
        request,
        'dashboard/employee/payroll.html',
        'payroll',
        'Отчеты',
        'Часы и загрузка',
    )


@login_required
def employee_notifications(request):
    return _render_employee_page(
        request,
        'dashboard/employee/notifications.html',
        'notifications',
        'Уведомления',
        'Все важные события по слотам и задачам',
    )
