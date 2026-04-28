from datetime import date, timedelta

from .models import (
    EmployeeAbsence,
    EmployeeAvailabilityOverride,
    EmployeeBaseAvailability,
    GlobalSettings,
)


def get_week_start(day_value):
    return day_value - timedelta(days=day_value.weekday())


def ensure_employee_base_availability(user, settings_obj=None):
    settings_obj = settings_obj or GlobalSettings.objects.first() or GlobalSettings.objects.create()
    existing = set(
        EmployeeBaseAvailability.objects.filter(user=user).values_list("weekday", flat=True)
    )
    to_create = []
    for weekday in range(7):
        if weekday in existing:
            continue
        if weekday <= 4:
            mode = "fixed"
            start_time = settings_obj.work_start
            end_time = settings_obj.work_end
        else:
            mode = "off"
            start_time = None
            end_time = None
        to_create.append(
            EmployeeBaseAvailability(
                user=user,
                weekday=weekday,
                mode=mode,
                start_time=start_time,
                end_time=end_time,
            )
        )
    if to_create:
        EmployeeBaseAvailability.objects.bulk_create(to_create)


def build_base_map(user):
    return {
        rule.weekday: rule
        for rule in EmployeeBaseAvailability.objects.filter(user=user).order_by("weekday")
    }


def build_override_map(user, start_date, end_date):
    return {
        override.date: override
        for override in EmployeeAvailabilityOverride.objects.filter(
            user=user,
            date__range=(start_date, end_date),
        )
    }


def build_absence_map(user, start_date, end_date):
    result = {}
    absences = EmployeeAbsence.objects.filter(
        user=user,
        start_date__lte=end_date,
        end_date__gte=start_date,
    ).order_by("start_date")
    for absence in absences:
        current = max(absence.start_date, start_date)
        boundary = min(absence.end_date, end_date)
        while current <= boundary:
            key = current
            if absence.absence_type == "sick" or key not in result:
                result[key] = absence.absence_type
            current += timedelta(days=1)
    return result


def resolve_day_availability(
    user,
    day_date,
    *,
    settings_obj=None,
    base_map=None,
    override_map=None,
    absence_map=None,
):
    settings_obj = settings_obj or GlobalSettings.objects.first() or GlobalSettings.objects.create()
    if base_map is None:
        ensure_employee_base_availability(user, settings_obj=settings_obj)
        base_map = build_base_map(user)
    if override_map is None:
        override_map = build_override_map(user, day_date, day_date)
    if absence_map is None:
        absence_map = build_absence_map(user, day_date, day_date)

    absence_type = absence_map.get(day_date)
    if absence_type:
        return {
            "date": day_date,
            "is_available": False,
            "start_time": None,
            "end_time": None,
            "mode": "off",
            "source": "absence",
            "absence_type": absence_type,
            "override_type": absence_type,
        }

    override = override_map.get(day_date)
    if override:
        if override.override_type in {"unavailable", "vacation", "sick"}:
            return {
                "date": day_date,
                "is_available": False,
                "start_time": None,
                "end_time": None,
                "mode": "off",
                "source": "override",
                "absence_type": override.override_type if override.override_type in {"vacation", "sick"} else "",
                "override_type": override.override_type,
            }
        if override.override_type == "available":
            return {
                "date": day_date,
                "is_available": True,
                "start_time": settings_obj.work_start,
                "end_time": settings_obj.work_end,
                "mode": "fixed",
                "source": "override",
                "absence_type": "",
                "override_type": override.override_type,
            }
        return {
            "date": day_date,
            "is_available": True,
            "start_time": override.start_time or settings_obj.work_start,
            "end_time": override.end_time or settings_obj.work_end,
            "mode": "fixed",
            "source": "override",
            "absence_type": "",
            "override_type": override.override_type,
        }

    rule = base_map.get(day_date.weekday())
    if not rule or rule.mode == "off":
        return {
            "date": day_date,
            "is_available": False,
            "start_time": None,
            "end_time": None,
            "mode": "off",
            "source": "base",
            "absence_type": "",
            "override_type": "",
        }
    if rule.mode == "flex":
        return {
            "date": day_date,
            "is_available": True,
            "start_time": None,
            "end_time": None,
            "mode": "flex",
            "source": "base",
            "absence_type": "",
            "override_type": "",
        }

    return {
        "date": day_date,
        "is_available": True,
        "start_time": rule.start_time or settings_obj.work_start,
        "end_time": rule.end_time or settings_obj.work_end,
        "mode": "fixed",
        "source": "base",
        "absence_type": "",
        "override_type": "",
    }


def resolve_range_availability(user, start_date, end_date, *, settings_obj=None):
    settings_obj = settings_obj or GlobalSettings.objects.first() or GlobalSettings.objects.create()
    ensure_employee_base_availability(user, settings_obj=settings_obj)
    base_map = build_base_map(user)
    override_map = build_override_map(user, start_date, end_date)
    absence_map = build_absence_map(user, start_date, end_date)

    result = {}
    current = start_date
    while current <= end_date:
        result[current] = resolve_day_availability(
            user,
            current,
            settings_obj=settings_obj,
            base_map=base_map,
            override_map=override_map,
            absence_map=absence_map,
        )
        current += timedelta(days=1)
    return result
