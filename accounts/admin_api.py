from datetime import timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import generics, serializers, status
from rest_framework.response import Response
from django.db import transaction

from .concurrency import assert_optimistic_lock, set_resource_version_header
from .models import GlobalSettings, GlobalSettingsChange, SystemBackup, SystemLogEntry
from .pagination import apply_non_paginated_limit
from .permissions import IsAdminRole
from .serializers import DetailMessageSerializer, EmptySerializer
from .system_utils import (
    collect_runtime_metrics,
    create_backup,
    format_bytes,
    log_system_event,
    maybe_create_daily_backup,
)


class AdminSettingsSerializer(serializers.Serializer):
    if_match = serializers.DateTimeField(required=False)
    work_start = serializers.TimeField(format='%H:%M', input_formats=['%H:%M'], required=False)
    work_end = serializers.TimeField(format='%H:%M', input_formats=['%H:%M'], required=False)
    weekly_hours_norm = serializers.IntegerField(min_value=1, required=False)
    platform_name = serializers.CharField(max_length=100, required=False)
    support_email = serializers.EmailField(allow_blank=True, required=False)
    support_phone = serializers.CharField(max_length=30, allow_blank=True, required=False)
    global_announcement = serializers.CharField(max_length=255, allow_blank=True, required=False)
    allow_password_reset_requests = serializers.BooleanField(required=False)
    backup_retention_days = serializers.IntegerField(min_value=0, required=False)


class AdminSettingsPayloadSerializer(serializers.Serializer):
    work_start = serializers.TimeField(format='%H:%M', input_formats=['%H:%M'])
    work_end = serializers.TimeField(format='%H:%M', input_formats=['%H:%M'])
    weekly_hours_norm = serializers.IntegerField(min_value=1)
    platform_name = serializers.CharField(max_length=100)
    support_email = serializers.EmailField(allow_blank=True)
    support_phone = serializers.CharField(max_length=30, allow_blank=True)
    global_announcement = serializers.CharField(max_length=255, allow_blank=True)
    allow_password_reset_requests = serializers.BooleanField()
    backup_retention_days = serializers.IntegerField(min_value=0)
    updated_at = serializers.DateTimeField()


class AdminSettingsResponseSerializer(serializers.Serializer):
    ok = serializers.BooleanField()
    updated_at = serializers.DateTimeField()
    settings = AdminSettingsPayloadSerializer()


class AdminSettingsHistoryItemSerializer(serializers.Serializer):
    date = serializers.DateTimeField()
    author = serializers.CharField()
    title = serializers.CharField()
    details = serializers.CharField()
    changes = serializers.ListField(child=serializers.CharField())


class AdminSettingsHistoryResponseSerializer(serializers.Serializer):
    history = AdminSettingsHistoryItemSerializer(many=True)


class AdminBackupSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    file_name = serializers.CharField()
    created_at = serializers.DateTimeField()
    file_size = serializers.IntegerField()
    size_display = serializers.CharField()
    status = serializers.CharField()
    status_label = serializers.CharField()
    source = serializers.CharField()
    source_label = serializers.CharField()
    download_url = serializers.CharField()
    restore_url = serializers.CharField()


class AdminBackupResponseSerializer(serializers.Serializer):
    backup = AdminBackupSerializer()


class AdminMonitoringResponseSerializer(serializers.Serializer):
    online_users = serializers.IntegerField()
    requests_per_minute = serializers.IntegerField()
    errors_24h = serializers.IntegerField()
    last_login = serializers.DateTimeField(allow_null=True)
    last_login_user = serializers.CharField()
    cpu_load_percent_1m = serializers.FloatField(allow_null=True)
    memory_used_percent = serializers.FloatField(allow_null=True)
    memory_used_bytes = serializers.IntegerField(allow_null=True)
    memory_total_bytes = serializers.IntegerField(allow_null=True)
    disk_used_percent = serializers.FloatField(allow_null=True)
    disk_used_bytes = serializers.IntegerField(allow_null=True)
    disk_total_bytes = serializers.IntegerField(allow_null=True)
    last_backup = AdminBackupSerializer(allow_null=True)
    updated_at = serializers.DateTimeField()


