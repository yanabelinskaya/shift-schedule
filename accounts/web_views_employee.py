from datetime import date, timedelta
from pathlib import Path

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .models import (
    DepartmentTask,
    EmployeeProfile,
    LeaveRequest,
    Sprint,
    Substitution,
    TaskSubmission,
    UserRole,
)
from .object_access import employee_can_access_task
from .web_views_shared import (
    _ensure_role,
    _get_employee_next_slot_context,
    _render_employee_page,
    _resolve_back_url,
)

@login_required
@require_http_methods(["GET"])
def employee_dashboard(request):
    _ensure_role(request, 'employee')
    user = request.user
    profile = EmployeeProfile.objects.select_related('department').filter(user=user).first()
    department = profile.department if profile else None

    today = timezone.localdate()
    now = timezone.localtime()

    month_names = [
        "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря",
    ]
    priority_labels = {"high": "Высокий", "mid": "Средний", "low": "Низкий"}
    status_labels = {
        "awaiting_confirmation": "Ожидает",
        "confirmed": "Подтверждено",
        "conflict": "Конфликт",
        "in_progress": "В процессе",
        "completed": "Выполнено",
    }
    status_tones = {
        "awaiting_confirmation": "muted",
        "confirmed": "success",
        "conflict": "danger",
        "in_progress": "warning",
        "completed": "success",
    }

    def fmt_date(d):
        return f"{d.day} {month_names[d.month - 1]}"

    def deadline_label(task):
        if task.date == today:
            base = "Сегодня"
        elif task.date == today + timedelta(days=1):
            base = "Завтра"
        else:
            base = fmt_date(task.date)
        if task.due_time:
            base += f", {task.due_time:%H:%M}"
        return base

    def is_task_overdue(task):
        if task.status == DepartmentTask.TaskStatus.COMPLETED:
            return False
        if task.date < today:
            return True
        if task.date == today and task.due_time and task.due_time < now.time():
            return True
        return False

    active_statuses = [
        DepartmentTask.TaskStatus.AWAITING_CONFIRMATION,
        DepartmentTask.TaskStatus.CONFIRMED,
        DepartmentTask.TaskStatus.IN_PROGRESS,
        DepartmentTask.TaskStatus.CONFLICT,
    ]

    in_progress_tasks = []
    upcoming_tasks = []

    if department:
        my_tasks = (
            DepartmentTask.objects.filter(
                department=department,
                status__in=active_statuses,
            )
            .filter(
                Q(task_type='employee', assigned_to=user) |
                Q(task_type='department', taken_by=user)
            )
            .distinct()
            .select_related('sprint')
            .order_by('date', 'due_time')
        )

        for task in my_tasks:
            overdue = is_task_overdue(task)
            tone = 'danger' if overdue else status_tones.get(task.status, 'muted')
            sprint_label = None
            if task.sprint:
                sprint_label = f"Спринт: {fmt_date(task.sprint.start_date)} — {fmt_date(task.sprint.end_date)}"
            item = {
                'id': task.id,
                'title': task.title,
                'priority': task.priority,
                'priority_label': priority_labels.get(task.priority, task.priority),
                'status': task.status,
                'status_label': status_labels.get(task.status, task.status),
                'status_tone': tone,
                'deadline_label': deadline_label(task),
                'sprint_label': sprint_label,
                'is_overdue': overdue,
            }
            if task.status == DepartmentTask.TaskStatus.IN_PROGRESS:
                in_progress_tasks.append(item)
            else:
                upcoming_tasks.append(item)

    # Workload
    total_active = len(in_progress_tasks) + len(upcoming_tasks)
    if total_active == 0:
        workload_label, workload_tone, workload_pct = "Свободен", "success", 0
    elif total_active <= 2:
        workload_label, workload_tone, workload_pct = "Нормальная загрузка", "success", 40
    elif total_active <= 5:
        workload_label, workload_tone, workload_pct = "Высокая загрузка", "warning", 70
    else:
        workload_label, workload_tone, workload_pct = "Перегружен", "danger", 100

    # Notifications derived from recent events
    notifications = []
    since = timezone.now() - timedelta(days=14)

    if department:
        # New tasks assigned in the last 14 days
        new_tasks = (
            DepartmentTask.objects.filter(
                department=department,
                created_at__gte=since,
            )
            .filter(
                Q(task_type='employee', assigned_to=user) |
                Q(task_type='department')
            )
            .order_by('-created_at')[:6]
        )
        for task in new_tasks:
            notifications.append({
                'kind': 'task',
                'text': f'Новая задача: {task.title}',
                'url': reverse('employee-task-detail', args=[task.id]),
                'date_label': fmt_date(task.date),
                'ts': task.created_at,
            })

        # Leave requests reviewed
        reviewed = LeaveRequest.objects.filter(
            user=user,
            reviewed_at__gte=since,
            status__in=['approved', 'rejected'],
        ).order_by('-reviewed_at')[:4]
        for lr in reviewed:
            verb = 'одобрена' if lr.status == 'approved' else 'отклонена'
            type_label = 'Отпуск' if lr.request_type == 'vacation' else 'Больничный'
            notifications.append({
                'kind': 'leave',
                'text': f'Заявка «{type_label}» {verb}',
                'url': reverse('employee-requests'),
                'date_label': fmt_date(lr.start_date),
                'ts': lr.reviewed_at,
            })

        # Substitutions where user is the substitute
        subs = (
            Substitution.objects.filter(
                substitute_user=user,
                end_date__gte=today,
            )
            .select_related('absent_user')
            .order_by('start_date')[:3]
        )
        for sub in subs:
            absent_name = sub.absent_user.get_full_name().strip() or sub.absent_user.username
            notifications.append({
                'kind': 'sub',
                'text': f'Вы назначены заменой для {absent_name}',
                'url': '#',
                'date_label': f'{fmt_date(sub.start_date)} — {fmt_date(sub.end_date)}',
                'ts': sub.created_at,
            })

    notifications.sort(key=lambda n: n['ts'], reverse=True)

    return render(request, 'dashboard/employee/dashboard.html', {
        'active_tab': 'dashboard',
        'page_title': 'Дашборд',
        'page_subtitle': f'Добро пожаловать, {user.get_full_name().strip() or user.username}',
        'department_name': department.name if department else '—',
        'in_progress_tasks': in_progress_tasks,
        'upcoming_tasks': upcoming_tasks[:8],
        'notifications': notifications[:10],
        'total_active': total_active,
        'workload_label': workload_label,
        'workload_tone': workload_tone,
        'workload_pct': workload_pct,
    })


