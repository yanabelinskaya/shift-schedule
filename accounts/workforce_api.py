from datetime import date, timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema, extend_schema_field
from rest_framework import generics, serializers, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from .concurrency import (
    assert_optimistic_lock,
    assert_queryset_optimistic_lock,
    set_resource_version_header,
)
from .object_access import can_manage_employee, require_employee_task_access, require_manager_department
from .pagination import BoundedListMixin
from .serializers import DetailMessageSerializer
from .models import (
    DepartmentTask,
    EmployeeAbsence,
    EmployeeAvailability,
    EmployeeProfile,
    EmployeeShiftRequest,
    GlobalSettings,
    TaskSubmission,
)
from .permissions import IsEmployeeRole, IsManagerRole


def _format_user_name(user):
    if not user:
        return ""
    return user.get_full_name().strip() or user.username


def _parse_week_offset(value, default=0):
    if value is None:
        return default
    raw = str(value).strip().lower()
    if raw == 'current':
        return 0
    if raw == 'next':
        return 1
    if raw == 'prev':
        return -1
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _validate_submission_attachments(attachments):
    settings_obj = GlobalSettings.objects.only(
        'task_submission_max_files',
        'task_submission_max_file_size_mb',
    ).first()

    if settings_obj is not None:
        max_files = int(settings_obj.task_submission_max_files or 5)
        max_file_size = int(settings_obj.task_submission_max_file_size_mb or 10) * 1024 * 1024
    else:
        max_files = int(getattr(settings, 'TASK_SUBMISSION_MAX_FILES', 5) or 5)
        max_file_size = int(getattr(settings, 'TASK_SUBMISSION_MAX_FILE_SIZE', 10 * 1024 * 1024) or 10 * 1024 * 1024)

    if len(attachments) > max_files:
        raise ValidationError({'detail': f'Можно прикрепить не более {max_files} файлов.'})

    allowed_extensions = {
        str(ext).strip().lower().lstrip('.')
        for ext in (getattr(settings, 'TASK_SUBMISSION_ALLOWED_EXTENSIONS', set()) or set())
        if str(ext).strip()
    }
    allowed_content_types = {
        str(mime).strip().lower()
        for mime in (getattr(settings, 'TASK_SUBMISSION_ALLOWED_CONTENT_TYPES', set()) or set())
        if str(mime).strip()
    }

    for attachment in attachments:
        if attachment.size and attachment.size > max_file_size:
            max_mb = max_file_size / (1024 * 1024)
            raise ValidationError(
                {'detail': f'Файл "{attachment.name}" превышает лимит {max_mb:.1f} MB.'}
            )

        extension = Path(attachment.name or '').suffix.lower().lstrip('.')
        if allowed_extensions and extension not in allowed_extensions:
            raise ValidationError({'detail': f'Недопустимый тип файла: {attachment.name}.'})

        content_type = (getattr(attachment, 'content_type', '') or '').lower()
        if allowed_content_types and content_type and content_type not in allowed_content_types:
            raise ValidationError({'detail': f'Недопустимый MIME-тип файла: {attachment.name}.'})


class SimpleEmployeeSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class AvailabilityEntrySerializer(serializers.ModelSerializer):
    user_id = serializers.IntegerField(source='user.id', read_only=True)

    class Meta:
        model = EmployeeAvailability
        fields = (
            'id',
            'user_id',
            'date',
            'is_available',
            'start_time',
            'end_time',
            'priority',
            'is_approved',
            'approved_at',
            'updated_at',
        )


class AbsenceEntrySerializer(serializers.ModelSerializer):
    user_id = serializers.IntegerField(source='user.id', read_only=True)

    class Meta:
        model = EmployeeAbsence
        fields = ('id', 'user_id', 'absence_type', 'start_date', 'end_date')


