import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Reset a user's password from deploy environment variables."

    def handle(self, *args, **options):
        username = os.environ.get("DJANGO_RESET_USER_PASSWORD_USERNAME", "").strip()
        password = os.environ.get("DJANGO_RESET_USER_PASSWORD", "")

        if not username or not password:
            self.stdout.write(
                "DJANGO_RESET_USER_PASSWORD_USERNAME or DJANGO_RESET_USER_PASSWORD is not set; skipping."
            )
            return

        User = get_user_model()
        user = User.objects.filter(username=username).first()
        if not user:
            self.stdout.write(self.style.WARNING(f"User '{username}' not found; password not changed."))
            return

        user.set_password(password)
        user.is_active = True
        user.save(update_fields=["password", "is_active"])
        self.stdout.write(self.style.SUCCESS(f"Password for '{username}' updated."))