def _serialize_backup(request, backup):
    if not backup:
        return None
    return {
        'id': backup.id,
        'file_name': backup.file_name,
        'created_at': timezone.localtime(backup.created_at),
        'file_size': backup.file_size,
        'size_display': format_bytes(backup.file_size),
        'status': backup.status,
        'status_label': backup.get_status_display(),
        'source': backup.source,
        'source_label': backup.get_source_display(),
        'download_url': request.build_absolute_uri(reverse('admin-system-backup-download', args=[backup.id])),
        'restore_url': request.build_absolute_uri(
            reverse('api-admin-system-backups-restore', args=[backup.id])
        ),
    }


def _system_monitoring_snapshot():
    now = timezone.now()
    online_window = now - timedelta(minutes=5)
    rpm_window = now - timedelta(minutes=1)
    errors_window = now - timedelta(hours=24)
    User = get_user_model()

    online_users = (
        SystemLogEntry.objects.filter(created_at__gte=online_window, user__isnull=False)
        .values('user_id')
        .distinct()
        .count()
    )
    requests_per_minute = SystemLogEntry.objects.filter(created_at__gte=rpm_window).count()
    errors_24h = SystemLogEntry.objects.filter(created_at__gte=errors_window, level='error').count()

    last_login_user = User.objects.exclude(last_login__isnull=True).order_by('-last_login').first()
    last_login = last_login_user.last_login if last_login_user else None
    last_login_name = ''
    if last_login_user:
        last_login_name = last_login_user.get_full_name() or last_login_user.username

    runtime_metrics = collect_runtime_metrics()
    last_backup = SystemBackup.objects.filter(status='ready').order_by('-created_at').first()
    return {
        'online_users': online_users,
        'requests_per_minute': requests_per_minute,
        'errors_24h': errors_24h,
        'last_login': last_login,
        'last_login_user': last_login_name,
        'cpu_load_percent_1m': runtime_metrics.get('cpu_load_percent_1m'),
        'memory_used_percent': runtime_metrics.get('memory_used_percent'),
        'memory_used_bytes': runtime_metrics.get('memory_used_bytes'),
        'memory_total_bytes': runtime_metrics.get('memory_total_bytes'),
        'disk_used_percent': runtime_metrics.get('disk_used_percent'),
        'disk_used_bytes': runtime_metrics.get('disk_used_bytes'),
        'disk_total_bytes': runtime_metrics.get('disk_total_bytes'),
        'last_backup': last_backup,
        'updated_at': now,
    }