class ShiftRequestSerializer(serializers.ModelSerializer):
    user_id = serializers.IntegerField(source='user.id', read_only=True)
    user_name = serializers.SerializerMethodField()
    request_type_label = serializers.CharField(source='get_request_type_display', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = EmployeeShiftRequest
        fields = (
            'id',
            'user_id',
            'user_name',
            'date',
            'request_type',
            'request_type_label',
            'start_time',
            'end_time',
            'reason',
            'status',
            'status_label',
            'created_at',
            'updated_at',
        )

    @extend_schema_field(serializers.CharField())
    def get_user_name(self, obj):
        return _format_user_name(obj.user)


class TaskSubmissionSerializer(serializers.ModelSerializer):
    author_id = serializers.IntegerField(source='author.id', read_only=True)
    author_name = serializers.SerializerMethodField()
    attachment_url = serializers.SerializerMethodField()
    attachment_name = serializers.SerializerMethodField()

    class Meta:
        model = TaskSubmission
        fields = (
            'id',
            'author_id',
            'author_name',
            'comment',
            'attachment_url',
            'attachment_name',
            'created_at',
        )

    @extend_schema_field(serializers.CharField())
    def get_author_name(self, obj):
        return _format_user_name(obj.author)

    @extend_schema_field(serializers.CharField())
    def get_attachment_url(self, obj):
        if obj.attachment:
            return obj.attachment.url
        return ''

    @extend_schema_field(serializers.CharField())
    def get_attachment_name(self, obj):
        if obj.attachment:
            return Path(obj.attachment.name).name
        return ''


class DepartmentTaskSerializer(serializers.ModelSerializer):
    assigned_to_id = serializers.IntegerField(source='assigned_to.id', read_only=True)
    assigned_to_name = serializers.SerializerMethodField()
    created_by_id = serializers.IntegerField(source='created_by.id', read_only=True)
    created_by_name = serializers.SerializerMethodField()
    task_type_label = serializers.CharField(source='get_task_type_display', read_only=True)
    priority_label = serializers.CharField(source='get_priority_display', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    is_overdue = serializers.SerializerMethodField()

    class Meta:
        model = DepartmentTask
        fields = (
            'id',
            'department_id',
            'assigned_to_id',
            'assigned_to_name',
            'created_by_id',
            'created_by_name',
            'date',
            'start_time',
            'end_time',
            'due_time',
            'title',
            'description',
            'task_type',
            'task_type_label',
            'priority',
            'priority_label',
            'status',
            'status_label',
            'is_overdue',
            'created_at',
            'updated_at',
        )

    @extend_schema_field(serializers.CharField())
    def get_assigned_to_name(self, obj):
        return _format_user_name(obj.assigned_to)

    @extend_schema_field(serializers.CharField())
    def get_created_by_name(self, obj):
        return _format_user_name(obj.created_by)

    @extend_schema_field(serializers.BooleanField())
    def get_is_overdue(self, obj):
        if obj.status == 'done':
            return False
        due = obj.due_time or obj.end_time
        now = timezone.localtime()
        if obj.date < now.date():
            return True
        return bool(due and obj.date == now.date() and due < now.time())


class TaskDetailSerializer(serializers.Serializer):
    task = DepartmentTaskSerializer()
    submissions = TaskSubmissionSerializer(many=True)


class ManagerAvailabilityOverviewSerializer(serializers.Serializer):
    department_id = serializers.IntegerField()
    department_name = serializers.CharField()
    week_start = serializers.DateField()
    week_end = serializers.DateField()
    week_offset = serializers.IntegerField()
    schedule_approved = serializers.BooleanField()
    employees = SimpleEmployeeSerializer(many=True)
    availability = AvailabilityEntrySerializer(many=True)
    absences = AbsenceEntrySerializer(many=True)
    shift_requests = ShiftRequestSerializer(many=True)


class ManagerScheduleApproveAssignmentSerializer(serializers.Serializer):
    user_id = serializers.IntegerField(min_value=1)
    date = serializers.DateField()
    start_time = serializers.TimeField(format='%H:%M', input_formats=['%H:%M'])
    end_time = serializers.TimeField(format='%H:%M', input_formats=['%H:%M'])


class ManagerScheduleApproveRequestSerializer(serializers.Serializer):
    dates = serializers.ListField(child=serializers.DateField(), allow_empty=False)
    assignments = ManagerScheduleApproveAssignmentSerializer(many=True)


class ManagerScheduleApproveResponseSerializer(serializers.Serializer):
    ok = serializers.BooleanField()
    approved = serializers.IntegerField()
    approved_at = serializers.DateTimeField()


class ManagerShiftRequestDecisionSerializer(serializers.Serializer):
    request_id = serializers.IntegerField(min_value=1)
    decision = serializers.ChoiceField(choices=['approved', 'rejected'])


class ManagerShiftRequestDecisionResponseSerializer(serializers.Serializer):
    ok = serializers.BooleanField()
    status = serializers.ChoiceField(choices=['approved', 'rejected'])


class ManagerTaskCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200)
    description = serializers.CharField(required=False, allow_blank=True)
    task_type = serializers.ChoiceField(choices=DepartmentTask.TASK_TYPES, default='employee')
    assigned_to = serializers.IntegerField(required=False, allow_null=True)
    date = serializers.DateField()
    start_time = serializers.TimeField(required=False, allow_null=True, format='%H:%M', input_formats=['%H:%M'])
    end_time = serializers.TimeField(required=False, allow_null=True, format='%H:%M', input_formats=['%H:%M'])
    due_time = serializers.TimeField(required=False, allow_null=True, format='%H:%M', input_formats=['%H:%M'])
    priority = serializers.ChoiceField(choices=DepartmentTask.PRIORITY_CHOICES, default='mid')


class ManagerTaskUpdateSerializer(serializers.Serializer):
    if_match = serializers.DateTimeField(required=False)
    title = serializers.CharField(max_length=200, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    task_type = serializers.ChoiceField(choices=DepartmentTask.TASK_TYPES, required=False)
    assigned_to = serializers.IntegerField(required=False, allow_null=True)
    date = serializers.DateField(required=False)
    start_time = serializers.TimeField(required=False, allow_null=True, format='%H:%M', input_formats=['%H:%M'])
    end_time = serializers.TimeField(required=False, allow_null=True, format='%H:%M', input_formats=['%H:%M'])
    due_time = serializers.TimeField(required=False, allow_null=True, format='%H:%M', input_formats=['%H:%M'])
    priority = serializers.ChoiceField(choices=DepartmentTask.PRIORITY_CHOICES, required=False)
    status = serializers.ChoiceField(choices=DepartmentTask.STATUS_CHOICES, required=False)


class ManagerTaskExtendSerializer(serializers.Serializer):
    if_match = serializers.DateTimeField(required=False)
    date = serializers.DateField()
    due_time = serializers.TimeField(required=False, allow_null=True, format='%H:%M', input_formats=['%H:%M'])


class EmployeeAvailabilityDaySerializer(serializers.Serializer):
    date = serializers.DateField()
    is_available = serializers.BooleanField()
    start_time = serializers.TimeField(required=False, allow_null=True, format='%H:%M', input_formats=['%H:%M'])
    end_time = serializers.TimeField(required=False, allow_null=True, format='%H:%M', input_formats=['%H:%M'])
    priority = serializers.ChoiceField(choices=EmployeeAvailability.PRIORITY_CHOICES, default='mid')


class EmployeeAvailabilityUpdateSerializer(serializers.Serializer):
    if_match = serializers.DateTimeField(required=False)
    days = EmployeeAvailabilityDaySerializer(many=True)


class EmployeeAvailabilityUpdateResponseSerializer(serializers.Serializer):
    ok = serializers.BooleanField()
    updated_at = serializers.DateTimeField()


class EmployeeShiftRequestCreateSerializer(serializers.Serializer):
    request_type = serializers.ChoiceField(choices=[choice[0] for choice in EmployeeShiftRequest.REQUEST_TYPES])
    date = serializers.DateField()
    reason = serializers.CharField()
    start_time = serializers.TimeField(required=False, allow_null=True, format='%H:%M', input_formats=['%H:%M'])
    end_time = serializers.TimeField(required=False, allow_null=True, format='%H:%M', input_formats=['%H:%M'])


class EmployeeTaskStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=DepartmentTask.STATUS_CHOICES)


class EmployeeTaskStatusResponseSerializer(serializers.Serializer):
    ok = serializers.BooleanField()
    status = serializers.ChoiceField(choices=DepartmentTask.STATUS_CHOICES)
    status_label = serializers.CharField()
    status_tone = serializers.CharField()


class EmployeeTaskSubmitResponseSerializer(serializers.Serializer):
    ok = serializers.BooleanField()
    status = serializers.ChoiceField(choices=DepartmentTask.STATUS_CHOICES)
    status_label = serializers.CharField()
    status_tone = serializers.CharField()
    submissions = TaskSubmissionSerializer(many=True)


class EmployeeTaskSubmitRequestSerializer(serializers.Serializer):
    comment = serializers.CharField(required=False, allow_blank=True)
    attachments = serializers.ListField(child=serializers.FileField(), required=False)


class ManagerAvailabilityOverviewView(generics.GenericAPIView):
    permission_classes = [IsManagerRole]
    serializer_class = ManagerAvailabilityOverviewSerializer

    @extend_schema(
        tags=['Manager'],
        summary='Обзор графика отдела',
        description='Возвращает доступности, отсутствия и запросы сотрудников отдела по неделе.',
        responses={200: ManagerAvailabilityOverviewSerializer, 400: DetailMessageSerializer},
    )
    def get(self, request):
        department = require_manager_department(request.user)
        today = timezone.localdate()
        week_offset = _parse_week_offset(request.query_params.get('week'), default=1)
        week_start = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)
        week_end = week_start + timedelta(days=6)

        profiles = EmployeeProfile.objects.select_related('user').filter(
            department=department,
            user__role='employee',
        )
        user_ids = list(profiles.values_list('user_id', flat=True))

        employees = [
            {'id': profile.user_id, 'name': _format_user_name(profile.user)}
            for profile in profiles
        ]

        availability = EmployeeAvailability.objects.filter(user_id__in=user_ids, date__range=(week_start, week_end))
        absences = EmployeeAbsence.objects.filter(
            user_id__in=user_ids,
            start_date__lte=week_end,
            end_date__gte=week_start,
        )
        shift_requests = EmployeeShiftRequest.objects.select_related('user').filter(
            user_id__in=user_ids,
            date__range=(week_start, week_end),
        ).order_by('-created_at')

        schedule_approved = EmployeeAvailability.objects.filter(
            user_id__in=user_ids,
            date__range=(week_start, week_end),
            is_approved=True,
        ).exists()

        payload = {
            'department_id': department.id,
            'department_name': department.name,
            'week_start': week_start,
            'week_end': week_end,
            'week_offset': week_offset,
            'schedule_approved': schedule_approved,
            'employees': employees,
            'availability': availability,
            'absences': absences,
            'shift_requests': shift_requests,
        }
        return Response(ManagerAvailabilityOverviewSerializer(payload).data)


