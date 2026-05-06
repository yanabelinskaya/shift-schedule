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
    set_resource_version_header,
)
from .availability import (
    ensure_employee_base_availability,
    get_week_start,
    resolve_range_availability,
)
from .object_access import (
    can_manage_employee,
    get_manager_task_or_404,
    require_employee_task_access,
    require_manager_department,
)
from .pagination import BoundedListMixin
from .serializers import DetailMessageSerializer
from .models import (
    Department,
    DepartmentTask,
    EmployeeAbsence,
    EmployeeAvailabilityOverride,
    EmployeeBaseAvailability,
    EmployeePlannedLoad,
    EmployeeProfile,
    EmployeeShiftRequest,
    GlobalSettings,
    LeaveRequest,
    Sprint,
    Substitution,
    TaskMessage,
    TaskMessageReadState,
    TaskSubmission,
)
from .permissions import IsEmployeeRole, IsManagerRole
from .notifications import (
    notify_task_taken,
    notify_task_dropped,
    notify_task_on_review,
    notify_task_returned,
    notify_task_completed,
    record_status_change,
)


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


TASK_STATUS_LABELS = {
    DepartmentTask.TaskStatus.AWAITING_CONFIRMATION: 'Новая',
    DepartmentTask.TaskStatus.CONFIRMED: 'Взята',
    DepartmentTask.TaskStatus.IN_PROGRESS: 'В работе',
    DepartmentTask.TaskStatus.ON_REVIEW: 'На проверке',
    DepartmentTask.TaskStatus.COMPLETED: 'Выполнена',
    DepartmentTask.TaskStatus.RETURNED: 'Возвращена на доработку',
    DepartmentTask.TaskStatus.CONFLICT: 'Конфликт',
}

TASK_STATUS_TONES = {
    DepartmentTask.TaskStatus.AWAITING_CONFIRMATION: 'muted',
    DepartmentTask.TaskStatus.CONFIRMED: 'info',
    DepartmentTask.TaskStatus.IN_PROGRESS: 'warning',
    DepartmentTask.TaskStatus.ON_REVIEW: 'info',
    DepartmentTask.TaskStatus.COMPLETED: 'success',
    DepartmentTask.TaskStatus.RETURNED: 'danger',
    DepartmentTask.TaskStatus.CONFLICT: 'danger',
}

EMPLOYEE_STATUS_TRANSITIONS = {
    DepartmentTask.TaskStatus.AWAITING_CONFIRMATION: set(),
    DepartmentTask.TaskStatus.CONFIRMED: {
        DepartmentTask.TaskStatus.IN_PROGRESS,
    },
    DepartmentTask.TaskStatus.IN_PROGRESS: {
        DepartmentTask.TaskStatus.ON_REVIEW,
        DepartmentTask.TaskStatus.COMPLETED,
    },
    DepartmentTask.TaskStatus.ON_REVIEW: set(),
    DepartmentTask.TaskStatus.COMPLETED: set(),
    DepartmentTask.TaskStatus.RETURNED: {
        DepartmentTask.TaskStatus.IN_PROGRESS,
    },
    DepartmentTask.TaskStatus.CONFLICT: {
        DepartmentTask.TaskStatus.CONFIRMED,
        DepartmentTask.TaskStatus.IN_PROGRESS,
    },
}


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


class AvailabilityEntrySerializer(serializers.Serializer):
    user_id = serializers.IntegerField()
    date = serializers.DateField()
    is_available = serializers.BooleanField()
    start_time = serializers.TimeField(allow_null=True, required=False)
    end_time = serializers.TimeField(allow_null=True, required=False)
    mode = serializers.CharField()
    source = serializers.CharField()
    override_type = serializers.CharField(allow_blank=True, required=False)
    absence_type = serializers.CharField(allow_blank=True, required=False)


class BaseAvailabilityRuleSerializer(serializers.ModelSerializer):
    user_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = EmployeeBaseAvailability
        fields = ("user_id", "weekday", "mode", "start_time", "end_time", "updated_at")


class AvailabilityOverrideSerializer(serializers.ModelSerializer):
    user_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = EmployeeAvailabilityOverride
        fields = (
            "id",
            "user_id",
            "date",
            "override_type",
            "start_time",
            "end_time",
            "note",
            "created_at",
            "updated_at",
        )


class PlannedLoadSerializer(serializers.ModelSerializer):
    user_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = EmployeePlannedLoad
        fields = ("user_id", "week_start", "target_mode", "target_value", "note", "updated_at")


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
        if obj.status == DepartmentTask.TaskStatus.COMPLETED:
            return False
        due = obj.due_time
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
    employees = SimpleEmployeeSerializer(many=True)
    availability = AvailabilityEntrySerializer(many=True)
    base_availability = BaseAvailabilityRuleSerializer(many=True)
    overrides = AvailabilityOverrideSerializer(many=True)
    planned_loads = PlannedLoadSerializer(many=True)
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
    due_time = serializers.TimeField(required=False, allow_null=True, format='%H:%M', input_formats=['%H:%M'])
    priority = serializers.ChoiceField(choices=DepartmentTask.PRIORITY_CHOICES, default='mid')


