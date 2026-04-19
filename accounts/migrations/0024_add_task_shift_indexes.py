from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0023_alter_departmenttask_task_type"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="departmenttask",
            index=models.Index(fields=["department", "date", "status"], name="idx_task_dept_date_status"),
        ),
        migrations.AddIndex(
            model_name="employeeshiftrequest",
            index=models.Index(fields=["user", "date", "status"], name="idx_shift_user_date_status"),
        ),
    ]
