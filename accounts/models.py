from datetime import time
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models


def _default_shift_templates():
    return [
        "07:00-15:00",
        "15:00-23:00",
        "10:00-18:00",
        "08:00-16:00",
        "12:00-20:00",
    ]


class UserRole(models.TextChoices):
    ADMIN = 'admin', 'Администратор'
    MANAGER = 'manager', 'Менеджер'
    EMPLOYEE = 'employee', 'Сотрудник'


class User(AbstractUser):
    role = models.CharField(
        max_length=20,
        choices=UserRole.choices,
        default=UserRole.EMPLOYEE,
    )


class Department(models.Model):
    name = models.CharField(max_length=120, unique=True)
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='managed_departments',
    )
    is_archived = models.BooleanField(default=False)

    def __str__(self):
        return self.name


class DepartmentPosition(models.Model):
    department = models.ForeignKey(
        Department,
        on_delete=models.CASCADE,
        related_name='positions',
    )
    title = models.CharField(max_length=120)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['department', 'title'], name='unique_department_position'),
        ]

    def __str__(self):
        return f'{self.department}: {self.title}'


class EmployeeProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='profile',
    )
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)
    middle_name = models.CharField(max_length=120, blank=True)
    corporate_phone = models.CharField(max_length=30, blank=True)
    position = models.CharField(max_length=120, blank=True)
    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='employees',
    )
    monthly_salary = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    personal_phone = models.CharField(max_length=30, blank=True)
    address = models.CharField(max_length=255, blank=True)
    passport_series = models.CharField(max_length=10, blank=True)
    passport_number = models.CharField(max_length=10, blank=True)
    passport_issued_by = models.CharField(max_length=255, blank=True)
    passport_issue_date = models.DateField(null=True, blank=True)
    snils = models.CharField(max_length=20, blank=True)
    inn = models.CharField(max_length=20, blank=True)
    salary_reason = models.CharField(max_length=255, blank=True)
    department_change_reason = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f'{self.user.username} profile'


class EmployeeAbsence(models.Model):
    ABSENCE_TYPES = [
        ("vacation", "Отпуск"),
        ("sick", "Больничный"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="absences",
    )
    absence_type = models.CharField(max_length=20, choices=ABSENCE_TYPES)
    start_date = models.DateField()
    end_date = models.DateField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_absences",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_date", "-end_date"]
        constraints = [
            models.CheckConstraint(
                check=models.Q(end_date__gte=models.F("start_date")),
                name="chk_absence_dates_order",
            ),
        ]

    def __str__(self):
        return f"{self.user_id} {self.absence_type} {self.start_date}..{self.end_date}"


class EmployeeAvailability(models.Model):
    PRIORITY_CHOICES = [
        ("high", "Очень хочу"),
        ("mid", "Ок"),
        ("low", "Не хочу"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="availability_entries",
    )
    date = models.DateField()
    is_available = models.BooleanField(default=False)
    start_time = models.TimeField()
    end_time = models.TimeField()
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default="mid")
    is_approved = models.BooleanField(default=False)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_availability_entries",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "date"], name="unique_employee_availability"),
            models.CheckConstraint(
                check=models.Q(end_time__gt=models.F("start_time")),
                name="chk_availability_time_order",
            ),
        ]
        ordering = ["date"]

    def __str__(self):
        return f"{self.user_id} availability {self.date}"


class EmployeeBaseAvailability(models.Model):
    MODE_CHOICES = [
        ("off", "Недоступен"),
        ("fixed", "Фиксированные часы"),
        ("flex", "Свободный график"),
    ]

    WEEKDAY_CHOICES = [
        (0, "Понедельник"),
        (1, "Вторник"),
        (2, "Среда"),
        (3, "Четверг"),
        (4, "Пятница"),
        (5, "Суббота"),
        (6, "Воскресенье"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="base_availability_rules",
    )
    weekday = models.PositiveSmallIntegerField(choices=WEEKDAY_CHOICES)
    mode = models.CharField(max_length=20, choices=MODE_CHOICES, default="off")
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["weekday"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "weekday"],
                name="unique_employee_base_availability_weekday",
            ),
            models.CheckConstraint(
                check=(
                    models.Q(start_time__isnull=True)
                    | models.Q(end_time__isnull=True)
                    | models.Q(end_time__gt=models.F("start_time"))
                ),
                name="chk_base_availability_time_order",
            ),
        ]

    def __str__(self):
        return f"{self.user_id} base availability weekday {self.weekday}"


