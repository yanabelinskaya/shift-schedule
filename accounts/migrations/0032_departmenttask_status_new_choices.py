from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0031_sprint_goal'),
    ]

    operations = [
        migrations.AlterField(
            model_name='departmenttask',
            name='status',
            field=models.CharField(
                choices=[
                    ('awaiting_confirmation', 'Новая'),
                    ('confirmed', 'Взята'),
                    ('in_progress', 'В работе'),
                    ('on_review', 'На проверке'),
                    ('completed', 'Выполнена'),
                    ('returned', 'Возвращена на доработку'),
                    ('conflict', 'Конфликт'),
                ],
                default='awaiting_confirmation',
                max_length=30,
            ),
        ),
    ]