class ManagerScheduleApproveView(generics.GenericAPIView):
    permission_classes = [IsManagerRole]
    serializer_class = ManagerScheduleApproveRequestSerializer

    @extend_schema(
        tags=['Manager'],
        summary='Утвердить график отдела',
        request=ManagerScheduleApproveRequestSerializer,
        responses={200: ManagerScheduleApproveResponseSerializer, 400: DetailMessageSerializer},
    )
    def post(self, request):
        require_manager_department(request.user)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        date_values = data['dates']
        assignments = data['assignments']

        user_ids = list(
            EmployeeProfile.objects.filter(department=department, user__role='employee').values_list('user_id', flat=True)
        )
        if not user_ids:
            raise ValidationError({'detail': 'Нет сотрудников для утверждения графика.'})

        absence_by_user_date = {}
        if user_ids and date_values:
            range_start = min(date_values)
            range_end = max(date_values)
            date_set = set(date_values)
            absences = EmployeeAbsence.objects.filter(
                user_id__in=user_ids,
                start_date__lte=range_end,
                end_date__gte=range_start,
            )
            for absence in absences:
                start_date = max(absence.start_date, range_start)
                end_date = min(absence.end_date, range_end)
                current = start_date
                while current <= end_date:
                    if current in date_set:
                        key = (absence.user_id, current)
                        if absence.absence_type == 'sick' or key not in absence_by_user_date:
                            absence_by_user_date[key] = absence.absence_type
                    current += timedelta(days=1)

        entries = EmployeeAvailability.objects.filter(user_id__in=user_ids, date__in=date_values)
        entries_map = {(entry.user_id, entry.date): entry for entry in entries}

        with transaction.atomic():
            entries.update(is_approved=True, approved_by=None, approved_at=None)
            approved_at = timezone.now()
            approved_count = 0

            for item in assignments:
                user_id = item['user_id']
                day_date = item['date']
                start_time = item['start_time']
                end_time = item['end_time']

                if user_id not in user_ids or day_date not in date_values:
                    continue
                if absence_by_user_date.get((user_id, day_date)):
                    continue

                entry = entries_map.get((user_id, day_date))
                if entry:
                    entry.is_available = True
                    entry.start_time = start_time
                    entry.end_time = end_time
                    entry.is_approved = True
                    entry.approved_by = request.user
                    entry.approved_at = approved_at
                    entry.save(
                        update_fields=[
                            'is_available',
                            'start_time',
                            'end_time',
                            'is_approved',
                            'approved_by',
                            'approved_at',
                            'updated_at',
                        ]
                    )
                else:
                    EmployeeAvailability.objects.create(
                        user_id=user_id,
                        date=day_date,
                        is_available=True,
                        start_time=start_time,
                        end_time=end_time,
                        priority='mid',
                        is_approved=True,
                        approved_by=request.user,
                        approved_at=approved_at,
                    )
                approved_count += 1

        payload = {
            'ok': True,
            'approved': approved_count,
            'approved_at': timezone.localtime(approved_at),
        }
        return Response(ManagerScheduleApproveResponseSerializer(payload).data)