class EmployeeAvailabilityOverride(models.Model):
    OVERRIDE_TYPE_CHOICES = [
        ("unavailable", "Недоступен"),
        ("partial", "Частичная доступность"),
        ("available", "Я доступен весь день"),
        ("vacation", "Отпуск"),
        ("sick", "Больничный"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="availability_overrides",
    )
    date = models.DateField()
    override_type = models.CharField(max_length=20, choices=OVERRIDE_TYPE_CHOICES, default="unavailable")
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_availability_overrides",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["date", "id"]
        constraints = [
            models.UniqueConstraint(fields=["user", "date"], name="unique_employee_availability_override_date"),
            models.CheckConstraint(
                check=(
                    models.Q(start_time__isnull=True)
                    | models.Q(end_time__isnull=True)
                    | models.Q(end_time__gt=models.F("start_time"))
                ),
                name="chk_availability_override_time_order",
            ),
        ]

    def __str__(self):
        return f"{self.user_id} override {self.date} ({self.override_type})"


class EmployeePlannedLoad(models.Model):
    TARGET_MODE_CHOICES = [
        ("none", "Без плановой загрузки"),
        ("hours", "Часы в неделю"),
        ("days", "Дни в неделю"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="planned_loads",
    )
    week_start = models.DateField()
    target_mode = models.CharField(max_length=20, choices=TARGET_MODE_CHOICES, default="none")
    target_value = models.PositiveIntegerField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-week_start"]
        constraints = [
            models.UniqueConstraint(fields=["user", "week_start"], name="unique_employee_planned_load_week"),
        ]

    def __str__(self):
        return f"{self.user_id} load {self.week_start} ({self.target_mode})"


class EmployeeShiftRequest(models.Model):
    REQUEST_TYPES = [
        ("extra_hours", "Дополнительные часы"),
        ("replacement", "Замена слота"),
    ]
    STATUS_CHOICES = [
        ("pending", "На рассмотрении"),
        ("approved", "Одобрено"),
        ("rejected", "Отклонено"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="shift_requests",
    )
    date = models.DateField()
    request_type = models.CharField(max_length=20, choices=REQUEST_TYPES)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "date", "status"], name="idx_shift_user_date_status"),
        ]
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(start_time__isnull=True)
                    | models.Q(end_time__isnull=True)
                    | models.Q(end_time__gt=models.F("start_time"))
                ),
                name="chk_shift_request_time_order",
            ),
        ]

    def __str__(self):
        return f"{self.user_id} {self.request_type} {self.date}"


class DepartmentTask(models.Model):
    TASK_TYPES = [
        ("employee", "Сотруднику"),
        ("department", "Для отдела"),
    ]
    PRIORITY_CHOICES = [
        ("critical", "Критический"),
        ("high", "Высокий"),
        ("mid", "Средний"),
        ("low", "Низкий"),
    ]
    class TaskStatus(models.TextChoices):
        AWAITING_CONFIRMATION = "awaiting_confirmation", "Новая"
        CONFIRMED = "confirmed", "Взята"
        IN_PROGRESS = "in_progress", "В работе"
        ON_REVIEW = "on_review", "На проверке"
        COMPLETED = "completed", "Выполнена"
        RETURNED = "returned", "Возвращена на доработку"
        CONFLICT = "conflict", "Конфликт"

    STATUS_CHOICES = TaskStatus.choices

    department = models.ForeignKey(
        Department,
        on_delete=models.CASCADE,
        related_name="tasks",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_tasks",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_tasks",
    )
    taken_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="taken_tasks",
    )
    sprint = models.ForeignKey(
        "Sprint",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tasks",
    )
    date = models.DateField()
    due_time = models.TimeField(null=True, blank=True)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    task_type = models.CharField(max_length=20, choices=TASK_TYPES, default="employee")
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default="mid")
    status = models.CharField(
        max_length=30,
        choices=TaskStatus.choices,
        default=TaskStatus.AWAITING_CONFIRMATION,
    )
    refusal_reason = models.TextField(blank=True)
    manager_review_comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        indexes = [
            models.Index(fields=["department", "date", "status"], name="idx_task_dept_date_status"),
        ]
        constraints = []

    def __str__(self):
        return f"{self.title} ({self.date})"