class AdminSettingsView(generics.GenericAPIView):
    permission_classes = [IsAdminRole]
    serializer_class = AdminSettingsSerializer

    @extend_schema(
        tags=['Admin'],
        summary='Текущие глобальные настройки',
        responses={200: AdminSettingsPayloadSerializer},
    )
    def get(self, request):
        settings_obj = GlobalSettings.objects.first() or GlobalSettings.objects.create()
        response = Response(
            {
                'work_start': settings_obj.work_start.strftime('%H:%M'),
                'work_end': settings_obj.work_end.strftime('%H:%M'),
                'weekly_hours_norm': settings_obj.weekly_hours_norm,
                'platform_name': settings_obj.platform_name,
                'support_email': settings_obj.support_email,
                'support_phone': settings_obj.support_phone,
                'global_announcement': settings_obj.global_announcement,
                'allow_password_reset_requests': settings_obj.allow_password_reset_requests,
                'backup_retention_days': settings_obj.backup_retention_days,
                'updated_at': timezone.localtime(settings_obj.updated_at),
            }
        )
        return set_resource_version_header(response, settings_obj)

    @extend_schema(
        tags=['Admin'],
        summary='Обновить глобальные настройки',
        request=AdminSettingsSerializer,
        responses={200: AdminSettingsResponseSerializer, 400: DetailMessageSerializer},
    )
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        settings_obj = GlobalSettings.objects.first() or GlobalSettings.objects.create()
        assert_optimistic_lock(request, settings_obj, client_version=serializer.validated_data.get('if_match'))
        before = {
            'work_start': settings_obj.work_start.strftime('%H:%M'),
            'work_end': settings_obj.work_end.strftime('%H:%M'),
            'weekly_hours_norm': settings_obj.weekly_hours_norm,
            'platform_name': settings_obj.platform_name,
            'support_email': settings_obj.support_email,
            'support_phone': settings_obj.support_phone,
            'global_announcement': settings_obj.global_announcement,
            'allow_password_reset_requests': settings_obj.allow_password_reset_requests,
            'backup_retention_days': settings_obj.backup_retention_days,
        }

        data = serializer.validated_data
        if 'work_start' in data:
            settings_obj.work_start = data['work_start']
        if 'work_end' in data:
            settings_obj.work_end = data['work_end']
        if 'weekly_hours_norm' in data:
            settings_obj.weekly_hours_norm = data['weekly_hours_norm']
        if 'platform_name' in data:
            settings_obj.platform_name = data['platform_name'].strip() or "Conector Shift"
        if 'support_email' in data:
            settings_obj.support_email = data['support_email'].strip()
        if 'support_phone' in data:
            settings_obj.support_phone = data['support_phone'].strip()
        if 'global_announcement' in data:
            settings_obj.global_announcement = data['global_announcement'].strip()
        if 'allow_password_reset_requests' in data:
            settings_obj.allow_password_reset_requests = bool(data['allow_password_reset_requests'])
        if 'backup_retention_days' in data:
            settings_obj.backup_retention_days = data['backup_retention_days']

        settings_obj.updated_by = request.user
        settings_obj.save()

        after = {
            'work_start': settings_obj.work_start.strftime('%H:%M'),
            'work_end': settings_obj.work_end.strftime('%H:%M'),
            'weekly_hours_norm': settings_obj.weekly_hours_norm,
            'platform_name': settings_obj.platform_name,
            'support_email': settings_obj.support_email,
            'support_phone': settings_obj.support_phone,
            'global_announcement': settings_obj.global_announcement,
            'allow_password_reset_requests': settings_obj.allow_password_reset_requests,
            'backup_retention_days': settings_obj.backup_retention_days,
        }

        changes = []
        if before['work_start'] != after['work_start'] or before['work_end'] != after['work_end']:
            changes.append(f"Время по умолчанию: {after['work_start']}-{after['work_end']}")
        if before['weekly_hours_norm'] != after['weekly_hours_norm']:
            changes.append(f"Норма {after['weekly_hours_norm']} ч/нед")
        if before['platform_name'] != after['platform_name']:
            changes.append(f"Название платформы: {after['platform_name']}")
        if before['support_email'] != after['support_email']:
            changes.append(
                f"Email поддержки: {after['support_email'] or 'не указан'}"
            )
        if before['support_phone'] != after['support_phone']:
            changes.append(
                f"Телефон поддержки: {after['support_phone'] or 'не указан'}"
            )
        if before['global_announcement'] != after['global_announcement']:
            changes.append(
                "Глобальное объявление: обновлено" if after['global_announcement'] else "Глобальное объявление: отключено"
            )
        if before['allow_password_reset_requests'] != after['allow_password_reset_requests']:
            changes.append(
                "Восстановление пароля: включено"
                if after['allow_password_reset_requests']
                else "Восстановление пароля: отключено"
            )
        if before['backup_retention_days'] != after['backup_retention_days']:
            changes.append(f"Хранение бэкапов: {after['backup_retention_days']} дн")

        if changes:
            GlobalSettingsChange.objects.create(
                admin=request.user,
                summary='Обновлены глобальные настройки',
                details='Изменения сохранены через API администратора.',
                changes=changes,
                payload=after,
            )

        response = Response(
            {
                'ok': True,
                'updated_at': timezone.localtime(settings_obj.updated_at),
                'settings': after,
            }
        )
        return set_resource_version_header(response, settings_obj)


