from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0032_departmenttask_status_new_choices'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='TaskMessage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('text', models.TextField(blank=True)),
                ('attachment', models.FileField(blank=True, null=True, upload_to='task_messages/%Y/%m/%d/')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('author', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='task_messages',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('reply_to', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='replies',
                    to='accounts.taskmessage',
                )),
                ('task', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='messages',
                    to='accounts.departmenttask',
                )),
            ],
            options={
                'ordering': ['created_at'],
                'indexes': [
                    models.Index(fields=['task', 'created_at'], name='idx_msg_task_created'),
                ],
            },
        ),
    ]
