from .models import GlobalSettings


def site_config(request):
    settings_obj = GlobalSettings.objects.only(
        "platform_name",
        "support_email",
        "support_phone",
        "global_announcement",
        "allow_password_reset_requests",
    ).first()

    if settings_obj is None:
        return {
            "site_config": {
                "platform_name": "Conector Shift",
                "support_email": "",
                "support_phone": "",
                "global_announcement": "",
                "allow_password_reset_requests": True,
            }
        }

    return {
        "site_config": {
            "platform_name": settings_obj.platform_name or "Conector Shift",
            "support_email": settings_obj.support_email or "",
            "support_phone": settings_obj.support_phone or "",
            "global_announcement": settings_obj.global_announcement or "",
            "allow_password_reset_requests": bool(settings_obj.allow_password_reset_requests),
        }
    }