class AdminSettingsHistoryView(generics.GenericAPIView):
    permission_classes = [IsAdminRole]

    @extend_schema(
        tags=['Admin'],
        summary='История изменений глобальных настроек',
        responses={200: AdminSettingsHistoryResponseSerializer},
    )
    def get(self, request):
        changes = GlobalSettingsChange.objects.filter(admin=request.user).order_by('-created_at')
        changes = apply_non_paginated_limit(request, changes)
        history = [
            {
                'date': timezone.localtime(change.created_at),
                'author': change.admin.get_full_name() or change.admin.username,
                'title': change.summary,
                'details': change.details,
                'changes': change.changes,
            }
            for change in changes
        ]
        return Response({'history': history})


class AdminSystemBackupCreateView(generics.GenericAPIView):
    permission_classes = [IsAdminRole]
    serializer_class = EmptySerializer

    @extend_schema(
        tags=['Admin'],
        summary='Создать системный бэкап',
        responses={200: AdminBackupResponseSerializer, 500: DetailMessageSerializer},
    )
    def post(self, request):
        try:
            backup = create_backup(created_by=request.user, source='manual')
        except Exception:
            return Response(
                {'detail': 'Не удалось создать бэкап.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response({'backup': _serialize_backup(request, backup)})


class AdminSystemBackupRestoreView(generics.GenericAPIView):
    permission_classes = [IsAdminRole]
    serializer_class = EmptySerializer

    @extend_schema(
        tags=['Admin'],
        summary='Восстановить систему из бэкапа',
        responses={200: AdminBackupResponseSerializer, 404: DetailMessageSerializer, 500: DetailMessageSerializer},
    )
    def post(self, request, backup_id):
        backup = get_object_or_404(SystemBackup, id=backup_id)
        file_path = Path(backup.file_path)
        if not file_path.exists():
            return Response({'detail': 'Файл бэкапа не найден.'}, status=status.HTTP_404_NOT_FOUND)

        try:
            with transaction.atomic():
                call_command('loaddata', str(file_path), verbosity=0)
            backup.status = 'restored'
            backup.restored_at = timezone.now()
            backup.restored_by = request.user
            backup.save(update_fields=['status', 'restored_at', 'restored_by'])
            log_system_event(
                f'Восстановлен бэкап {backup.file_name}',
                user=request.user,
                level='warning',
            )
            return Response({'detail': 'Бэкап восстановлен.', 'backup': _serialize_backup(request, backup)})
        except Exception as exc:
            log_system_event(
                f'Ошибка восстановления бэкапа {backup.file_name}',
                user=request.user,
                level='error',
                error=str(exc),
            )
            return Response(
                {'detail': 'Не удалось восстановить бэкап.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class AdminSystemMonitoringView(generics.GenericAPIView):
    permission_classes = [IsAdminRole]

    @extend_schema(
        tags=['Admin'],
        summary='Снимок мониторинга системы',
        responses={200: AdminMonitoringResponseSerializer},
    )
    def get(self, request):
        maybe_create_daily_backup()
        snapshot = _system_monitoring_snapshot()
        last_login = snapshot.get('last_login')
        last_backup = snapshot.get('last_backup')

        return Response(
            {
                'online_users': snapshot.get('online_users', 0),
                'requests_per_minute': snapshot.get('requests_per_minute', 0),
                'errors_24h': snapshot.get('errors_24h', 0),
                'last_login': timezone.localtime(last_login) if last_login else None,
                'last_login_user': snapshot.get('last_login_user', ''),
                'cpu_load_percent_1m': snapshot.get('cpu_load_percent_1m'),
                'memory_used_percent': snapshot.get('memory_used_percent'),
                'memory_used_bytes': snapshot.get('memory_used_bytes'),
                'memory_total_bytes': snapshot.get('memory_total_bytes'),
                'disk_used_percent': snapshot.get('disk_used_percent'),
                'disk_used_bytes': snapshot.get('disk_used_bytes'),
                'disk_total_bytes': snapshot.get('disk_total_bytes'),
                'last_backup': _serialize_backup(request, last_backup),
                'updated_at': timezone.localtime(snapshot.get('updated_at')),
            }
        )