class ManagerShiftRequestListView(BoundedListMixin, generics.ListAPIView):
    permission_classes = [IsManagerRole]
    serializer_class = ShiftRequestSerializer

    @extend_schema(tags=['Manager'], summary='Запросы сотрудников по сменам')
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        department = require_manager_department(self.request.user)
        week_offset = _parse_week_offset(self.request.query_params.get('week'), default=1)
        today = timezone.localdate()
        week_start = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)
        week_end = week_start + timedelta(days=6)
        user_ids = list(
            EmployeeProfile.objects.filter(department=department, user__role='employee').values_list('user_id', flat=True)
        )
        queryset = EmployeeShiftRequest.objects.select_related('user').filter(
            user_id__in=user_ids,
            date__range=(week_start, week_end),
        )
        status_filter = (self.request.query_params.get('status') or '').strip().lower()
        if status_filter in {'pending', 'approved', 'rejected'}:
            queryset = queryset.filter(status=status_filter)
        return queryset.order_by('-created_at')


class ManagerShiftRequestDecisionView(generics.GenericAPIView):
    permission_classes = [IsManagerRole]
    serializer_class = ManagerShiftRequestDecisionSerializer

    @extend_schema(
        tags=['Manager'],
        summary='Решение по запросу сотрудника',
        request=ManagerShiftRequestDecisionSerializer,
        responses={200: ManagerShiftRequestDecisionResponseSerializer, 400: DetailMessageSerializer, 403: DetailMessageSerializer},
    )
    def post(self, request):
        require_manager_department(request.user)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        request_id = serializer.validated_data['request_id']
        decision = serializer.validated_data['decision']

        shift_request = get_object_or_404(EmployeeShiftRequest, id=request_id)
        if not can_manage_employee(request.user, shift_request.user):
            raise PermissionDenied('Нет доступа к запросу.')

        shift_request.status = decision
        shift_request.save(update_fields=['status', 'updated_at'])

        if decision == 'approved':
            settings_obj = GlobalSettings.objects.first() or GlobalSettings.objects.create()
            start_time = shift_request.start_time or settings_obj.work_start
            end_time = shift_request.end_time or settings_obj.work_end
            entry, _ = EmployeeAvailability.objects.get_or_create(
                user_id=shift_request.user_id,
                date=shift_request.date,
                defaults={
                    'is_available': True,
                    'start_time': start_time,
                    'end_time': end_time,
                    'priority': 'mid',
                },
            )
            entry.is_available = shift_request.request_type != 'replacement'
            entry.start_time = start_time
            entry.end_time = end_time
            entry.is_approved = True
            entry.approved_by = request.user
            entry.approved_at = timezone.now()
            entry.save(
                update_fields=[
                    'is_available',
                    'start_time',
                    'end_time',
                    'priority',
                    'is_approved',
                    'approved_by',
                    'approved_at',
                    'updated_at',
                ]
            )

        return Response({'ok': True, 'status': decision})