class TaskSubmission(models.Model):
    task = models.ForeignKey(
        DepartmentTask,
        on_delete=models.CASCADE,
        related_name="submissions",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="task_submissions",
    )
    comment = models.TextField(blank=True)
    attachment = models.FileField(upload_to="task_submissions/%Y/%m/%d/", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Task {self.task_id} submission"


class Sprint(models.Model):
    STATUS_CHOICES = [
        ("planning", "Планирование"),
        ("active", "Активен"),
        ("completed", "Завершён"),
    ]

    department = models.ForeignKey(
        Department,
        on_delete=models.CASCADE,
        related_name="sprints",
    )
    title = models.CharField(max_length=200)
    goal = models.TextField(blank=True)
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="planning")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_sprints",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_date"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="chk_sprint_dates_order",
            ),
        ]

    def __str__(self):
        return f"{self.title} ({self.department})"


class LeaveRequest(models.Model):
    REQUEST_TYPES = [
        ("vacation", "Отпуск"),
        ("sick", "Больничный"),
    ]
    STATUS_CHOICES = [
        ("pending", "На рассмотрении"),
        ("approved", "Одобрено"),
        ("rejected", "Отклонено"),
        ("cancelled", "Отменено"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="leave_requests",
    )
    request_type = models.CharField(max_length=20, choices=REQUEST_TYPES)
    start_date = models.DateField()
    end_date = models.DateField()
    comment = models.TextField(blank=True)
    attachment = models.FileField(upload_to='leave_attachments/', blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_leave_requests",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_comment = models.TextField(blank=True)
    rejection_reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="chk_leave_request_dates_order",
            ),
        ]

    def __str__(self):
        return f"{self.user_id} {self.request_type} {self.start_date}..{self.end_date}"


class Substitution(models.Model):
    absent_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="substitutions_as_absent",
    )
    substitute_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="substitutions_as_substitute",
    )
    start_date = models.DateField()
    end_date = models.DateField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_substitutions",
    )
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-start_date"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="chk_substitution_dates_order",
            ),
        ]

    def __str__(self):
        return f"{self.absent_user_id} → {self.substitute_user_id} {self.start_date}..{self.end_date}"


class TaskReassignment(models.Model):
    REASON_CHOICES = [
        ("vacation", "Отпуск"),
        ("sick", "Больничный"),
        ("substitution", "Замещение"),
        ("manual", "Перераспределение"),
    ]

    task = models.ForeignKey(
        DepartmentTask,
        on_delete=models.CASCADE,
        related_name="reassignments",
    )
    previous_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reassignments_lost",
    )
    new_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reassignments_gained",
    )
    reason = models.CharField(max_length=20, choices=REASON_CHOICES, default="manual")
    note = models.CharField(max_length=255, blank=True)
    leave_request = models.ForeignKey(
        "LeaveRequest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="task_reassignments",
    )
    substitution = models.ForeignKey(
        "Substitution",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="task_reassignments",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_task_reassignments",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["task", "created_at"], name="idx_task_reassign_task"),
        ]

    def __str__(self):
        return f"Task {self.task_id}: {self.previous_user_id} → {self.new_user_id}"


class PasswordResetRequest(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Ожидает'),
        ('processed', 'Обработан'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='password_reset_requests',
    )
    email = models.EmailField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'Reset request for {self.email} ({self.status})'


class TaskMessage(models.Model):
    task = models.ForeignKey(
        DepartmentTask,
        on_delete=models.CASCADE,
        related_name='messages',
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='task_messages',
    )
    text = models.TextField(blank=True)
    attachment = models.FileField(upload_to='task_messages/%Y/%m/%d/', null=True, blank=True)
    reply_to = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='replies',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['task', 'created_at'], name='idx_msg_task_created'),
        ]

    def __str__(self):
        return f'Message on task {self.task_id} by {self.author_id}'


class TaskMessageReadState(models.Model):
    task = models.ForeignKey(
        DepartmentTask,
        on_delete=models.CASCADE,
        related_name='message_read_states',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='task_message_read_states',
    )
    last_read_message = models.ForeignKey(
        TaskMessage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['task', 'user'], name='uniq_task_message_read_state'),
        ]
        indexes = [
            models.Index(fields=['user', 'task'], name='idx_msg_read_user_task'),
        ]

    def __str__(self):
        return f'Read state for task {self.task_id} by {self.user_id}'