def employee_schedule(request):  # kept for legacy URL compatibility
    return redirect('employee-tasks')
def employee_availability(request):  # kept for legacy URL compatibility
    return redirect('employee-tasks')


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

    task_filter = (request.GET.get("filter") or "all").strip().lower()
    valid_filters = {"all", "new", "in_progress", "on_review", "done", "overdue"}
    if task_filter not in valid_filters:
        task_filter = "all"
    search_query = (request.GET.get("q") or "").strip()

    tasks_queryset = DepartmentTask.objects.none()
    if department:
        base_tasks = DepartmentTask.objects.filter(department=department)
        assigned_tasks = base_tasks.filter(task_type="employee", assigned_to=user)
        department_tasks = base_tasks.filter(task_type="department")
        tasks_queryset = (assigned_tasks | department_tasks).distinct()

    submissions_qs = TaskSubmission.objects.select_related("author").order_by("-created_at")
    tasks_queryset = tasks_queryset.select_related("created_by", "sprint").prefetch_related(
        Prefetch("submissions", queryset=submissions_qs)
    )
    if search_query:
        tasks_queryset = tasks_queryset.filter(title__icontains=search_query)

    now = timezone.localtime()
    month_names = [
        "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря",
    ]
    priority_rank = {"high": 3, "mid": 2, "low": 1}
    priority_labels = {"high": "Высокий", "mid": "Средний", "low": "Низкий"}
    status_labels = {
        "awaiting_confirmation": "Новая",
        "confirmed": "Взята",
        "in_progress": "В работе",
        "on_review": "На проверке",
        "completed": "Выполнена",
        "returned": "Возвращена на доработку",
        "conflict": "Конфликт",
    }
    status_tones = {
        "awaiting_confirmation": "muted",
        "confirmed": "info",
        "in_progress": "warning",
        "on_review": "info",
        "completed": "success",
        "returned": "danger",
        "conflict": "danger",
    }

    tasks_all = list(tasks_queryset)

    tasks_total_all = len(tasks_all)
    tasks_new = sum(1 for t in tasks_all if t.status == "awaiting_confirmation")
    tasks_progress = sum(1 for t in tasks_all if t.status in ("confirmed", "in_progress", "returned"))
    tasks_on_review = sum(1 for t in tasks_all if t.status == "on_review")
    tasks_done = sum(1 for t in tasks_all if t.status == "completed")

    def _is_overdue(task):
        if task.status in ("completed",):
            return False
        due_time = getattr(task, 'due_time', None) or getattr(task, 'end_time', None)
        if task.date < now.date():
            return True
        if due_time and task.date == now.date() and due_time < now.time():
            return True
        return False

    tasks_overdue = sum(1 for t in tasks_all if _is_overdue(t))

    def _apply_filter(tasks):
        if task_filter == "new":
            return [t for t in tasks if t.status == "awaiting_confirmation"]
        if task_filter == "in_progress":
            return [t for t in tasks if t.status in ("confirmed", "in_progress", "returned")]
        if task_filter == "on_review":
            return [t for t in tasks if t.status == "on_review"]
        if task_filter == "done":
            return [t for t in tasks if t.status == "completed"]
        if task_filter == "overdue":
            return [t for t in tasks if _is_overdue(t)]
        return tasks

    tasks_filtered = _apply_filter(tasks_all)

    task_cards = []
    for task in tasks_filtered:
        if task.due_time:
            due_label = f"{task.date.day} {month_names[task.date.month - 1]}, {task.due_time:%H:%M}"
        else:
            due_label = f"{task.date.day} {month_names[task.date.month - 1]}"

        is_overdue = _is_overdue(task)
        status_tone = status_tones.get(task.status, "muted")
        if is_overdue and task.status != "completed":
            status_tone = "danger"

        submissions = list(task.submissions.all())
        files_count = sum(1 for s in submissions if s.attachment)
        comments_count = len(submissions)

        sprint_label = ""
        if task.sprint_id:
            sprint_label = task.sprint.title if task.sprint else ""

        description_short = task.description[:120].rstrip() if task.description else ""
        if task.description and len(task.description) > 120:
            description_short += "…"

        task_cards.append({
            "id": task.id,
            "title": task.title,
            "description_short": description_short,
            "priority": task.priority,
            "priority_label": priority_labels.get(task.priority, "Средний"),
            "status": task.status,
            "status_label": status_labels.get(task.status, task.status),
            "status_tone": status_tone,
            "due_label": due_label,
            "is_overdue": is_overdue,
            "sprint_label": sprint_label,
            "files_count": files_count,
            "comments_count": comments_count,
        })

    priority_rank_key = {"high": 0, "mid": 1, "low": 2}
    status_order = {
        "returned": 0, "on_review": 1, "in_progress": 2,
        "confirmed": 3, "awaiting_confirmation": 4, "completed": 5, "conflict": 6,
    }
    task_cards.sort(key=lambda c: (
        status_order.get(c["status"], 99),
        priority_rank_key.get(c["priority"], 1),
        c["title"],
    ))

    filter_counts = {
        "all": tasks_total_all,
        "new": tasks_new,
        "in_progress": tasks_progress,
        "on_review": tasks_on_review,
        "done": tasks_done,
        "overdue": tasks_overdue,
    }

    return render(
        request,
        'dashboard/employee/tasks.html',
        {
            'active_tab': 'tasks',
            'page_title': 'Мои задачи',
            'page_subtitle': 'Задачи, назначенные вам или взятые самостоятельно',
            'task_filter': task_filter,
            'search_query': search_query,
            'department_name': department_name,
            'manager_label': manager_label,
            'task_cards': task_cards,
            'filter_counts': filter_counts,
            **_get_employee_next_slot_context(request.user),
        },
    )


