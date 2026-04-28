from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0030_add_leave_request_attachment'),
    ]

    operations = [
        migrations.AddField(
            model_name='sprint',
            name='goal',
            field=models.TextField(blank=True, default=''),
            preserve_default=False,
        ),
    ]
