from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models


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
    hourly_rate = models.DecimalField(
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
    hourly_rate_reason = models.CharField(max_length=255, blank=True)
    department_change_reason = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f'{self.user.username} profile'


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
