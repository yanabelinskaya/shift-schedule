import threading
from datetime import timedelta
import os
import platform
from pathlib import Path
import shutil

from django.conf import settings
from django.core.management import call_command
from django.utils import timezone

from .models import GlobalSettings, SystemBackup, SystemLogEntry


_auto_backup_lock = threading.Lock()
_last_auto_backup_date = None


def _backup_dir():
    backup_dir = Path(getattr(settings, "BACKUP_STORAGE_DIR", Path(settings.BASE_DIR) / "backups"))
    if not backup_dir.is_absolute():
        backup_dir = Path(settings.BASE_DIR) / backup_dir
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


def format_bytes(value):
    if value is None:
        return "0 B"
    size = float(value)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _read_linux_meminfo():
    meminfo_path = Path("/proc/meminfo")
    if not meminfo_path.exists():
        return {}
    result = {}
    try:
        with meminfo_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if ":" not in line:
                    continue
                key, raw_value = line.split(":", 1)
                parts = raw_value.strip().split()
                if not parts:
                    continue
                try:
                    kib_value = int(parts[0])
                except (TypeError, ValueError):
                    continue
                result[key.strip()] = kib_value * 1024
    except Exception:
        return {}
    return result


def collect_runtime_metrics():
    cpu_count = os.cpu_count() or 1
    load_1m = load_5m = load_15m = None
    cpu_percent_1m = cpu_percent_5m = cpu_percent_15m = None

    if hasattr(os, "getloadavg"):
        try:
            load_1m, load_5m, load_15m = os.getloadavg()
            cpu_percent_1m = round((load_1m / cpu_count) * 100, 1)
            cpu_percent_5m = round((load_5m / cpu_count) * 100, 1)
            cpu_percent_15m = round((load_15m / cpu_count) * 100, 1)
        except (OSError, ValueError):
            load_1m = load_5m = load_15m = None

    memory_total = None
    memory_available = None
    memory_used = None
    memory_used_percent = None
    memory_source = "unknown"

    meminfo = _read_linux_meminfo()
    if meminfo:
        memory_total = meminfo.get("MemTotal")
        memory_available = meminfo.get("MemAvailable")
        if memory_total is not None and memory_available is not None:
            memory_used = max(memory_total - memory_available, 0)
            if memory_total > 0:
                memory_used_percent = round((memory_used / memory_total) * 100, 1)
            memory_source = "system"

    if memory_total is None:
        try:
            page_size = int(os.sysconf("SC_PAGE_SIZE"))
            phys_pages = int(os.sysconf("SC_PHYS_PAGES"))
            if page_size > 0 and phys_pages > 0:
                memory_total = page_size * phys_pages
        except (AttributeError, OSError, ValueError):
            memory_total = None

    process_rss = None
    try:
        import resource

        usage = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        if usage > 0:
            if platform.system() == "Darwin":
                process_rss = usage
            else:
                process_rss = usage * 1024
    except Exception:
        process_rss = None

    if memory_used is None and process_rss is not None:
        memory_used = process_rss
        memory_source = "process"
        if memory_total:
            memory_used_percent = round((memory_used / memory_total) * 100, 1)

    disk_total = None
    disk_used = None
    disk_free = None
    disk_used_percent = None
    try:
        usage = shutil.disk_usage(settings.BASE_DIR)
        disk_total = int(usage.total)
        disk_used = int(usage.used)
        disk_free = int(usage.free)
        if disk_total > 0:
            disk_used_percent = round((disk_used / disk_total) * 100, 1)
    except Exception:
        pass

    return {
        "cpu_count": cpu_count,
        "load_avg_1m": load_1m,
        "load_avg_5m": load_5m,
        "load_avg_15m": load_15m,
        "cpu_load_percent_1m": cpu_percent_1m,
        "cpu_load_percent_5m": cpu_percent_5m,
        "cpu_load_percent_15m": cpu_percent_15m,
        "memory_total_bytes": memory_total,
        "memory_available_bytes": memory_available,
        "memory_used_bytes": memory_used,
        "memory_used_percent": memory_used_percent,
        "memory_source": memory_source,
        "process_rss_bytes": process_rss,
        "disk_total_bytes": disk_total,
        "disk_used_bytes": disk_used,
        "disk_free_bytes": disk_free,
        "disk_used_percent": disk_used_percent,
    }