@login_required
def employee_task_detail(request, task_id):
    _ensure_role(request, 'employee')
    user = request.user
    task = get_object_or_404(DepartmentTask, id=task_id)
    if not employee_can_access_task(user, task):
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
        "awaiting_confirmation": "Новая",
        "confirmed": "Взята",
        "in_progress": "В работе",
        "on_review": "На проверке",
        "completed": "Выполнена",
        "returned": "Возвращена на доработку",
        "conflict": "Конфликт",
    }
    status_tones = {
        "awaiting_confirmation": "muted",
        "confirmed": "info",
        "in_progress": "warning",
        "on_review": "info",
        "completed": "success",
        "returned": "danger",
        "conflict": "danger",
    }

    due_label = "Срок не задан"
    if task.due_time:
        due_label = f"{task.date.day} {month_names[task.date.month - 1]}, {task.due_time:%H:%M}"
    else:
        due_label = f"{task.date.day} {month_names[task.date.month - 1]}"

    is_overdue = False
    now = timezone.localtime()
    if task.status != "completed":
        if task.date < now.date():
            is_overdue = True
        elif task.due_time and task.date == now.date() and task.due_time < now.time():
            is_overdue = True

    status_tone = status_tones.get(task.status, "muted")
    if is_overdue:
        status_tone = "danger"

    task_type_label = "Персональная"
    if task.task_type == "department":
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

    task_status_label = status_labels.get(task.status, task.status)

    can_add_comment = task.status not in ("completed",)
    can_take = task.status == "awaiting_confirmation" and not task.taken_by_id
    can_drop = task.status in ("awaiting_confirmation", "confirmed") and task.taken_by_id == user.id
    can_start = task.status == "confirmed"
    can_send_review = task.status == "in_progress"
    can_resume = task.status == "returned"

    sprint_label = ""
    sprint_dates = ""
    if task.sprint_id:
        sprint = task.sprint if hasattr(task, '_sprint_cache') else None
        if not sprint:
            try:
                from .models import Sprint as SprintModel
                sprint = SprintModel.objects.get(id=task.sprint_id)
            except Exception:
                sprint = None
        if sprint:
            sprint_label = sprint.title
            sprint_dates = (
                f"{sprint.start_date.day} {month_names[sprint.start_date.month - 1]} — "
                f"{sprint.end_date.day} {month_names[sprint.end_date.month - 1]}"
            )

    back_url = _resolve_back_url(request, reverse("employee-tasks"))

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
            "manager_label": manager_label,
            "department_label": department_label,
            "sprint_label": sprint_label,
            "sprint_dates": sprint_dates,
            "submissions": submissions_payload,
            "can_add_comment": can_add_comment,
            "can_take": can_take,
            "can_drop": can_drop,
            "can_start": can_start,
            "can_send_review": can_send_review,
            "can_resume": can_resume,
            "back_url": back_url,
            **_get_employee_next_slot_context(request.user),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def employee_sprint(request):
    _ensure_role(request, 'employee')
    user = request.user
    profile = EmployeeProfile.objects.select_related('department').filter(user=user).first()
    department = profile.department if profile else None

    current_sprint = None
    tasks = []
    available_tasks = []
    my_tasks = []
    in_progress_tasks = []
    completed_tasks = []
    tasks_done = 0
    progress_pct = 0
    sprint_goal = ''
    month_names = ['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря']
    chat_error = request.session.pop('employee_sprint_chat_error', '')

    if department:
        current_sprint = (
            Sprint.objects.filter(department=department, status='active')
            .order_by('start_date')
            .first()
        )

        if current_sprint:
            if request.method == 'POST':
                task_id = request.POST.get('task_id')
                action = request.POST.get('action')
                if task_id and action in ('take', 'drop', 'comment'):
                    try:
                        task = DepartmentTask.objects.get(id=task_id, sprint=current_sprint)
                        if action == 'take' and task.taken_by is None and task.status != DepartmentTask.TaskStatus.COMPLETED:
                            task.taken_by = user
                            if task.status == DepartmentTask.TaskStatus.AWAITING_CONFIRMATION:
                                task.status = DepartmentTask.TaskStatus.CONFIRMED
                            task.save(update_fields=['taken_by', 'status'])
                        elif action == 'drop' and task.taken_by_id == user.id and task.status not in (
                            DepartmentTask.TaskStatus.COMPLETED,
                            DepartmentTask.TaskStatus.IN_PROGRESS,
                        ):
                            task.taken_by = None
                            task.status = DepartmentTask.TaskStatus.AWAITING_CONFIRMATION
                            task.save(update_fields=['taken_by', 'status'])
                        elif action == 'comment':
                            comment = request.POST.get('comment', '').strip()
                            attachments = request.FILES.getlist('attachments')
                            if not comment and not attachments:
                                request.session['employee_sprint_chat_error'] = 'Добавьте сообщение или файл.'
                            elif len(attachments) > 5:
                                request.session['employee_sprint_chat_error'] = 'Можно прикрепить не больше 5 файлов за раз.'
                            else:
                                max_attachment_size = 10 * 1024 * 1024
                                allowed_suffixes = {
                                    '.pdf', '.jpg', '.jpeg', '.png', '.txt', '.doc', '.docx', '.xls', '.xlsx',
                                }
                                invalid_attachment = next(
                                    (
                                        attachment for attachment in attachments
                                        if Path(attachment.name).suffix.lower() not in allowed_suffixes
                                        or attachment.size > max_attachment_size
                                    ),
                                    None,
                                )
                                if invalid_attachment:
                                    request.session['employee_sprint_chat_error'] = 'Файлы должны быть PDF, JPG, PNG, TXT, DOC, DOCX, XLS или XLSX до 10 МБ.'
                                elif attachments:
                                    for index, attachment in enumerate(attachments):
                                        TaskSubmission.objects.create(
                                            task=task,
                                            author=user,
                                            comment=comment if index == 0 else '',
                                            attachment=attachment,
                                        )
                                else:
                                    TaskSubmission.objects.create(
                                        task=task,
                                        author=user,
                                        comment=comment,
                                    )
                    except DepartmentTask.DoesNotExist:
                        pass
                return redirect('employee-sprint')

            status_labels = {
                'awaiting_confirmation': 'Ожидает',
                'confirmed': 'Подтверждена',
                'conflict': 'Конфликт',
                'in_progress': 'В работе',
                'completed': 'Завершена',
            }
            status_tones = {
                'awaiting_confirmation': 'muted',
                'confirmed': 'success',
                'conflict': 'danger',
                'in_progress': 'warning',
                'completed': 'success',
            }
            priority_labels = {'high': 'Высокий', 'mid': 'Средний', 'low': 'Низкий'}
            priority_order = {'high': 0, 'mid': 1, 'low': 2}
            status_progress = {
                DepartmentTask.TaskStatus.AWAITING_CONFIRMATION: 0,
                DepartmentTask.TaskStatus.CONFIRMED: 35,
                DepartmentTask.TaskStatus.CONFLICT: 25,
                DepartmentTask.TaskStatus.IN_PROGRESS: 70,
                DepartmentTask.TaskStatus.COMPLETED: 100,
            }

            tasks_qs = (
                DepartmentTask.objects.filter(sprint=current_sprint)
                .select_related('taken_by', 'assigned_to')
                .prefetch_related(
                    Prefetch(
                        'submissions',
                        queryset=TaskSubmission.objects.select_related('author').order_by('-created_at'),
                    )
                )
            )
            for task in sorted(tasks_qs, key=lambda t: (priority_order.get(t.priority, 9), t.title)):
                taken_by_name = ''
                if task.taken_by:
                    taken_by_name = task.taken_by.get_full_name().strip() or task.taken_by.username
                due_label = ''
                if task.date:
                    due_label = task.date.strftime('%d.%m.%Y')
                    if task.due_time:
                        due_label += f' {task.due_time.strftime("%H:%M")}'
                is_mine = task.taken_by_id == user.id or task.assigned_to_id == user.id
                now = timezone.localtime()
                is_overdue = False
                if task.status != DepartmentTask.TaskStatus.COMPLETED:
                    if task.date < now.date():
                        is_overdue = True
                    elif task.date == now.date() and task.due_time and task.due_time < now.time():
                        is_overdue = True
                latest_submission = None
                task_submissions = list(task.submissions.all())
                submissions_payload = []
                for submission in task_submissions[:4]:
                    submission_author = '—'
                    if submission.author:
                        submission_author = submission.author.get_full_name().strip() or submission.author.username
                    submission_created = timezone.localtime(submission.created_at)
                    submissions_payload.append({
                        'author': submission_author,
                        'comment': submission.comment,
                        'created_label': f'{submission_created.day} {month_names[submission_created.month - 1]}, {submission_created:%H:%M}',
                        'file_url': submission.attachment.url if submission.attachment else '',
                        'file_name': Path(submission.attachment.name).name if submission.attachment else '',
                    })
                if task_submissions:
                    latest = task_submissions[0]
                    latest_author = latest.author.get_full_name().strip() or latest.author.username if latest.author else '—'
                    latest_created = timezone.localtime(latest.created_at)
                    latest_submission = {
                        'author': latest_author,
                        'comment': latest.comment or 'Прикреплен файл',
                        'created_label': f'{latest_created.day} {month_names[latest_created.month - 1]}, {latest_created:%H:%M}',
                        'has_file': bool(latest.attachment),
                    }
                tasks.append({
                    'id': task.id,
                    'title': task.title,
                    'description': task.description or '',
                    'priority': task.priority,
                    'priority_label': priority_labels.get(task.priority, task.priority),
                    'status': task.status,
                    'status_label': status_labels.get(task.status, task.status),
                    'status_tone': status_tones.get(task.status, ''),
                    'taken_by_id': task.taken_by_id,
                    'taken_by_name': taken_by_name,
                    'is_mine': is_mine,
                    'can_take': task.taken_by is None and task.status != DepartmentTask.TaskStatus.COMPLETED,
                    'can_drop': (
                        task.taken_by_id == user.id
                        and task.status not in (
                            DepartmentTask.TaskStatus.COMPLETED,
                            DepartmentTask.TaskStatus.IN_PROGRESS,
                        )
                    ),
                    'due_label': due_label,
                    'is_overdue': is_overdue,
                    'progress': status_progress.get(task.status, 0),
                    'latest_submission': latest_submission,
                    'submissions': submissions_payload,
                    'submissions_count': len(task_submissions),
                })

            tasks_done = sum(1 for t in tasks if t['status'] == DepartmentTask.TaskStatus.COMPLETED)
            progress_pct = round((tasks_done / len(tasks)) * 100) if tasks else 0
            available_tasks = [
                t for t in tasks
                if t['can_take']
            ]
            my_tasks = [
                t for t in tasks
                if t['is_mine'] and t['status'] not in (
                    DepartmentTask.TaskStatus.IN_PROGRESS,
                    DepartmentTask.TaskStatus.COMPLETED,
                )
            ]
            in_progress_tasks = [
                t for t in tasks
                if t['status'] == DepartmentTask.TaskStatus.IN_PROGRESS
            ]
            completed_tasks = [
                t for t in tasks
                if t['status'] == DepartmentTask.TaskStatus.COMPLETED
            ]
            sprint_goal = current_sprint.goal or 'Выполнить задачи, запланированные менеджером на текущий рабочий период.'

    sprint_period = ''
    if current_sprint:
        s, e = current_sprint.start_date, current_sprint.end_date
        if s.month == e.month:
            sprint_period = f'{s.day}–{e.day} {month_names[e.month - 1]} {e.year}'
        else:
            sprint_period = f'{s.day} {month_names[s.month - 1]} — {e.day} {month_names[e.month - 1]} {e.year}'

    subtitle = sprint_period if current_sprint else 'Нет активного спринта'

    return render(
        request,
        'dashboard/employee/sprint.html',
        {
            'active_tab': 'sprint',
            'page_title': current_sprint.title if current_sprint else 'Спринт',
            'page_subtitle': subtitle,
            'current_sprint': current_sprint,
            'sprint_period': sprint_period,
            'sprint_goal': sprint_goal,
            'tasks': tasks,
            'available_tasks': available_tasks,
            'my_tasks': my_tasks,
            'in_progress_tasks': in_progress_tasks,
            'completed_tasks': completed_tasks,
            'tasks_total': len(tasks),
            'tasks_done': tasks_done,
            'tasks_free': len(available_tasks),
            'tasks_mine': sum(1 for t in tasks if t['is_mine']),
            'tasks_in_progress': len(in_progress_tasks),
            'progress_pct': progress_pct,
            'chat_error': chat_error,
            **_get_employee_next_slot_context(user),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def employee_requests(request):
    _ensure_role(request, 'employee')
    user = request.user
    errors = []
    success = request.session.pop('employee_request_success', '')
    show_form = False
    form_data = {
        'request_type': '',
        'start_date': '',
        'start_display': '',
        'end_date': '',
        'end_display': '',
        'comment': '',
    }
    invalid_fields = []

    if request.method == 'POST':
        show_form = True
        request_type = request.POST.get('request_type', '').strip()
        start_date_str = request.POST.get('start_date', '').strip()
        end_date_str = request.POST.get('end_date', '').strip()
        comment = request.POST.get('comment', '').strip()
        attachment = request.FILES.get('attachment')
        form_data.update({
            'request_type': request_type,
            'start_date': start_date_str,
            'end_date': end_date_str,
            'comment': comment,
        })

        if request_type not in ('vacation', 'sick'):
            errors.append('Выберите тип заявки.')
            invalid_fields.append('request_type')

        start = None
        end = None
        today = timezone.localdate()
        vacation_min_date = today + timedelta(days=7)

        if not start_date_str:
            errors.append('Укажите дату начала.')
            invalid_fields.append('start_date')
        else:
            try:
                start = date.fromisoformat(start_date_str)
                form_data['start_display'] = start.strftime('%d.%m.%Y')
            except (ValueError, TypeError):
                errors.append('Неверный формат даты начала.')
                invalid_fields.append('start_date')

        if not end_date_str:
            errors.append('Укажите дату окончания.')
            invalid_fields.append('end_date')
        else:
            try:
                end = date.fromisoformat(end_date_str)
                form_data['end_display'] = end.strftime('%d.%m.%Y')
            except (ValueError, TypeError):
                errors.append('Неверный формат даты окончания.')
                invalid_fields.append('end_date')

        if start and request_type == 'vacation' and start < vacation_min_date:
            errors.append('Отпуск можно подать минимум за 7 дней до начала.')
            invalid_fields.append('start_date')
        elif start and request_type == 'sick' and start < today:
            errors.append('Дата начала больничного не может быть в прошлом.')
            invalid_fields.append('start_date')

        if start and end and end < start:
            errors.append('Дата окончания не может быть раньше даты начала.')
            invalid_fields.append('end_date')

        if attachment:
            max_attachment_size = 10 * 1024 * 1024
            allowed_suffixes = {'.pdf', '.jpg', '.jpeg', '.png'}
            suffix = Path(attachment.name).suffix.lower()
            if suffix not in allowed_suffixes:
                errors.append('Документ должен быть в формате PDF, JPG или PNG.')
                invalid_fields.append('attachment')
            if attachment.size > max_attachment_size:
                errors.append('Размер документа не должен превышать 10 МБ.')
                invalid_fields.append('attachment')

        if not errors:
            LeaveRequest.objects.create(
                user=user,
                request_type=request_type,
                start_date=start,
                end_date=end,
                comment=comment,
                attachment=attachment,
            )
            request.session['employee_request_success'] = 'Заявка отправлена на рассмотрение.'
            return redirect(reverse('employee-requests'))

    status_labels = {'pending': 'На рассмотрении', 'approved': 'Одобрено', 'rejected': 'Отклонено'}
    status_tones = {'pending': 'warning', 'approved': 'success', 'rejected': 'danger'}
    type_labels = {'vacation': 'Отпуск', 'sick': 'Больничный'}

    month_names = [
        "янв", "фев", "мар", "апр", "май", "июн",
        "июл", "авг", "сен", "окт", "ноя", "дек",
    ]

    def fmt(d):
        return f"{d.day} {month_names[d.month - 1]} {d.year}"

    qs = LeaveRequest.objects.filter(user=user).order_by('-created_at')

    leave_requests = []
    for lr in qs:
        attachment_url = lr.attachment.url if lr.attachment else ''
        attachment_name = lr.attachment.name.split('/')[-1] if lr.attachment else ''
        created_local = timezone.localtime(lr.created_at)
        leave_requests.append({
            'id': lr.id,
            'request_type': lr.request_type,
            'type_label': type_labels.get(lr.request_type, lr.request_type),
            'start_display': fmt(lr.start_date),
            'end_display': fmt(lr.end_date),
            'comment': lr.comment or '',
            'status': lr.status,
            'status_label': status_labels.get(lr.status, lr.status),
            'status_tone': status_tones.get(lr.status, ''),
            'rejection_reason': lr.rejection_reason or '',
            'attachment_url': attachment_url,
            'attachment_name': attachment_name,
            'created_display': fmt(created_local.date()),
        })

    counts = {
        'all': LeaveRequest.objects.filter(user=user).count(),
        'pending': LeaveRequest.objects.filter(user=user, status='pending').count(),
        'approved': LeaveRequest.objects.filter(user=user, status='approved').count(),
        'rejected': LeaveRequest.objects.filter(user=user, status='rejected').count(),
    }

    return render(
        request,
        'dashboard/employee/requests.html',
        {
            'active_tab': 'requests',
            'page_title': 'Заявки',
            'page_subtitle': 'Отпуска и больничные',
            'leave_requests': leave_requests,
            'errors': errors,
            'success': success,
            'show_form': show_form,
            'form_data': form_data,
            'invalid_fields': invalid_fields,
            'counts': counts,
        },
    )


@login_required
def employee_chat(request):
    return _render_employee_page(
        request,
        'dashboard/employee/chat.html',
        'chat',
        'Чаты',
        'Обсуждения задач с командой',
    )


@login_required
@require_http_methods(["GET"])
def employee_profile(request):
    _ensure_role(request, 'employee')
    user = request.user
    profile = EmployeeProfile.objects.select_related('department').filter(user=user).first()
    department = profile.department if profile else None

    today = timezone.localdate()
    month_names = [
        "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря",
    ]

    def fmt_date(d):
        return f"{d.day} {month_names[d.month - 1]} {d.year}"

    # ── Task statistics ────────────────────────────────────────────────────
    tasks_completed = 0
    tasks_active = 0
    tasks_overdue = 0
    sprints_count = 0

    if department:
        all_tasks = DepartmentTask.objects.filter(
            Q(task_type='employee', assigned_to=user) |
            Q(task_type='department', taken_by=user),
            department=department,
        ).distinct()

        tasks_completed = all_tasks.filter(status=DepartmentTask.TaskStatus.COMPLETED).count()
        active_statuses = [
            DepartmentTask.TaskStatus.AWAITING_CONFIRMATION,
            DepartmentTask.TaskStatus.CONFIRMED,
            DepartmentTask.TaskStatus.IN_PROGRESS,
        ]
        tasks_active = all_tasks.filter(status__in=active_statuses).count()
        tasks_overdue = all_tasks.filter(
            status__in=active_statuses,
            date__lt=today,
        ).count()
        sprints_count = (
            Sprint.objects.filter(
                department=department,
                tasks__taken_by=user,
            )
            .distinct()
            .count()
        )

    # ── Leave request history ─────────────────────────────────────────────
    leave_status_labels = {
        'pending': 'На рассмотрении',
        'approved': 'Одобрено',
        'rejected': 'Отклонено',
    }
    leave_status_tones = {
        'pending': 'warning',
        'approved': 'success',
        'rejected': 'danger',
    }
    leave_type_labels = {
        'vacation': 'Отпуск',
        'sick': 'Больничный',
    }
    leave_type_icons = {
        'vacation': 'vacation',
        'sick': 'sick',
    }

    leave_requests_raw = LeaveRequest.objects.filter(user=user).order_by('-created_at')[:20]
    leave_requests = []
    for lr in leave_requests_raw:
        leave_requests.append({
            'id': lr.id,
            'type_label': leave_type_labels.get(lr.request_type, lr.request_type),
            'type_icon': leave_type_icons.get(lr.request_type, 'vacation'),
            'start_display': fmt_date(lr.start_date),
            'end_display': fmt_date(lr.end_date),
            'status': lr.status,
            'status_label': leave_status_labels.get(lr.status, lr.status),
            'status_tone': leave_status_tones.get(lr.status, 'neutral'),
            'comment': lr.comment,
            'rejection_reason': lr.rejection_reason,
        })

    avatar_url = ''
    if profile and profile.avatar:
        avatar_url = request.build_absolute_uri(profile.avatar.url)

    return render(request, 'dashboard/employee/profile.html', {
        'active_tab': 'profile',
        'page_title': 'Мой профиль',
        'page_subtitle': 'Личная информация и статистика',
        'user': user,
        'profile': profile,
        'avatar_url': avatar_url,
        'department_name': department.name if department else '—',
        'position': (profile.position if profile else '') or '—',
        'tasks_completed': tasks_completed,
        'tasks_active': tasks_active,
        'tasks_overdue': tasks_overdue,
        'sprints_count': sprints_count,
        'leave_requests': leave_requests,
    })