class ManagerTaskUpdateSerializer(serializers.Serializer):
    if_match = serializers.DateTimeField(required=False)
    title = serializers.CharField(max_length=200, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    task_type = serializers.ChoiceField(choices=DepartmentTask.TASK_TYPES, required=False)
    assigned_to = serializers.IntegerField(required=False, allow_null=True)
    date = serializers.DateField(required=False)
    due_time = serializers.TimeField(required=False, allow_null=True, format='%H:%M', input_formats=['%H:%M'])
    priority = serializers.ChoiceField(choices=DepartmentTask.PRIORITY_CHOICES, required=False)
    status = serializers.ChoiceField(choices=DepartmentTask.STATUS_CHOICES, required=False)


class ManagerTaskExtendSerializer(serializers.Serializer):
    if_match = serializers.DateTimeField(required=False)
    date = serializers.DateField()
    due_time = serializers.TimeField(required=False, allow_null=True, format='%H:%M', input_formats=['%H:%M'])


class EmployeeBaseAvailabilityItemSerializer(serializers.Serializer):
    weekday = serializers.IntegerField(min_value=0, max_value=6)
    mode = serializers.ChoiceField(choices=[choice[0] for choice in EmployeeBaseAvailability.MODE_CHOICES])
    start_time = serializers.TimeField(required=False, allow_null=True, format='%H:%M', input_formats=['%H:%M'])
    end_time = serializers.TimeField(required=False, allow_null=True, format='%H:%M', input_formats=['%H:%M'])


class EmployeeAvailabilityOverrideItemSerializer(serializers.Serializer):
    date = serializers.DateField()
    override_type = serializers.ChoiceField(
        choices=[choice[0] for choice in EmployeeAvailabilityOverride.OVERRIDE_TYPE_CHOICES]
    )
    start_time = serializers.TimeField(required=False, allow_null=True, format='%H:%M', input_formats=['%H:%M'])
    end_time = serializers.TimeField(required=False, allow_null=True, format='%H:%M', input_formats=['%H:%M'])
    note = serializers.CharField(required=False, allow_blank=True)


class EmployeePlannedLoadItemSerializer(serializers.Serializer):
    week_start = serializers.DateField()
    target_mode = serializers.ChoiceField(choices=[choice[0] for choice in EmployeePlannedLoad.TARGET_MODE_CHOICES])
    target_value = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    note = serializers.CharField(required=False, allow_blank=True)


class EmployeeAvailabilityUpdateSerializer(serializers.Serializer):
    base = EmployeeBaseAvailabilityItemSerializer(many=True, required=False)
    overrides = EmployeeAvailabilityOverrideItemSerializer(many=True, required=False)
    clear_override_dates = serializers.ListField(child=serializers.DateField(), required=False)
    load_plan = EmployeePlannedLoadItemSerializer(required=False)


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
        description='Возвращает доступность сотрудников (база + исключения) и загрузку по неделе.',
        responses={200: ManagerAvailabilityOverviewSerializer, 400: DetailMessageSerializer},
    )
    def get(self, request):
        department = require_manager_department(request.user)
        today = timezone.localdate()
        week_offset = _parse_week_offset(request.query_params.get('week'), default=1)
        week_start = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)
        week_end = week_start + timedelta(days=6)
        settings_obj = GlobalSettings.objects.first() or GlobalSettings.objects.create()

        profiles = EmployeeProfile.objects.select_related('user').filter(
            department=department,
            user__role='employee',
        )
        user_ids = list(profiles.values_list('user_id', flat=True))

        employees = [
            {'id': profile.user_id, 'name': _format_user_name(profile.user)}
            for profile in profiles
        ]

        availability = []
        for profile in profiles:
            resolved = resolve_range_availability(
                profile.user,
                week_start,
                week_end,
                settings_obj=settings_obj,
            )
            for day_date, state in resolved.items():
                availability.append(
                    {
                        'user_id': profile.user_id,
                        'date': day_date,
                        'is_available': bool(state.get('is_available')),
                        'start_time': state.get('start_time'),
                        'end_time': state.get('end_time'),
                        'mode': state.get('mode') or 'off',
                        'source': state.get('source') or 'base',
                        'override_type': state.get('override_type') or '',
                        'absence_type': state.get('absence_type') or '',
                    }
                )

        base_availability = EmployeeBaseAvailability.objects.filter(user_id__in=user_ids).order_by(
            'user_id',
            'weekday',
        )
        overrides = EmployeeAvailabilityOverride.objects.filter(
            user_id__in=user_ids,
            date__range=(week_start, week_end),
        ).order_by('date', 'id')
        planned_loads = EmployeePlannedLoad.objects.filter(
            user_id__in=user_ids,
            week_start=week_start,
        )
        absences = EmployeeAbsence.objects.filter(
            user_id__in=user_ids,
            start_date__lte=week_end,
            end_date__gte=week_start,
        )
        shift_requests = EmployeeShiftRequest.objects.select_related('user').filter(
            user_id__in=user_ids,
            date__range=(week_start, week_end),
        ).order_by('-created_at')

        payload = {
            'department_id': department.id,
            'department_name': department.name,
            'week_start': week_start,
            'week_end': week_end,
            'week_offset': week_offset,
            'employees': employees,
            'availability': availability,
            'base_availability': base_availability,
            'overrides': overrides,
            'planned_loads': planned_loads,
            'absences': absences,
            'shift_requests': shift_requests,
        }
        return Response(ManagerAvailabilityOverviewSerializer(payload).data)