def log_system_event(action, user=None, level="info", **meta):
    try:
        SystemLogEntry.objects.create(
            action=action,
            user=user,
            level=level,
            meta=meta or {},
        )
    except Exception:
        # Do not block user flows if logging fails.
        return None
    return True


def _cleanup_old_backups():
    settings_obj = GlobalSettings.objects.only("backup_retention_days").first()
    if settings_obj is not None:
        retention_days = int(settings_obj.backup_retention_days or 0)
    else:
        try:
            retention_days = int(getattr(settings, "BACKUP_RETENTION_DAYS", 30) or 0)
        except (TypeError, ValueError):
            retention_days = 30
    if retention_days <= 0:
        return 0

    cutoff = timezone.now() - timedelta(days=retention_days)
    stale_backups = list(
        SystemBackup.objects.filter(created_at__lt=cutoff).only("id", "file_name", "file_path")
    )
    if not stale_backups:
        return 0

    deleted_ids = []
    for backup in stale_backups:
        file_path = Path(backup.file_path)
        if file_path.exists():
            try:
                file_path.unlink()
            except Exception:
                # Leave DB row so cleanup can be retried later.
                continue
        deleted_ids.append(backup.id)

    if not deleted_ids:
        return 0

    SystemBackup.objects.filter(id__in=deleted_ids).delete()
    log_system_event(
        "Автоочистка старых бэкапов",
        level="info",
        retention_days=retention_days,
        deleted_count=len(deleted_ids),
    )
    return len(deleted_ids)


def create_backup(created_by=None, source="manual"):
    backup_dir = _backup_dir()
    timestamp = timezone.localtime().strftime("%Y%m%d_%H%M%S")
    file_name = f"backup_{timestamp}.json"
    file_path = backup_dir / file_name
    try:
        with open(file_path, "w", encoding="utf-8") as output:
            call_command(
                "dumpdata",
                "--natural-foreign",
                "--natural-primary",
                "--indent",
                "2",
                stdout=output,
            )
        file_size = file_path.stat().st_size if file_path.exists() else 0
        backup = SystemBackup.objects.create(
            created_by=created_by,
            file_name=file_name,
            file_path=str(file_path),
            file_size=file_size,
            status="ready",
            source=source,
        )
        log_system_event(
            f"Создан бэкап {file_name}",
            user=created_by,
            level="info",
            size=file_size,
            source=source,
        )
        _cleanup_old_backups()
        return backup
    except Exception as exc:
        if file_path.exists():
            try:
                file_path.unlink()
            except Exception:
                pass
        backup = SystemBackup.objects.create(
            created_by=created_by,
            file_name=file_name,
            file_path=str(file_path),
            file_size=0,
            status="failed",
            source=source,
            notes=str(exc)[:250],
        )
        log_system_event(
            f"Ошибка создания бэкапа {file_name}",
            user=created_by,
            level="error",
            error=str(exc),
            source=source,
        )
        raise


def maybe_create_daily_backup():
    global _last_auto_backup_date
    today = timezone.localdate()
    if _last_auto_backup_date == today:
        return None
    with _auto_backup_lock:
        if _last_auto_backup_date == today:
            return None
        already_done = SystemBackup.objects.filter(
            source="auto",
            created_at__date=today,
            status="ready",
        ).exists()
        if already_done:
            _last_auto_backup_date = today
            return None
        try:
            backup = create_backup(created_by=None, source="auto")
            _last_auto_backup_date = today
            return backup
        except Exception:
            _last_auto_backup_date = today
            return None