class GlobalSettings(models.Model):
    WORK_DAYS_CHOICES = [
        ("daily", "Ежедневно"),
        ("weekdays", "Пн–Пт"),
        ("week6", "Пн–Сб"),
    ]

    work_start = models.TimeField(default=time(7, 0))
    work_end = models.TimeField(default=time(23, 0))
    work_days = models.CharField(max_length=20, choices=WORK_DAYS_CHOICES, default="daily")
    weekly_hours_norm = models.PositiveIntegerField(default=40)
    platform_name = models.CharField(max_length=100, default="Conector Shift")
    support_email = models.EmailField(blank=True, default="")
    support_phone = models.CharField(max_length=30, blank=True, default="")
    global_announcement = models.CharField(max_length=255, blank=True, default="")
    allow_password_reset_requests = models.BooleanField(default=True)
    backup_retention_days = models.PositiveIntegerField(default=30)
    task_submission_max_files = models.PositiveIntegerField(default=5)
    task_submission_max_file_size_mb = models.PositiveIntegerField(default=10)
    ot_threshold = models.PositiveIntegerField(default=12)
    ot_coeff = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal("1.5"))
    shift_templates = models.JSONField(default=_default_shift_templates, blank=True)
    allow_custom_shifts = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_global_settings",
    )

    def __str__(self):
        return "Global settings"


class GlobalSettingsChange(models.Model):
    admin = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="settings_changes",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    summary = models.CharField(max_length=255)
    details = models.TextField(blank=True)
    changes = models.JSONField(default=list, blank=True)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.admin} settings change"


class SystemLogEntry(models.Model):
    LEVEL_CHOICES = [
        ("info", "Инфо"),
        ("warning", "Предупреждение"),
        ("error", "Ошибка"),
    ]

    created_at = models.DateTimeField(auto_now_add=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="system_log_entries",
    )
    action = models.CharField(max_length=255)
    path = models.CharField(max_length=255, blank=True)
    method = models.CharField(max_length=10, blank=True)
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES, default="info")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action}"


class SystemBackup(models.Model):
    STATUS_CHOICES = [
        ("ready", "Готов"),
        ("failed", "Ошибка"),
        ("restored", "Восстановлен"),
    ]
    SOURCE_CHOICES = [
        ("manual", "Вручную"),
        ("auto", "Авто"),
    ]

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_backups",
    )
    file_name = models.CharField(max_length=255)
    file_path = models.CharField(max_length=512)
    file_size = models.PositiveBigIntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="ready")
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default="manual")
    restored_at = models.DateTimeField(null=True, blank=True)
    restored_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="restored_backups",
    )
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.file_name


class Notification(models.Model):
    """Адресные уведомления внутри системы."""

    class Kind(models.TextChoices):
        TASK_TAKEN = "task_taken", "Сотрудник взял задачу"
        TASK_DROPPED = "task_dropped", "Сотрудник отказался от задачи"
        TASK_ON_REVIEW = "task_on_review", "Задача отправлена на проверку"
        TASK_RETURNED = "task_returned", "Задача возвращена на доработку"
        TASK_COMPLETED = "task_completed", "Задача принята"
        TASK_ASSIGNED = "task_assigned", "Назначена новая задача"
        TASK_REASSIGNED = "task_reassigned", "Задача переназначена"
        LEAVE_REQUEST_NEW = "leave_request_new", "Новая заявка на отпуск/больничный"
        LEAVE_REQUEST_DECISION = "leave_request_decision", "Решение по заявке"
        SUBSTITUTION_ASSIGNED = "substitution_assigned", "Назначена замена"

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="emitted_notifications",
    )
    kind = models.CharField(max_length=40, choices=Kind.choices)
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    url = models.CharField(max_length=512, blank=True)
    task = models.ForeignKey(
        DepartmentTask,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="notifications",
    )
    leave_request = models.ForeignKey(
        LeaveRequest,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="notifications",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient", "read_at"], name="idx_notif_recipient_read"),
            models.Index(fields=["recipient", "-created_at"], name="idx_notif_recipient_date"),
        ]

    def __str__(self):
        return f"{self.kind} → {self.recipient_id}"

    @property
    def is_unread(self) -> bool:
        return self.read_at is None


class TaskStatusHistory(models.Model):
    """Лог переходов статуса задачи. Заполняется бизнес-логикой."""

    task = models.ForeignKey(
        DepartmentTask,
        on_delete=models.CASCADE,
        related_name="status_history",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="task_status_changes",
    )
    from_status = models.CharField(max_length=30, blank=True)
    to_status = models.CharField(max_length=30)
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["task", "-created_at"], name="idx_task_history_task"),
        ]

    def __str__(self):
        return f"Task {self.task_id}: {self.from_status} → {self.to_status}"