class ManagerTaskListCreateView(BoundedListMixin, generics.ListCreateAPIView):
    permission_classes = [IsManagerRole]

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return ManagerTaskCreateSerializer
        return DepartmentTaskSerializer

    @extend_schema(tags=['Manager'], summary='Список задач отдела')
    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)

    def get_queryset(self):
        department = require_manager_department(self.request.user)
        week_offset = _parse_week_offset(self.request.query_params.get('week'), default=0)
        today = timezone.localdate()
        week_start = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)
        week_end = week_start + timedelta(days=6)

        queryset = DepartmentTask.objects.filter(department=department, date__range=(week_start, week_end))

        status_filter = (self.request.query_params.get('status') or '').strip().lower()
        if status_filter in {'todo', 'in_progress', 'done'}:
            queryset = queryset.filter(status=status_filter)

        priority_filter = (self.request.query_params.get('priority') or '').strip().lower()
        if priority_filter in {'high', 'mid', 'low'}:
            queryset = queryset.filter(priority=priority_filter)

        type_filter = (self.request.query_params.get('type') or '').strip().lower()
        if type_filter in {'employee', 'slot', 'department'}:
            queryset = queryset.filter(task_type=type_filter)

        employee_param = (self.request.query_params.get('employee') or '').strip()
        if employee_param and employee_param != 'all':
            try:
                employee_id = int(employee_param)
            except (TypeError, ValueError):
                employee_id = None
            if employee_id:
                if type_filter == 'slot':
                    queryset = queryset.filter(task_type='slot')
                elif type_filter == 'department':
                    queryset = queryset.filter(task_type='department')
                elif type_filter == 'employee':
                    queryset = queryset.filter(assigned_to_id=employee_id)
                else:
                    queryset = queryset.filter(
                        Q(assigned_to_id=employee_id) | Q(task_type='slot') | Q(task_type='department')
                    )

        return queryset.select_related('assigned_to', 'created_by').order_by('-date', '-created_at')

    @extend_schema(
        tags=['Manager'],
        summary='Создать задачу',
        request=ManagerTaskCreateSerializer,
        responses={201: DepartmentTaskSerializer, 400: DetailMessageSerializer},
    )
    def post(self, request, *args, **kwargs):
        return self.create(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        department = require_manager_department(request.user)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        assigned_to = None
        if data['task_type'] == 'employee':
            assigned_to_id = data.get('assigned_to')
            if not assigned_to_id:
                raise ValidationError({'detail': 'Выберите сотрудника.'})
            user_model = get_user_model()
            assigned_to = user_model.objects.filter(id=assigned_to_id, role='employee', is_active=True).first()
            if not assigned_to or not EmployeeProfile.objects.filter(user=assigned_to, department=department).exists():
                raise ValidationError({'detail': 'Сотрудник не найден.'})

        start_time = data.get('start_time')
        end_time = data.get('end_time')
        due_time = data.get('due_time') or end_time

        if start_time and end_time and start_time >= end_time:
            raise ValidationError({'detail': 'Время окончания должно быть позже начала.'})
        if data['task_type'] == 'slot' and (start_time is None or end_time is None):
            raise ValidationError({'detail': 'Для слота укажите время.'})

        task = DepartmentTask.objects.create(
            department=department,
            created_by=request.user,
            assigned_to=assigned_to,
            date=data['date'],
            start_time=start_time,
            end_time=end_time,
            due_time=due_time,
            title=data['title'].strip(),
            description=(data.get('description') or '').strip(),
            task_type=data['task_type'],
            priority=data.get('priority', 'mid'),
            status='todo',
        )
        return Response(DepartmentTaskSerializer(task).data, status=status.HTTP_201_CREATED)


class ManagerTaskDetailView(generics.GenericAPIView):
    permission_classes = [IsManagerRole]
    serializer_class = ManagerTaskUpdateSerializer

    def _get_task(self, task_id):
        department = require_manager_department(self.request.user)
        return get_object_or_404(
            DepartmentTask.objects.select_related('assigned_to', 'created_by'),
            id=task_id,
            department=department,
        )

    @extend_schema(
        tags=['Manager'],
        summary='Детали задачи отдела',
        responses={200: TaskDetailSerializer, 400: DetailMessageSerializer, 404: DetailMessageSerializer},
    )
    def get(self, request, task_id):
        task = self._get_task(task_id)
        submissions = TaskSubmission.objects.select_related('author').filter(task=task).order_by('-created_at')
        payload = {
            'task': DepartmentTaskSerializer(task).data,
            'submissions': TaskSubmissionSerializer(submissions, many=True).data,
        }
        response = Response(payload)
        return set_resource_version_header(response, task)

    @extend_schema(
        tags=['Manager'],
        summary='Обновить задачу',
        request=ManagerTaskUpdateSerializer,
        responses={200: DepartmentTaskSerializer, 400: DetailMessageSerializer, 404: DetailMessageSerializer},
    )
    def patch(self, request, task_id):
        task = self._get_task(task_id)
        department = require_manager_department(request.user)

        serializer = self.get_serializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        assert_optimistic_lock(request, task, client_version=data.get('if_match'))

        task_type = data.get('task_type', task.task_type)
        assigned_to = task.assigned_to
        if task_type == 'employee':
            assigned_to_id = data.get('assigned_to', task.assigned_to_id)
            if not assigned_to_id:
                raise ValidationError({'detail': 'Выберите сотрудника.'})
            user_model = get_user_model()
            assigned_to = user_model.objects.filter(id=assigned_to_id, role='employee', is_active=True).first()
            if not assigned_to or not EmployeeProfile.objects.filter(user=assigned_to, department=department).exists():
                raise ValidationError({'detail': 'Сотрудник не найден.'})
        elif 'assigned_to' in data or task_type in {'slot', 'department'}:
            assigned_to = None

        start_time = data.get('start_time', task.start_time)
        end_time = data.get('end_time', task.end_time)
        due_time = data.get('due_time', task.due_time)
        if 'end_time' in data and 'due_time' not in data:
            due_time = end_time

        if start_time and end_time and start_time >= end_time:
            raise ValidationError({'detail': 'Время окончания должно быть позже начала.'})
        if task_type == 'slot' and (start_time is None or end_time is None):
            raise ValidationError({'detail': 'Для слота укажите время.'})

        if 'title' in data:
            title = data['title'].strip()
            if not title:
                raise ValidationError({'detail': 'Укажите название задачи.'})
            task.title = title
        if 'description' in data:
            task.description = (data.get('description') or '').strip()

        task.task_type = task_type
        task.assigned_to = assigned_to
        task.date = data.get('date', task.date)
        task.start_time = start_time
        task.end_time = end_time
        task.due_time = due_time
        task.priority = data.get('priority', task.priority)
        if 'status' in data:
            task.status = data['status']
        task.save()
        response = Response(DepartmentTaskSerializer(task).data)
        return set_resource_version_header(response, task)

    @extend_schema(
        tags=['Manager'],
        summary='Удалить задачу',
        responses={204: None, 400: DetailMessageSerializer, 404: DetailMessageSerializer},
    )
    def delete(self, request, task_id):
        task = self._get_task(task_id)
        task.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ManagerTaskExtendView(generics.GenericAPIView):
    permission_classes = [IsManagerRole]
    serializer_class = ManagerTaskExtendSerializer

    @extend_schema(
        tags=['Manager'],
        summary='Продлить задачу',
        request=ManagerTaskExtendSerializer,
        responses={200: DepartmentTaskSerializer, 400: DetailMessageSerializer, 404: DetailMessageSerializer},
    )
    def post(self, request, task_id):
        department = require_manager_department(request.user)
        task = get_object_or_404(DepartmentTask, id=task_id, department=department)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        assert_optimistic_lock(request, task, client_version=serializer.validated_data.get('if_match'))

        new_date = serializer.validated_data['date']
        due_time = serializer.validated_data.get('due_time') or task.due_time or task.end_time

        task.date = new_date
        task.due_time = due_time
        task.save(update_fields=['date', 'due_time', 'updated_at'])
        response = Response(DepartmentTaskSerializer(task).data)
        return set_resource_version_header(response, task)


class EmployeeAvailabilityView(generics.GenericAPIView):
    permission_classes = [IsEmployeeRole]
    serializer_class = EmployeeAvailabilityUpdateSerializer

    @extend_schema(tags=['Employee'], summary='Доступность сотрудника на неделю')
    def get(self, request):
        today = timezone.localdate()
        week_offset = _parse_week_offset(request.query_params.get('week'), default=0)

        week_start = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)
        week_end = week_start + timedelta(days=6)
        is_current_week = week_offset == 0

        settings_obj = GlobalSettings.objects.first() or GlobalSettings.objects.create()
        entries = list(
            EmployeeAvailability.objects.filter(
                user=request.user,
                date__range=(week_start, week_end),
            )
        )
        entries_map = {entry.date: entry for entry in entries}
        week_updated_at = max((entry.updated_at for entry in entries), default=None)

        current_week_start = today - timedelta(days=today.weekday())
        current_week_friday = current_week_start + timedelta(days=4)
        next_week_start = current_week_start + timedelta(days=7)
        next_week_end = next_week_start + timedelta(days=6)
        next_week_edit_closed = today > current_week_friday

        week_is_approved = EmployeeAvailability.objects.filter(
            user=request.user,
            date__range=(week_start, week_end),
            is_approved=True,
        ).exists()

        is_next_week = week_start == next_week_start and week_end == next_week_end
        is_locked = is_current_week or week_is_approved or (is_next_week and next_week_edit_closed)

        days = []
        for i in range(7):
            day = week_start + timedelta(days=i)
            entry = entries_map.get(day)
            days.append(
                {
                    'date': day,
                    'is_available': entry.is_available if entry else False,
                    'start_time': entry.start_time if entry else settings_obj.work_start,
                    'end_time': entry.end_time if entry else settings_obj.work_end,
                    'priority': entry.priority if entry else 'mid',
                    'is_approved': bool(entry and entry.is_approved),
                }
            )

        return Response(
            {
                'week_start': week_start,
                'week_end': week_end,
                'week_offset': week_offset,
                'is_locked': is_locked,
                'week_is_approved': week_is_approved,
                'week_updated_at': timezone.localtime(week_updated_at).isoformat() if week_updated_at else None,
                'days': days,
            }
        )

    @extend_schema(
        tags=['Employee'],
        summary='Сохранить доступность сотрудника',
        request=EmployeeAvailabilityUpdateSerializer,
        responses={200: EmployeeAvailabilityUpdateResponseSerializer, 400: DetailMessageSerializer, 403: DetailMessageSerializer},
    )
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated_data = serializer.validated_data
        updates_map = {item['date']: item for item in validated_data['days']}
        if not updates_map:
            raise ValidationError({'detail': 'Нет данных для сохранения.'})

        settings_obj = GlobalSettings.objects.first() or GlobalSettings.objects.create()
        dates = list(updates_map.keys())
        today = timezone.localdate()
        current_week_start = today - timedelta(days=today.weekday())
        current_week_end = current_week_start + timedelta(days=6)
        current_week_friday = current_week_start + timedelta(days=4)
        next_week_start = current_week_end + timedelta(days=1)
        next_week_end = next_week_start + timedelta(days=6)

        if any(current_week_start <= day <= current_week_end for day in dates):
            return Response({'detail': 'Текущая неделя заблокирована для редактирования.'}, status=status.HTTP_403_FORBIDDEN)

        if EmployeeAvailability.objects.filter(user=request.user, date__in=dates, is_approved=True).exists():
            return Response({'detail': 'График уже подтвержден менеджером.'}, status=status.HTTP_403_FORBIDDEN)

        if today > current_week_friday and any(next_week_start <= day <= next_week_end for day in dates):
            return Response(
                {'detail': 'Редактирование следующей недели доступно только до пятницы.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        existing_entries = EmployeeAvailability.objects.filter(user=request.user, date__in=dates)
        assert_queryset_optimistic_lock(
            request,
            existing_entries,
            client_version=validated_data.get('if_match'),
        )
        existing_map = {entry.date: entry for entry in existing_entries}

        with transaction.atomic():
            for payload_item in updates_map.values():
                start_time = payload_item.get('start_time') or settings_obj.work_start
                end_time = payload_item.get('end_time') or settings_obj.work_end
                entry = existing_map.get(payload_item['date'])
                if entry:
                    entry.is_available = payload_item['is_available']
                    entry.start_time = start_time
                    entry.end_time = end_time
                    entry.priority = payload_item.get('priority', 'mid')
                    entry.is_approved = False
                    entry.approved_by = None
                    entry.approved_at = None
                    entry.save(
                        update_fields=[
                            'is_available',
                            'start_time',
                            'end_time',
                            'priority',
                            'is_approved',
                            'approved_by',
                            'approved_at',
                            'updated_at',
                        ]
                    )
                else:
                    EmployeeAvailability.objects.create(
                        user=request.user,
                        date=payload_item['date'],
                        is_available=payload_item['is_available'],
                        start_time=start_time,
                        end_time=end_time,
                        priority=payload_item.get('priority', 'mid'),
                    )

        updated_at = timezone.localtime(timezone.now())
        return Response({'ok': True, 'updated_at': updated_at.isoformat()})


class EmployeeShiftRequestListCreateView(BoundedListMixin, generics.ListCreateAPIView):
    permission_classes = [IsEmployeeRole]

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return EmployeeShiftRequestCreateSerializer
        return ShiftRequestSerializer

    @extend_schema(tags=['Employee'], summary='Запросы сотрудника по сменам')
    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)

    def get_queryset(self):
        return EmployeeShiftRequest.objects.select_related('user').filter(user=self.request.user).order_by('-created_at')

    @extend_schema(
        tags=['Employee'],
        summary='Создать запрос по смене',
        request=EmployeeShiftRequestCreateSerializer,
        responses={201: ShiftRequestSerializer, 400: DetailMessageSerializer},
    )
    def post(self, request, *args, **kwargs):
        return self.create(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        start_time = data.get('start_time')
        end_time = data.get('end_time')
        if data['request_type'] == 'extra_hours' and (start_time is None or end_time is None):
            raise ValidationError({'detail': 'Укажите время дополнительных часов.'})

        shift_request = EmployeeShiftRequest.objects.create(
            user=request.user,
            date=data['date'],
            request_type=data['request_type'],
            start_time=start_time,
            end_time=end_time,
            reason=data['reason'].strip(),
        )
        return Response(ShiftRequestSerializer(shift_request).data, status=status.HTTP_201_CREATED)


class EmployeeTaskListView(BoundedListMixin, generics.ListAPIView):
    permission_classes = [IsEmployeeRole]
    serializer_class = DepartmentTaskSerializer

    @extend_schema(tags=['Employee'], summary='Список доступных задач сотрудника')
    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)

    def get_queryset(self):
        user = self.request.user
        profile = EmployeeProfile.objects.select_related('department').filter(user=user).first()
        if not profile or not profile.department:
            return DepartmentTask.objects.none()

        department = profile.department
        base_tasks = DepartmentTask.objects.filter(department=department)
        assigned_tasks = base_tasks.filter(task_type='employee', assigned_to=user)
        department_tasks = base_tasks.filter(task_type='department')

        availability_entries = list(EmployeeAvailability.objects.filter(user=user, is_available=True))
        slot_tasks = DepartmentTask.objects.none()
        if availability_entries:
            slot_filters = Q()
            for entry in availability_entries:
                slot_filters |= Q(date=entry.date, start_time=entry.start_time, end_time=entry.end_time)
            if slot_filters:
                slot_tasks = base_tasks.filter(task_type='slot').filter(slot_filters)

        queryset = (assigned_tasks | slot_tasks | department_tasks).distinct().select_related('assigned_to', 'created_by')

        view_mode = (self.request.query_params.get('view') or 'active').strip().lower()
        if view_mode == 'archive':
            queryset = queryset.filter(status='done')
        else:
            queryset = queryset.exclude(status='done')

        return queryset.order_by('-date', '-created_at')


class EmployeeTaskDetailView(generics.GenericAPIView):
    permission_classes = [IsEmployeeRole]

    @extend_schema(
        tags=['Employee'],
        summary='Детали задачи сотрудника',
        responses={200: TaskDetailSerializer, 403: DetailMessageSerializer, 404: DetailMessageSerializer},
    )
    def get(self, request, task_id):
        task = get_object_or_404(DepartmentTask.objects.select_related('assigned_to', 'created_by'), id=task_id)
        require_employee_task_access(request.user, task)

        submissions = TaskSubmission.objects.select_related('author').filter(task=task).order_by('-created_at')
        payload = {
            'task': DepartmentTaskSerializer(task).data,
            'submissions': TaskSubmissionSerializer(submissions, many=True).data,
        }
        return Response(payload)


class EmployeeTaskStatusUpdateView(generics.GenericAPIView):
    permission_classes = [IsEmployeeRole]
    serializer_class = EmployeeTaskStatusSerializer

    @extend_schema(
        tags=['Employee'],
        summary='Изменить статус задачи',
        request=EmployeeTaskStatusSerializer,
        responses={200: EmployeeTaskStatusResponseSerializer, 400: DetailMessageSerializer, 403: DetailMessageSerializer},
    )
    def post(self, request, task_id):
        task = get_object_or_404(DepartmentTask, id=task_id)
        require_employee_task_access(request.user, task)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        status_value = serializer.validated_data['status']

        status_order = {'todo': 0, 'in_progress': 1, 'done': 2}
        if status_order.get(status_value, 0) < status_order.get(task.status, 0):
            raise ValidationError({'detail': 'Нельзя вернуть задачу назад.'})

        if task.status != status_value:
            task.status = status_value
            task.save(update_fields=['status', 'updated_at'])

        status_labels = {'todo': 'Назначена', 'in_progress': 'В работе', 'done': 'Выполнено'}
        status_tones = {'todo': 'muted', 'in_progress': 'warning', 'done': 'success'}
        return Response(
            {
                'ok': True,
                'status': task.status,
                'status_label': status_labels.get(task.status, 'Назначена'),
                'status_tone': status_tones.get(task.status, 'muted'),
            }
        )


class EmployeeTaskSubmissionCreateView(generics.GenericAPIView):
    permission_classes = [IsEmployeeRole]
    parser_classes = [MultiPartParser, FormParser]
    serializer_class = EmployeeTaskSubmitRequestSerializer

    @extend_schema(
        tags=['Employee'],
        summary='Отправить сдачу по задаче',
        request=EmployeeTaskSubmitRequestSerializer,
        responses={200: EmployeeTaskSubmitResponseSerializer, 400: DetailMessageSerializer, 403: DetailMessageSerializer},
    )
    def post(self, request, task_id):
        task = get_object_or_404(DepartmentTask, id=task_id)
        require_employee_task_access(request.user, task)

        comment = (request.POST.get('comment') or '').strip()
        attachments = request.FILES.getlist('attachments')
        if not comment and not attachments:
            raise ValidationError({'detail': 'Добавьте комментарий или файл.'})
        _validate_submission_attachments(attachments)

        created_submissions = []
        if attachments:
            for attachment in attachments:
                created_submissions.append(
                    TaskSubmission.objects.create(
                        task=task,
                        author=request.user,
                        comment=comment,
                        attachment=attachment,
                    )
                )
        else:
            created_submissions.append(
                TaskSubmission.objects.create(
                    task=task,
                    author=request.user,
                    comment=comment,
                )
            )

        if task.task_type != 'department' and task.status != 'done':
            task.status = 'done'
            task.save(update_fields=['status', 'updated_at'])

        status_labels = {'todo': 'Назначена', 'in_progress': 'В работе', 'done': 'Выполнено'}
        status_tones = {'todo': 'muted', 'in_progress': 'warning', 'done': 'success'}

        payload = {
            'ok': True,
            'status': task.status,
            'status_label': status_labels.get(task.status, 'Назначена'),
            'status_tone': status_tones.get(task.status, 'success'),
            'submissions': TaskSubmissionSerializer(created_submissions, many=True).data,
        }
        return Response(payload)