class ManagerScheduleApproveView(generics.GenericAPIView):
    permission_classes = [IsManagerRole]
    serializer_class = ManagerScheduleApproveRequestSerializer

    @extend_schema(
        tags=['Manager'],
        summary='Устаревший эндпоинт утверждения графика',
        request=ManagerScheduleApproveRequestSerializer,
        responses={200: ManagerScheduleApproveResponseSerializer, 400: DetailMessageSerializer},
    )
    def post(self, request):
        raise ValidationError(
            {
                'detail': (
                    'Этап утверждения графика отключен. '
                    'Используйте доступность сотрудников и назначение задач без блокировок.'
                )
            }
        )


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
            override_type = 'partial'
            override_start = start_time
            override_end = end_time
            if shift_request.request_type == 'replacement':
                override_type = 'unavailable'
                override_start = None
                override_end = None

            EmployeeAvailabilityOverride.objects.update_or_create(
                user_id=shift_request.user_id,
                date=shift_request.date,
                defaults={
                    'override_type': override_type,
                    'start_time': override_start,
                    'end_time': override_end,
                    'note': (shift_request.reason or '')[:255],
                    'created_by': request.user,
                },
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
        allowed_statuses = {choice[0] for choice in DepartmentTask.TaskStatus.choices}
        if status_filter in allowed_statuses:
            queryset = queryset.filter(status=status_filter)

        priority_filter = (self.request.query_params.get('priority') or '').strip().lower()
        if priority_filter in {'critical', 'high', 'mid', 'low'}:
            queryset = queryset.filter(priority=priority_filter)

        type_filter = (self.request.query_params.get('type') or '').strip().lower()
        if type_filter in {'employee', 'department'}:
            queryset = queryset.filter(task_type=type_filter)

        employee_param = (self.request.query_params.get('employee') or '').strip()
        if employee_param and employee_param != 'all':
            try:
                employee_id = int(employee_param)
            except (TypeError, ValueError):
                employee_id = None
            if employee_id:
                if type_filter == 'department':
                    queryset = queryset.filter(task_type='department')
                elif type_filter == 'employee':
                    queryset = queryset.filter(assigned_to_id=employee_id)
                else:
                    queryset = queryset.filter(
                        Q(assigned_to_id=employee_id) | Q(task_type='department')
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
            if assigned_to_id:
                user_model = get_user_model()
                assigned_to = user_model.objects.filter(id=assigned_to_id, role='employee', is_active=True).first()
                if not assigned_to or not EmployeeProfile.objects.filter(user=assigned_to, department=department).exists():
                    raise ValidationError({'detail': 'Сотрудник не найден.'})

        task = DepartmentTask.objects.create(
            department=department,
            created_by=request.user,
            assigned_to=assigned_to,
            date=data['date'],
            due_time=data.get('due_time'),
            title=data['title'].strip(),
            description=(data.get('description') or '').strip(),
            task_type=data['task_type'],
            priority=data.get('priority', 'mid'),
            status=DepartmentTask.TaskStatus.CONFIRMED if assigned_to else DepartmentTask.TaskStatus.AWAITING_CONFIRMATION,
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
            if assigned_to_id:
                user_model = get_user_model()
                assigned_to = user_model.objects.filter(id=assigned_to_id, role='employee', is_active=True).first()
                if not assigned_to or not EmployeeProfile.objects.filter(user=assigned_to, department=department).exists():
                    raise ValidationError({'detail': 'Сотрудник не найден.'})
            else:
                assigned_to = None
        elif 'assigned_to' in data or task_type == 'department':
            assigned_to = None

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
        task.due_time = data.get('due_time', task.due_time)
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
        due_time = serializer.validated_data.get('due_time') or task.due_time

        task.date = new_date
        task.due_time = due_time
        task.save(update_fields=['date', 'due_time', 'updated_at'])
        response = Response(DepartmentTaskSerializer(task).data)
        return set_resource_version_header(response, task)


class EmployeeAvailabilityView(generics.GenericAPIView):
    permission_classes = [IsEmployeeRole]
    serializer_class = EmployeeAvailabilityUpdateSerializer

    @extend_schema(tags=['Employee'], summary='Доступность сотрудника (база + исключения)')
    def get(self, request):
        today = timezone.localdate()
        week_offset = _parse_week_offset(request.query_params.get('week'), default=0)
        week_start = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)
        week_end = week_start + timedelta(days=6)
        settings_obj = GlobalSettings.objects.first() or GlobalSettings.objects.create()
        ensure_employee_base_availability(request.user, settings_obj=settings_obj)

        base_rules = EmployeeBaseAvailability.objects.filter(user=request.user).order_by("weekday")
        week_overrides = EmployeeAvailabilityOverride.objects.filter(
            user=request.user,
            date__range=(week_start, week_end),
        ).order_by("date")
        planned_load = EmployeePlannedLoad.objects.filter(
            user=request.user,
            week_start=week_start,
        ).first()

        week_resolved = resolve_range_availability(
            request.user,
            week_start,
            week_end,
            settings_obj=settings_obj,
        )
        days = []
        for day_date in sorted(week_resolved.keys()):
            state = week_resolved[day_date]
            days.append(
                {
                    "date": day_date,
                    "is_available": bool(state.get("is_available")),
                    "start_time": state.get("start_time"),
                    "end_time": state.get("end_time"),
                    "mode": state.get("mode") or "off",
                    "source": state.get("source") or "base",
                    "override_type": state.get("override_type") or "",
                    "absence_type": state.get("absence_type") or "",
                }
            )

        return Response(
            {
                'week_start': week_start,
                'week_end': week_end,
                'week_offset': week_offset,
                'base': BaseAvailabilityRuleSerializer(base_rules, many=True).data,
                'overrides': AvailabilityOverrideSerializer(week_overrides, many=True).data,
                'planned_load': PlannedLoadSerializer(planned_load).data if planned_load else None,
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
        settings_obj = GlobalSettings.objects.first() or GlobalSettings.objects.create()
        ensure_employee_base_availability(request.user, settings_obj=settings_obj)
        changed_dates = set()
        base_was_updated = False

        base_payload = validated_data.get("base") or []
        overrides_payload = validated_data.get("overrides") or []
        clear_dates = validated_data.get("clear_override_dates") or []
        load_payload = validated_data.get("load_plan")

        if not (base_payload or overrides_payload or clear_dates or load_payload):
            raise ValidationError({"detail": "Нет данных для сохранения."})

        with transaction.atomic():
            for item in base_payload:
                mode = item["mode"]
                start_time = item.get("start_time")
                end_time = item.get("end_time")
                if mode == "fixed":
                    start_time = start_time or settings_obj.work_start
                    end_time = end_time or settings_obj.work_end
                    if start_time >= end_time:
                        raise ValidationError({"detail": "В базовой доступности время окончания должно быть позже начала."})
                else:
                    start_time = None
                    end_time = None
                EmployeeBaseAvailability.objects.update_or_create(
                    user=request.user,
                    weekday=item["weekday"],
                    defaults={
                        "mode": mode,
                        "start_time": start_time,
                        "end_time": end_time,
                    },
                )
                base_was_updated = True

            if clear_dates:
                EmployeeAvailabilityOverride.objects.filter(
                    user=request.user,
                    date__in=clear_dates,
                ).delete()
                changed_dates.update(clear_dates)

            for item in overrides_payload:
                override_type = item["override_type"]
                start_time = item.get("start_time")
                end_time = item.get("end_time")
                if override_type == "partial":
                    start_time = start_time or settings_obj.work_start
                    end_time = end_time or settings_obj.work_end
                    if start_time >= end_time:
                        raise ValidationError({"detail": "В исключении время окончания должно быть позже начала."})
                else:
                    start_time = None
                    end_time = None
                EmployeeAvailabilityOverride.objects.update_or_create(
                    user=request.user,
                    date=item["date"],
                    defaults={
                        "override_type": override_type,
                        "start_time": start_time,
                        "end_time": end_time,
                        "note": (item.get("note") or "")[:255],
                        "created_by": request.user,
                    },
                )
                changed_dates.add(item["date"])

            if load_payload:
                week_start = get_week_start(load_payload["week_start"])
                target_mode = load_payload["target_mode"]
                target_value = load_payload.get("target_value")
                if target_mode == "none":
                    EmployeePlannedLoad.objects.filter(
                        user=request.user,
                        week_start=week_start,
                    ).delete()
                else:
                    EmployeePlannedLoad.objects.update_or_create(
                        user=request.user,
                        week_start=week_start,
                        defaults={
                            "target_mode": target_mode,
                            "target_value": target_value,
                            "note": (load_payload.get("note") or "")[:255],
                        },
                    )

            conflict_queryset = DepartmentTask.objects.filter(
                assigned_to=request.user,
                status__in=[
                    DepartmentTask.TaskStatus.AWAITING_CONFIRMATION,
                    DepartmentTask.TaskStatus.CONFIRMED,
                    DepartmentTask.TaskStatus.IN_PROGRESS,
                ],
            )
            if changed_dates:
                conflict_queryset = conflict_queryset.filter(date__in=sorted(changed_dates))
            elif base_was_updated:
                conflict_queryset = conflict_queryset.filter(date__gte=timezone.localdate())
            else:
                conflict_queryset = conflict_queryset.none()

            conflicted_count = conflict_queryset.update(
                status=DepartmentTask.TaskStatus.CONFLICT,
                updated_at=timezone.now(),
            )

        updated_at = timezone.localtime(timezone.now())
        return Response(
            {
                "ok": True,
                "updated_at": updated_at.isoformat(),
                "conflicted_tasks": conflicted_count,
            }
        )


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

        queryset = (assigned_tasks | department_tasks).distinct().select_related('assigned_to', 'created_by')

        view_mode = (self.request.query_params.get('view') or 'active').strip().lower()
        if view_mode == 'archive':
            queryset = queryset.filter(status=DepartmentTask.TaskStatus.COMPLETED)
        else:
            queryset = queryset.exclude(status=DepartmentTask.TaskStatus.COMPLETED)

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

        if status_value != task.status:
            allowed_statuses = EMPLOYEE_STATUS_TRANSITIONS.get(task.status, set())
            if status_value not in allowed_statuses:
                raise ValidationError({'detail': 'Недопустимый переход статуса задачи.'})
            previous_status = task.status
            task.status = status_value
            task.save(update_fields=['status', 'updated_at'])
            record_status_change(
                task,
                from_status=previous_status,
                to_status=task.status,
                actor=request.user,
            )
            if task.status == DepartmentTask.TaskStatus.ON_REVIEW:
                notify_task_on_review(task, request.user)

        return Response(
            {
                'ok': True,
                'status': task.status,
                'status_label': TASK_STATUS_LABELS.get(task.status, task.get_status_display()),
                'status_tone': TASK_STATUS_TONES.get(task.status, 'muted'),
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

        if task.task_type != 'department' and task.status != DepartmentTask.TaskStatus.COMPLETED:
            previous_status = task.status
            task.status = DepartmentTask.TaskStatus.COMPLETED
            task.save(update_fields=['status', 'updated_at'])
            record_status_change(
                task,
                from_status=previous_status,
                to_status=task.status,
                actor=request.user,
                comment=comment,
            )
            notify_task_on_review(task, request.user)

        payload = {
            'ok': True,
            'status': task.status,
            'status_label': TASK_STATUS_LABELS.get(task.status, task.get_status_display()),
            'status_tone': TASK_STATUS_TONES.get(task.status, 'success'),
            'submissions': TaskSubmissionSerializer(created_submissions, many=True).data,
        }
        return Response(payload)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LEAVE_TYPE_LABELS = {'vacation': 'Отпуск', 'sick': 'Больничный'}
_LEAVE_STATUS_LABELS = {'pending': 'На рассмотрении', 'approved': 'Одобрено', 'rejected': 'Отклонено'}
_LEAVE_STATUS_TONES = {'pending': 'warning', 'approved': 'success', 'rejected': 'danger'}
_SPRINT_STATUS_LABELS = {'planning': 'Планирование', 'active': 'Активен', 'completed': 'Завершён'}
_SPRINT_STATUS_TONES = {'planning': 'neutral', 'active': 'success', 'completed': 'muted'}


# ---------------------------------------------------------------------------
# Serializers – Sprint
# ---------------------------------------------------------------------------

class SprintTaskSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    priority = serializers.CharField()
    priority_label = serializers.SerializerMethodField()
    status = serializers.CharField()
    status_label = serializers.SerializerMethodField()
    status_tone = serializers.SerializerMethodField()
    taken_by_id = serializers.IntegerField(allow_null=True)
    taken_name = serializers.SerializerMethodField()

    def get_priority_label(self, obj):
        return dict(DepartmentTask.PRIORITY_CHOICES).get(obj.priority, obj.priority)

    def get_status_label(self, obj):
        return TASK_STATUS_LABELS.get(obj.status, obj.get_status_display())

    def get_status_tone(self, obj):
        return TASK_STATUS_TONES.get(obj.status, 'muted')

    def get_taken_name(self, obj):
        return _format_user_name(obj.taken_by) if obj.taken_by_id else None


class SprintSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    goal = serializers.CharField(allow_blank=True)
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    status = serializers.CharField()
    status_label = serializers.SerializerMethodField()
    status_tone = serializers.SerializerMethodField()
    tasks_total = serializers.SerializerMethodField()
    tasks_done = serializers.SerializerMethodField()

    def get_status_label(self, obj):
        return _SPRINT_STATUS_LABELS.get(obj.status, obj.status)

    def get_status_tone(self, obj):
        return _SPRINT_STATUS_TONES.get(obj.status, 'neutral')

    def get_tasks_total(self, obj):
        return obj.tasks.count()

    def get_tasks_done(self, obj):
        return obj.tasks.filter(status=DepartmentTask.TaskStatus.COMPLETED).count()


class SprintCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200)
    goal = serializers.CharField(required=False, allow_blank=True)
    start_date = serializers.DateField()
    end_date = serializers.DateField()

    def validate(self, data):
        if data['end_date'] < data['start_date']:
            raise ValidationError({'end_date': 'Дата окончания не может быть раньше даты начала.'})
        return data


class SprintStatusUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=['planning', 'active', 'completed'])


# ---------------------------------------------------------------------------
# Serializers – Leave Requests
# ---------------------------------------------------------------------------

class LeaveRequestSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    request_type = serializers.CharField()
    type_label = serializers.SerializerMethodField()
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    comment = serializers.CharField()
    status = serializers.CharField()
    status_label = serializers.SerializerMethodField()
    status_tone = serializers.SerializerMethodField()
    rejection_reason = serializers.CharField()
    employee_name = serializers.SerializerMethodField()

    def get_type_label(self, obj):
        return _LEAVE_TYPE_LABELS.get(obj.request_type, obj.request_type)

    def get_status_label(self, obj):
        return _LEAVE_STATUS_LABELS.get(obj.status, obj.status)

    def get_status_tone(self, obj):
        return _LEAVE_STATUS_TONES.get(obj.status, 'neutral')

    def get_employee_name(self, obj):
        return _format_user_name(obj.user)


class LeaveRequestCreateSerializer(serializers.Serializer):
    request_type = serializers.ChoiceField(choices=['vacation', 'sick'])
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    comment = serializers.CharField(required=False, allow_blank=True, default='')

    def validate(self, data):
        if data['end_date'] < data['start_date']:
            raise ValidationError({'end_date': 'Дата окончания не может быть раньше даты начала.'})
        return data


class LeaveRequestReviewSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=['approve', 'reject'])
    rejection_reason = serializers.CharField(required=False, allow_blank=True, default='')

    def validate(self, data):
        if data['action'] == 'reject' and not data.get('rejection_reason', '').strip():
            pass  # reason is optional
        return data


# ---------------------------------------------------------------------------
# Serializers – Substitution
# ---------------------------------------------------------------------------

class SubstitutionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    absent_user_id = serializers.IntegerField()
    absent_name = serializers.SerializerMethodField()
    substitute_user_id = serializers.IntegerField()
    substitute_name = serializers.SerializerMethodField()
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    note = serializers.CharField()
    is_active = serializers.SerializerMethodField()

    def get_absent_name(self, obj):
        return _format_user_name(obj.absent_user)

    def get_substitute_name(self, obj):
        return _format_user_name(obj.substitute_user)

    def get_is_active(self, obj):
        today = date.today()
        return obj.start_date <= today <= obj.end_date


class SubstitutionCreateSerializer(serializers.Serializer):
    absent_user_id = serializers.IntegerField()
    substitute_user_id = serializers.IntegerField()
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    note = serializers.CharField(required=False, allow_blank=True, default='')

    def validate(self, data):
        if data['end_date'] < data['start_date']:
            raise ValidationError({'end_date': 'Дата окончания не может быть раньше даты начала.'})
        if data['absent_user_id'] == data['substitute_user_id']:
            raise ValidationError({'substitute_user_id': 'Замещающий не может совпадать с отсутствующим.'})
        return data


# ---------------------------------------------------------------------------
# Serializers – Team
# ---------------------------------------------------------------------------

class TeamMemberSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.SerializerMethodField()
    email = serializers.EmailField()
    is_active = serializers.BooleanField()
    active_tasks = serializers.IntegerField()
    absence_status = serializers.SerializerMethodField()

    def get_name(self, obj):
        return _format_user_name(obj)

    def get_absence_status(self, obj):
        today = date.today()
        absence = EmployeeAbsence.objects.filter(
            user=obj,
            start_date__lte=today,
            end_date__gte=today,
        ).first()
        if absence:
            return absence.reason or 'Отсутствует'
        return None


# ---------------------------------------------------------------------------
# Employee – Sprint views
# ---------------------------------------------------------------------------

class EmployeeCurrentSprintView(generics.GenericAPIView):
    permission_classes = [IsEmployeeRole]

    @extend_schema(
        tags=['Employee'],
        summary='Текущий активный спринт сотрудника',
        responses={200: dict},
    )
    def get(self, request):
        profile = get_object_or_404(EmployeeProfile, user=request.user)
        if not profile.department_id:
            return Response({'sprint': None, 'tasks': []})

        sprint = Sprint.objects.filter(
            department_id=profile.department_id,
            status='active',
        ).first()
        if not sprint:
            return Response({'sprint': None, 'tasks': []})

        tasks = list(sprint.tasks.select_related('taken_by').order_by('priority', 'title'))
        sprint_data = SprintSerializer(sprint).data
        sprint_data['tasks'] = SprintTaskSerializer(tasks, many=True).data
        sprint_data['tasks_mine'] = sum(1 for t in tasks if t.taken_by_id == request.user.id)
        sprint_data['tasks_free'] = sum(1 for t in tasks if not t.taken_by_id)
        return Response({'sprint': sprint_data})


class EmployeeTaskTakeView(generics.GenericAPIView):
    permission_classes = [IsEmployeeRole]

    @extend_schema(
        tags=['Employee'],
        summary='Взять задачу',
        responses={200: dict, 400: DetailMessageSerializer, 403: DetailMessageSerializer},
    )
    def post(self, request, task_id):
        task = get_object_or_404(DepartmentTask, id=task_id)
        require_employee_task_access(request.user, task)
        if task.taken_by_id:
            raise ValidationError({'detail': 'Задача уже взята другим сотрудником.'})
        previous_status = task.status
        task.taken_by = request.user
        task.status = DepartmentTask.TaskStatus.CONFIRMED
        task.save(update_fields=['taken_by', 'status', 'updated_at'])
        record_status_change(
            task,
            from_status=previous_status,
            to_status=task.status,
            actor=request.user,
            comment="Сотрудник взял задачу в работу",
        )
        notify_task_taken(task, request.user)
        return Response({'ok': True, 'status': task.status})


class EmployeeTaskDropView(generics.GenericAPIView):
    permission_classes = [IsEmployeeRole]

    @extend_schema(
        tags=['Employee'],
        summary='Отказаться от задачи',
        responses={200: dict, 400: DetailMessageSerializer, 403: DetailMessageSerializer},
    )
    def post(self, request, task_id):
        task = get_object_or_404(DepartmentTask, id=task_id)
        require_employee_task_access(request.user, task)
        if task.taken_by_id != request.user.id:
            raise ValidationError({'detail': 'Нельзя отказаться от чужой задачи.'})
        reason = (request.data.get('reason') or request.POST.get('reason') or '').strip()
        if not reason:
            raise ValidationError({'reason': 'Укажите причину отказа.'})
        previous_status = task.status
        task.taken_by = None
        task.status = DepartmentTask.TaskStatus.AWAITING_CONFIRMATION
        task.refusal_reason = reason
        task.save(update_fields=['taken_by', 'status', 'refusal_reason', 'updated_at'])
        record_status_change(
            task,
            from_status=previous_status,
            to_status=task.status,
            actor=request.user,
            comment=f"Отказ от задачи. Причина: {reason}",
        )
        notify_task_dropped(task, request.user, reason=reason)
        return Response({'ok': True, 'status': task.status, 'reason': reason})


# ---------------------------------------------------------------------------
# Employee – Task Chat views
# ---------------------------------------------------------------------------

_MSG_MONTH_NAMES = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def _fmt_msg_time(dt):
    local = timezone.localtime(dt)
    return f"{local.day} {_MSG_MONTH_NAMES[local.month - 1]}, {local:%H:%M}"


def _mark_task_messages_read(task, user):
    latest_message = (
        TaskMessage.objects.filter(task=task)
        .order_by('-id')
        .only('id')
        .first()
    )
    if not latest_message:
        return
    TaskMessageReadState.objects.update_or_create(
        task=task,
        user=user,
        defaults={'last_read_message': latest_message},
    )


class EmployeeTaskMessageListCreateView(generics.GenericAPIView):
    permission_classes = [IsEmployeeRole]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(tags=['Employee'], summary='Сообщения чата задачи')
    def get(self, request, task_id):
        task = get_object_or_404(DepartmentTask, id=task_id)
        require_employee_task_access(request.user, task)
        qs = TaskMessage.objects.filter(task=task).select_related(
            'author', 'reply_to', 'reply_to__author',
        )
        since = request.query_params.get('since')
        if since:
            try:
                qs = qs.filter(id__gt=int(since))
            except (ValueError, TypeError):
                pass
        messages = []
        for msg in qs:
            reply_data = None
            if msg.reply_to:
                ra = msg.reply_to.author
                reply_data = {
                    'id': msg.reply_to.id,
                    'author': ra.get_full_name().strip() or ra.username if ra else '—',
                    'text': msg.reply_to.text[:120],
                }
            a = msg.author
            messages.append({
                'id': msg.id,
                'author': a.get_full_name().strip() or a.username if a else '—',
                'author_id': a.id if a else None,
                'is_mine': a.id == request.user.id if a else False,
                'text': msg.text,
                'attachment_url': msg.attachment.url if msg.attachment else '',
                'attachment_name': Path(msg.attachment.name).name if msg.attachment else '',
                'reply_to': reply_data,
                'created_label': _fmt_msg_time(msg.created_at),
            })
        _mark_task_messages_read(task, request.user)
        return Response({'messages': messages})

    @extend_schema(tags=['Employee'], summary='Отправить сообщение в чат задачи')
    def post(self, request, task_id):
        task = get_object_or_404(DepartmentTask, id=task_id)
        require_employee_task_access(request.user, task)
        text = (request.data.get('text') or '').strip()
        attachment = request.FILES.get('attachment')
        if not text and not attachment:
            return Response({'detail': 'Введите сообщение или прикрепите файл.'}, status=400)
        reply_to = None
        reply_to_id = request.data.get('reply_to')
        if reply_to_id:
            try:
                reply_to = TaskMessage.objects.get(id=int(reply_to_id), task=task)
            except (TaskMessage.DoesNotExist, ValueError, TypeError):
                pass
        msg = TaskMessage.objects.create(
            task=task,
            author=request.user,
            text=text,
            attachment=attachment,
            reply_to=reply_to,
        )
        reply_data = None
        if reply_to:
            ra = reply_to.author
            reply_data = {
                'id': reply_to.id,
                'author': ra.get_full_name().strip() or ra.username if ra else '—',
                'text': reply_to.text[:120],
            }
        return Response({
            'message': {
                'id': msg.id,
                'author': request.user.get_full_name().strip() or request.user.username,
                'author_id': request.user.id,
                'is_mine': True,
                'text': msg.text,
                'attachment_url': msg.attachment.url if msg.attachment else '',
                'attachment_name': Path(msg.attachment.name).name if msg.attachment else '',
                'reply_to': reply_data,
                'created_label': _fmt_msg_time(msg.created_at),
            }
        }, status=201)


class ManagerTaskMessageListCreateView(generics.GenericAPIView):
    permission_classes = [IsManagerRole]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(tags=['Manager'], summary='Сообщения чата задачи')
    def get(self, request, task_id):
        task = get_manager_task_or_404(request.user, task_id)
        qs = TaskMessage.objects.filter(task=task).select_related(
            'author', 'reply_to', 'reply_to__author',
        )
        since = request.query_params.get('since')
        if since:
            try:
                qs = qs.filter(id__gt=int(since))
            except (ValueError, TypeError):
                pass
        messages = []
        for msg in qs:
            reply_data = None
            if msg.reply_to:
                ra = msg.reply_to.author
                reply_data = {
                    'id': msg.reply_to.id,
                    'author': ra.get_full_name().strip() or ra.username if ra else '—',
                    'text': msg.reply_to.text[:120],
                }
            a = msg.author
            messages.append({
                'id': msg.id,
                'author': a.get_full_name().strip() or a.username if a else '—',
                'author_id': a.id if a else None,
                'is_mine': a.id == request.user.id if a else False,
                'text': msg.text,
                'attachment_url': msg.attachment.url if msg.attachment else '',
                'attachment_name': Path(msg.attachment.name).name if msg.attachment else '',
                'reply_to': reply_data,
                'created_label': _fmt_msg_time(msg.created_at),
            })
        _mark_task_messages_read(task, request.user)
        return Response({'messages': messages})

    @extend_schema(tags=['Manager'], summary='Отправить сообщение в чат задачи')
    def post(self, request, task_id):
        task = get_manager_task_or_404(request.user, task_id)
        text = (request.data.get('text') or '').strip()
        attachment = request.FILES.get('attachment')
        if not text and not attachment:
            return Response({'detail': 'Введите сообщение или прикрепите файл.'}, status=400)
        reply_to = None
        reply_to_id = request.data.get('reply_to')
        if reply_to_id:
            try:
                reply_to = TaskMessage.objects.get(id=int(reply_to_id), task=task)
            except (TaskMessage.DoesNotExist, ValueError, TypeError):
                pass
        msg = TaskMessage.objects.create(
            task=task,
            author=request.user,
            text=text,
            attachment=attachment,
            reply_to=reply_to,
        )
        reply_data = None
        if reply_to:
            ra = reply_to.author
            reply_data = {
                'id': reply_to.id,
                'author': ra.get_full_name().strip() or ra.username if ra else '—',
                'text': reply_to.text[:120],
            }
        return Response({
            'message': {
                'id': msg.id,
                'author': request.user.get_full_name().strip() or request.user.username,
                'author_id': request.user.id,
                'is_mine': True,
                'text': msg.text,
                'attachment_url': msg.attachment.url if msg.attachment else '',
                'attachment_name': Path(msg.attachment.name).name if msg.attachment else '',
                'reply_to': reply_data,
                'created_label': _fmt_msg_time(msg.created_at),
            }
        }, status=201)


# ---------------------------------------------------------------------------
# Employee – Leave Request views
# ---------------------------------------------------------------------------

class EmployeeLeaveRequestListCreateView(BoundedListMixin, generics.GenericAPIView):
    permission_classes = [IsEmployeeRole]

    @extend_schema(
        tags=['Employee'],
        summary='Список заявок на отпуск/больничный',
        responses={200: LeaveRequestSerializer(many=True)},
    )
    def get(self, request):
        qs = LeaveRequest.objects.filter(user=request.user).select_related('user').order_by('-created_at')
        page = self.get_bounded_page(qs)
        return Response(LeaveRequestSerializer(page, many=True).data)

    @extend_schema(
        tags=['Employee'],
        summary='Создать заявку на отпуск/больничный',
        request=LeaveRequestCreateSerializer,
        responses={201: LeaveRequestSerializer, 400: DetailMessageSerializer},
    )
    def post(self, request):
        serializer = LeaveRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        lr = LeaveRequest.objects.create(
            user=request.user,
            request_type=d['request_type'],
            start_date=d['start_date'],
            end_date=d['end_date'],
            comment=d.get('comment', ''),
        )
        return Response(LeaveRequestSerializer(lr).data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Manager – Sprint views
# ---------------------------------------------------------------------------

class ManagerSprintListCreateView(BoundedListMixin, generics.GenericAPIView):
    permission_classes = [IsManagerRole]

    @extend_schema(
        tags=['Manager'],
        summary='Список спринтов отдела',
        responses={200: SprintSerializer(many=True)},
    )
    def get(self, request):
        department = require_manager_department(request)
        qs = Sprint.objects.filter(department=department).order_by('-start_date')
        page = self.get_bounded_page(qs)
        return Response(SprintSerializer(page, many=True).data)

    @extend_schema(
        tags=['Manager'],
        summary='Создать спринт',
        request=SprintCreateSerializer,
        responses={201: SprintSerializer, 400: DetailMessageSerializer},
    )
    def post(self, request):
        department = require_manager_department(request)
        serializer = SprintCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        sprint = Sprint.objects.create(
            department=department,
            title=d['title'],
            goal=d.get('goal', ''),
            start_date=d['start_date'],
            end_date=d['end_date'],
            created_by=request.user,
        )
        return Response(SprintSerializer(sprint).data, status=status.HTTP_201_CREATED)


class ManagerSprintDetailView(generics.GenericAPIView):
    permission_classes = [IsManagerRole]

    def _get_sprint(self, request, sprint_id):
        department = require_manager_department(request)
        return get_object_or_404(Sprint, id=sprint_id, department=department)

    @extend_schema(
        tags=['Manager'],
        summary='Детали спринта',
        responses={200: dict},
    )
    def get(self, request, sprint_id):
        sprint = self._get_sprint(request, sprint_id)
        tasks = list(sprint.tasks.select_related('taken_by').order_by('priority', 'title'))
        data = SprintSerializer(sprint).data
        data['tasks'] = SprintTaskSerializer(tasks, many=True).data
        return Response(data)

    @extend_schema(
        tags=['Manager'],
        summary='Изменить статус спринта',
        request=SprintStatusUpdateSerializer,
        responses={200: SprintSerializer, 400: DetailMessageSerializer},
    )
    def patch(self, request, sprint_id):
        sprint = self._get_sprint(request, sprint_id)
        serializer = SprintStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sprint.status = serializer.validated_data['status']
        sprint.save(update_fields=['status', 'updated_at'])
        return Response(SprintSerializer(sprint).data)


class ManagerSprintTaskAddView(generics.GenericAPIView):
    permission_classes = [IsManagerRole]

    @extend_schema(
        tags=['Manager'],
        summary='Добавить задачу в спринт',
        responses={200: dict, 400: DetailMessageSerializer},
    )
    def post(self, request, sprint_id):
        department = require_manager_department(request)
        sprint = get_object_or_404(Sprint, id=sprint_id, department=department)
        task_id = request.data.get('task_id')
        if not task_id:
            raise ValidationError({'task_id': 'Обязательное поле.'})
        task = get_object_or_404(DepartmentTask, id=task_id, department=department)
        if task.sprint_id and task.sprint_id != sprint.id:
            raise ValidationError({'task_id': 'Задача уже добавлена в другой спринт.'})
        task.sprint = sprint
        task.save(update_fields=['sprint'])
        return Response({'ok': True})


class ManagerSprintTaskRemoveView(generics.GenericAPIView):
    permission_classes = [IsManagerRole]

    @extend_schema(
        tags=['Manager'],
        summary='Убрать задачу из спринта',
        responses={200: dict, 400: DetailMessageSerializer},
    )
    def delete(self, request, sprint_id, task_id):
        department = require_manager_department(request)
        sprint = get_object_or_404(Sprint, id=sprint_id, department=department)
        task = get_object_or_404(DepartmentTask, id=task_id, sprint=sprint, department=department)
        task.sprint = None
        task.save(update_fields=['sprint'])
        return Response({'ok': True})


# ---------------------------------------------------------------------------
# Manager – Leave Request views
# ---------------------------------------------------------------------------

class ManagerLeaveRequestListView(BoundedListMixin, generics.GenericAPIView):
    permission_classes = [IsManagerRole]

    @extend_schema(
        tags=['Manager'],
        summary='Заявки сотрудников отдела',
        responses={200: LeaveRequestSerializer(many=True)},
    )
    def get(self, request):
        department = require_manager_department(request)
        status_filter = request.query_params.get('status', 'pending')
        User = get_user_model()
        dept_user_ids = EmployeeProfile.objects.filter(department=department).values_list('user_id', flat=True)
        qs = LeaveRequest.objects.filter(user_id__in=dept_user_ids).select_related('user')
        if status_filter != 'all':
            qs = qs.filter(status=status_filter)
        page = self.get_bounded_page(qs.order_by('-created_at'))
        return Response(LeaveRequestSerializer(page, many=True).data)


class ManagerLeaveRequestReviewView(generics.GenericAPIView):
    permission_classes = [IsManagerRole]

    @extend_schema(
        tags=['Manager'],
        summary='Одобрить или отклонить заявку',
        request=LeaveRequestReviewSerializer,
        responses={200: LeaveRequestSerializer, 400: DetailMessageSerializer},
    )
    def post(self, request, lr_id):
        department = require_manager_department(request)
        dept_user_ids = EmployeeProfile.objects.filter(department=department).values_list('user_id', flat=True)
        lr = get_object_or_404(LeaveRequest, id=lr_id, user_id__in=dept_user_ids)
        if lr.status != 'pending':
            raise ValidationError({'detail': 'Заявка уже рассмотрена.'})
        serializer = LeaveRequestReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        lr.status = 'approved' if d['action'] == 'approve' else 'rejected'
        lr.reviewed_by = request.user
        lr.reviewed_at = timezone.now()
        if d['action'] == 'reject':
            lr.rejection_reason = d.get('rejection_reason', '')
        lr.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'rejection_reason'])
        return Response(LeaveRequestSerializer(lr).data)


# ---------------------------------------------------------------------------
# Manager – Substitution views
# ---------------------------------------------------------------------------

class ManagerSubstitutionListCreateView(BoundedListMixin, generics.GenericAPIView):
    permission_classes = [IsManagerRole]

    @extend_schema(
        tags=['Manager'],
        summary='Замещения в отделе',
        responses={200: SubstitutionSerializer(many=True)},
    )
    def get(self, request):
        department = require_manager_department(request)
        dept_user_ids = EmployeeProfile.objects.filter(department=department).values_list('user_id', flat=True)
        qs = Substitution.objects.filter(
            absent_user_id__in=dept_user_ids,
        ).select_related('absent_user', 'substitute_user').order_by('-start_date')
        page = self.get_bounded_page(qs)
        return Response(SubstitutionSerializer(page, many=True).data)

    @extend_schema(
        tags=['Manager'],
        summary='Создать замещение',
        request=SubstitutionCreateSerializer,
        responses={201: SubstitutionSerializer, 400: DetailMessageSerializer},
    )
    def post(self, request):
        department = require_manager_department(request)
        serializer = SubstitutionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        dept_user_ids = set(
            EmployeeProfile.objects.filter(department=department).values_list('user_id', flat=True)
        )
        if d['absent_user_id'] not in dept_user_ids or d['substitute_user_id'] not in dept_user_ids:
            raise ValidationError({'detail': 'Оба сотрудника должны принадлежать вашему отделу.'})
        User = get_user_model()
        sub = Substitution.objects.create(
            absent_user_id=d['absent_user_id'],
            substitute_user_id=d['substitute_user_id'],
            start_date=d['start_date'],
            end_date=d['end_date'],
            note=d.get('note', ''),
            created_by=request.user,
        )
        sub = Substitution.objects.select_related('absent_user', 'substitute_user').get(id=sub.id)
        return Response(SubstitutionSerializer(sub).data, status=status.HTTP_201_CREATED)


class ManagerSubstitutionDeleteView(generics.GenericAPIView):
    permission_classes = [IsManagerRole]

    @extend_schema(
        tags=['Manager'],
        summary='Удалить замещение',
        responses={200: dict, 403: DetailMessageSerializer},
    )
    def delete(self, request, sub_id):
        department = require_manager_department(request)
        dept_user_ids = EmployeeProfile.objects.filter(department=department).values_list('user_id', flat=True)
        sub = get_object_or_404(Substitution, id=sub_id, absent_user_id__in=dept_user_ids)
        sub.delete()
        return Response({'ok': True})


# ---------------------------------------------------------------------------
# Manager – Team view
# ---------------------------------------------------------------------------

class ManagerTeamView(BoundedListMixin, generics.GenericAPIView):
    permission_classes = [IsManagerRole]

    @extend_schema(
        tags=['Manager'],
        summary='Список сотрудников отдела',
        responses={200: TeamMemberSerializer(many=True)},
    )
    def get(self, request):
        department = require_manager_department(request)
        User = get_user_model()
        today = date.today()
        profiles = EmployeeProfile.objects.filter(department=department).select_related('user')
        users = [p.user for p in profiles if p.user.is_active]

        active_task_counts = {}
        if users:
            active_statuses = [
                DepartmentTask.TaskStatus.AWAITING_CONFIRMATION,
                DepartmentTask.TaskStatus.CONFIRMED,
                DepartmentTask.TaskStatus.IN_PROGRESS,
            ]
            from django.db.models import Count
            counts = (
                DepartmentTask.objects.filter(
                    taken_by__in=users,
                    status__in=active_statuses,
                )
                .values('taken_by_id')
                .annotate(cnt=Count('id'))
            )
            active_task_counts = {row['taken_by_id']: row['cnt'] for row in counts}

        result = []
        for user in users:
            absence = EmployeeAbsence.objects.filter(
                user=user,
                start_date__lte=today,
                end_date__gte=today,
            ).first()
            result.append({
                'id': user.id,
                'name': _format_user_name(user),
                'email': user.email,
                'is_active': user.is_active,
                'active_tasks': active_task_counts.get(user.id, 0),
                'absence_status': absence.reason if absence else None,
            })

        return Response(result)
