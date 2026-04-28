from django.shortcuts import get_object_or_404
from rest_framework.exceptions import PermissionDenied, ValidationError

from .models import Department, DepartmentTask, EmployeeProfile


def get_manager_department(manager):
    department = Department.objects.filter(manager=manager, is_archived=False).first()
    if department:
        return department

    profile = getattr(manager, "profile", None)
    if profile and profile.department and not profile.department.is_archived:
        return profile.department
    return None


def require_manager_department(manager):
    department = get_manager_department(manager)
    if not department:
        raise ValidationError({"detail": "Менеджер не привязан к отделу."})
    return department


def can_manage_employee(actor, employee):
    role = getattr(actor, "role", None)
    if role == "admin":
        return True
    if role != "manager":
        return False

    department = get_manager_department(actor)
    if not department:
        return False
    return EmployeeProfile.objects.filter(user=employee, department=department).exists()


def require_manage_employee_access(actor, employee):
    if not can_manage_employee(actor, employee):
        raise PermissionDenied("Нет доступа к сотруднику.")


def employee_can_access_task(user, task):
    profile = EmployeeProfile.objects.filter(user=user).only("department_id").first()
    if not profile or profile.department_id != task.department_id:
        return False
    if task.task_type == "employee":
        return task.assigned_to_id == user.id or task.taken_by_id == user.id
    if task.task_type == "department":
        return True
    return False


def require_employee_task_access(user, task):
    if not employee_can_access_task(user, task):
        raise PermissionDenied("Нет доступа к задаче.")


def get_manager_task_or_404(manager, task_id):
    department = require_manager_department(manager)
    return get_object_or_404(
        DepartmentTask.objects.select_related("assigned_to", "created_by"),
        id=task_id,
        department=department,
    )
