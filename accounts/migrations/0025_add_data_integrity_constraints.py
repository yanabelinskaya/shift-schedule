from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0024_add_task_shift_indexes"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="employeeabsence",
            constraint=models.CheckConstraint(
                check=models.Q(end_date__gte=models.F("start_date")),
                name="chk_absence_dates_order",
            ),
        ),
        migrations.AddConstraint(
            model_name="employeeavailability",
            constraint=models.CheckConstraint(
                check=models.Q(end_time__gt=models.F("start_time")),
                name="chk_availability_time_order",
            ),
        ),
        migrations.AddConstraint(
            model_name="employeeshiftrequest",
            constraint=models.CheckConstraint(
                check=(
                    models.Q(start_time__isnull=True)
                    | models.Q(end_time__isnull=True)
                    | models.Q(end_time__gt=models.F("start_time"))
                ),
                name="chk_shift_request_time_order",
            ),
        ),
        migrations.AddConstraint(
            model_name="departmenttask",
            constraint=models.CheckConstraint(
                check=(
                    models.Q(start_time__isnull=True)
                    | models.Q(end_time__isnull=True)
                    | models.Q(end_time__gt=models.F("start_time"))
                ),
                name="chk_task_time_order",
            ),
        ),
    ]
