from datetime import date, timedelta
import os
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import (
    Department,
    DepartmentTask,
    EmployeeShiftRequest,
    EmployeeProfile,
    GlobalSettings,
    GlobalSettingsChange,
    PasswordResetRequest,
    SystemBackup,
    TaskSubmission,
)
from accounts.system_utils import _cleanup_old_backups


class ApiFlowsTestCase(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            username='admin_user',
            password='test-pass-123',
            role='admin',
            email='admin@example.com',
        )
        self.manager = User.objects.create_user(
            username='manager_user',
            password='test-pass-123',
            role='manager',
            email='manager@example.com',
        )
        self.employee = User.objects.create_user(
            username='employee_user',
            password='test-pass-123',
            role='employee',
            email='employee@example.com',
        )

        self.department = Department.objects.create(name='Отдел тестов', manager=self.manager)
        EmployeeProfile.objects.create(user=self.manager, department=self.department, position='Менеджер')
        EmployeeProfile.objects.create(user=self.employee, department=self.department, position='Сотрудник')

    def test_admin_settings_api_permissions_and_history(self):
        update_url = reverse('api-admin-settings')
        history_url = reverse('api-admin-settings-history')

        self.client.force_authenticate(self.employee)
        response = self.client.post(
            update_url,
            {
                'platform_name': 'Shift Control',
                'support_email': 'help@example.com',
                'support_phone': '+7 999 000-00-00',
                'global_announcement': 'Технические работы с 22:00 до 23:00.',
                'allow_password_reset_requests': False,
                'backup_retention_days': 21,
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.admin)
        response = self.client.post(
            update_url,
            {
                'platform_name': 'Shift Control',
                'support_email': 'help@example.com',
                'support_phone': '+7 999 000-00-00',
                'global_announcement': 'Технические работы с 22:00 до 23:00.',
                'allow_password_reset_requests': False,
                'backup_retention_days': 21,
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data.get('ok'))

        settings_obj = GlobalSettings.objects.first()
        self.assertIsNotNone(settings_obj)
        self.assertEqual(settings_obj.platform_name, 'Shift Control')
        self.assertEqual(settings_obj.support_email, 'help@example.com')
        self.assertEqual(settings_obj.support_phone, '+7 999 000-00-00')
        self.assertEqual(settings_obj.global_announcement, 'Технические работы с 22:00 до 23:00.')
        self.assertFalse(settings_obj.allow_password_reset_requests)
        self.assertEqual(settings_obj.backup_retention_days, 21)
        self.assertEqual(GlobalSettingsChange.objects.filter(admin=self.admin).count(), 1)

        history_response = self.client.get(history_url)
        self.assertEqual(history_response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(history_response.data.get('history', [])), 1)

    def test_admin_system_backup_create_requires_admin(self):
        create_url = reverse('api-admin-system-backups-create')
        monitoring_url = reverse('api-admin-system-monitoring')

        backup = SystemBackup.objects.create(
            created_by=self.admin,
            file_name='backup_test.json',
            file_path='/tmp/backup_test.json',
            file_size=123,
            status='ready',
            source='manual',
        )

        self.client.force_authenticate(self.manager)
        denied_response = self.client.post(create_url, format='json')
        self.assertEqual(denied_response.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.admin)
        with patch('accounts.admin_api.create_backup', return_value=backup):
            create_response = self.client.post(create_url, format='json')
        self.assertEqual(create_response.status_code, status.HTTP_200_OK)
        self.assertEqual(create_response.data['backup']['id'], backup.id)
        self.assertIn(
            reverse('api-admin-system-backups-restore', args=[backup.id]),
            create_response.data['backup']['restore_url'],
        )

        monitoring_response = self.client.get(monitoring_url)
        self.assertEqual(monitoring_response.status_code, status.HTTP_200_OK)
        self.assertIn('online_users', monitoring_response.data)
        self.assertIn('errors_24h', monitoring_response.data)
        self.assertIn('updated_at', monitoring_response.data)

    def test_manager_can_delete_task_employee_cannot(self):
        task = DepartmentTask.objects.create(
            department=self.department,
            created_by=self.manager,
            assigned_to=self.employee,
            date=date.today(),
            title='Удаляемая задача',
            task_type='employee',
            status='awaiting_confirmation',
        )
        delete_url = reverse('api-manager-task-detail', args=[task.id])

        self.client.force_authenticate(self.employee)
        denied_response = self.client.delete(delete_url)
        self.assertEqual(denied_response.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.manager)
        ok_response = self.client.delete(delete_url)
        self.assertEqual(ok_response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(DepartmentTask.objects.filter(id=task.id).exists())

    def test_employee_cannot_move_task_status_backward(self):
        task = DepartmentTask.objects.create(
            department=self.department,
            created_by=self.manager,
            assigned_to=self.employee,
            date=date.today(),
            title='Задача по статусам',
            task_type='employee',
            status='in_progress',
        )
        status_url = reverse('api-employee-task-status', args=[task.id])

        self.client.force_authenticate(self.employee)
        backward_response = self.client.post(status_url, {'status': 'awaiting_confirmation'}, format='json')
        self.assertEqual(backward_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(backward_response.data.get('code'), 'validation_error')
        self.assertTrue(backward_response.data.get('trace_id'))
        self.assertIn('message', backward_response.data)

        forward_response = self.client.post(status_url, {'status': 'completed'}, format='json')
        self.assertEqual(forward_response.status_code, status.HTTP_200_OK)
        task.refresh_from_db()
        self.assertEqual(task.status, 'completed')

    def test_auth_login_logout_token_flow(self):
        login_url = reverse('api-login')
        logout_url = reverse('api-logout')
        me_url = reverse('api-me')

        login_response = self.client.post(
            login_url,
            {'username': self.employee.username, 'password': 'test-pass-123'},
            format='json',
        )
        self.assertEqual(login_response.status_code, status.HTTP_200_OK)
        token = login_response.data.get('token')
        self.assertTrue(token)
        self.assertTrue(Token.objects.filter(key=token, user=self.employee).exists())

        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token}')
        me_response = self.client.get(me_url)
        self.assertEqual(me_response.status_code, status.HTTP_200_OK)
        self.assertEqual(me_response.data.get('username'), self.employee.username)

        logout_response = self.client.post(logout_url, format='json')
        self.assertEqual(logout_response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Token.objects.filter(key=token).exists())

        after_logout_response = self.client.get(me_url)
        self.assertIn(after_logout_response.status_code, {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN})

    def test_role_permissions_isolated_between_admin_and_manager_endpoints(self):
        manager_tasks_url = reverse('api-manager-tasks')
        admin_settings_url = reverse('api-admin-settings')

        self.client.force_authenticate(self.admin)
        manager_endpoint_response = self.client.get(manager_tasks_url)
        self.assertEqual(manager_endpoint_response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(manager_endpoint_response.data.get('code'), 'forbidden')
        self.assertTrue(manager_endpoint_response.data.get('trace_id'))

        self.client.force_authenticate(self.manager)
        admin_endpoint_response = self.client.get(admin_settings_url)
        self.assertEqual(admin_endpoint_response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(admin_endpoint_response.data.get('code'), 'forbidden')
        self.assertTrue(admin_endpoint_response.data.get('trace_id'))

    def test_manager_task_update_rejects_stale_if_match(self):
        task = DepartmentTask.objects.create(
            department=self.department,
            created_by=self.manager,
            assigned_to=self.employee,
            date=date.today(),
            title='Конкурентная задача',
            task_type='employee',
            status='awaiting_confirmation',
        )
        stale_version = task.updated_at
        DepartmentTask.objects.filter(id=task.id).update(
            title='Изменено другим процессом',
            updated_at=timezone.now() + timedelta(seconds=2),
        )

        self.client.force_authenticate(self.manager)
        response = self.client.patch(
            reverse('api-manager-task-detail', args=[task.id]),
            {'title': 'Моё изменение', 'if_match': stale_version.isoformat()},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data.get('code'), 'conflict')
        task.refresh_from_db()
        self.assertEqual(task.title, 'Изменено другим процессом')

    def test_admin_settings_rejects_stale_if_match(self):
        self.client.force_authenticate(self.admin)
        settings_obj = GlobalSettings.objects.create(updated_by=self.admin)
        stale_version = settings_obj.updated_at
        GlobalSettings.objects.filter(id=settings_obj.id).update(
            weekly_hours_norm=39,
            updated_at=timezone.now() + timedelta(seconds=2),
        )

        response = self.client.post(
            reverse('api-admin-settings'),
            {'weekly_hours_norm': 36, 'if_match': stale_version.isoformat()},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data.get('code'), 'conflict')
        settings_obj.refresh_from_db()
        self.assertEqual(settings_obj.weekly_hours_norm, 39)

    def test_manager_cannot_decide_shift_request_outside_department(self):
        User = get_user_model()
        other_manager = User.objects.create_user(
            username='manager_other',
            password='test-pass-123',
            role='manager',
            email='manager.other@example.com',
        )
        other_employee = User.objects.create_user(
            username='employee_other',
            password='test-pass-123',
            role='employee',
            email='employee.other@example.com',
        )
        other_department = Department.objects.create(name='Другой отдел', manager=other_manager)
        EmployeeProfile.objects.create(user=other_manager, department=other_department, position='Менеджер')
        EmployeeProfile.objects.create(user=other_employee, department=other_department, position='Сотрудник')

        shift_request = EmployeeShiftRequest.objects.create(
            user=other_employee,
            date=date.today() + timedelta(days=3),
            request_type='replacement',
            reason='Тест чужого отдела',
        )

        self.client.force_authenticate(self.manager)
        response = self.client.post(
            reverse('api-manager-shift-requests-decision'),
            {'request_id': shift_request.id, 'decision': 'approved'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_list_limit_and_optional_pagination(self):
        for index in range(5):
            DepartmentTask.objects.create(
                department=self.department,
                created_by=self.manager,
                assigned_to=self.employee,
                date=date.today() + timedelta(days=index),
                title=f'Задача {index}',
                task_type='employee',
                status='awaiting_confirmation',
            )

        self.client.force_authenticate(self.employee)
        tasks_url = reverse('api-employee-tasks')
        with self.settings(API_DEFAULT_LIST_LIMIT=2, API_MAX_LIST_LIMIT=3):
            limited_response = self.client.get(tasks_url)
            self.assertEqual(limited_response.status_code, status.HTTP_200_OK)
            self.assertIsInstance(limited_response.data, list)
            self.assertEqual(len(limited_response.data), 2)

            max_limited_response = self.client.get(f'{tasks_url}?limit=100')
            self.assertEqual(max_limited_response.status_code, status.HTTP_200_OK)
            self.assertEqual(len(max_limited_response.data), 3)

            paged_response = self.client.get(f'{tasks_url}?page=1&page_size=2')
            self.assertEqual(paged_response.status_code, status.HTTP_200_OK)
            self.assertIn('results', paged_response.data)
            self.assertEqual(len(paged_response.data['results']), 2)
            self.assertGreaterEqual(paged_response.data.get('count', 0), 5)

    def test_employee_task_submission_file_upload(self):
        task = DepartmentTask.objects.create(
            department=self.department,
            created_by=self.manager,
            assigned_to=self.employee,
            date=date.today(),
            title='Файловая сдача',
            task_type='employee',
            status='awaiting_confirmation',
        )
        submit_url = reverse('api-employee-task-submission', args=[task.id])

        self.client.force_authenticate(self.employee)
        file_payload = SimpleUploadedFile('report.txt', b'final-report', content_type='text/plain')
        response = self.client.post(
            submit_url,
            data={'comment': 'Готово', 'attachments': file_payload},
            format='multipart',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(TaskSubmission.objects.filter(task=task).count(), 1)

        submission = TaskSubmission.objects.get(task=task)
        self.assertTrue(submission.attachment.name.endswith('.txt'))
        task.refresh_from_db()
        self.assertEqual(task.status, 'completed')

        self.assertEqual(len(response.data.get('submissions', [])), 1)
        self.assertTrue(response.data['submissions'][0]['attachment_name'].endswith('.txt'))

    def test_employee_task_submission_rejects_disallowed_extension(self):
        task = DepartmentTask.objects.create(
            department=self.department,
            created_by=self.manager,
            assigned_to=self.employee,
            date=date.today(),
            title='Запрещенный файл',
            task_type='employee',
            status='awaiting_confirmation',
        )
        submit_url = reverse('api-employee-task-submission', args=[task.id])

        self.client.force_authenticate(self.employee)
        bad_file = SimpleUploadedFile('malware.exe', b'evil', content_type='application/octet-stream')
        response = self.client.post(
            submit_url,
            data={'comment': 'Тест', 'attachments': bad_file},
            format='multipart',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data.get('code'), 'validation_error')
        self.assertIn('trace_id', response.data)
        self.assertFalse(TaskSubmission.objects.filter(task=task).exists())

    def test_employee_task_submission_respects_global_max_files(self):
        task = DepartmentTask.objects.create(
            department=self.department,
            created_by=self.manager,
            assigned_to=self.employee,
            date=date.today(),
            title='Лимит файлов',
            task_type='employee',
            status='awaiting_confirmation',
        )
        GlobalSettings.objects.create(
            task_submission_max_files=1,
            task_submission_max_file_size_mb=10,
        )
        submit_url = reverse('api-employee-task-submission', args=[task.id])

        self.client.force_authenticate(self.employee)
        first_file = SimpleUploadedFile('report-1.txt', b'first', content_type='text/plain')
        second_file = SimpleUploadedFile('report-2.txt', b'second', content_type='text/plain')
        response = self.client.post(
            submit_url,
            data={'comment': 'Пакет', 'attachments': [first_file, second_file]},
            format='multipart',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data.get('code'), 'validation_error')
        self.assertFalse(TaskSubmission.objects.filter(task=task).exists())

    def test_password_reset_request_respects_global_toggle(self):
        GlobalSettings.objects.create(allow_password_reset_requests=False)
        reset_url = reverse('api-password-reset-request')

        response = self.client.post(
            reset_url,
            {'email': self.employee.email},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(PasswordResetRequest.objects.filter(email=self.employee.email).exists())

    def test_admin_backup_restore_edge_cases(self):
        self.client.force_authenticate(self.admin)

        missing_backup = SystemBackup.objects.create(
            created_by=self.admin,
            file_name='missing_backup.json',
            file_path='/tmp/missing_backup_for_restore.json',
            file_size=10,
            status='ready',
            source='manual',
        )
        missing_restore_response = self.client.post(
            reverse('api-admin-system-backups-restore', args=[missing_backup.id]),
            format='json',
        )
        self.assertEqual(missing_restore_response.status_code, status.HTTP_404_NOT_FOUND)

        with tempfile.NamedTemporaryFile(delete=False, suffix='.json') as tmp:
            tmp.write(b'[]')
            temp_path = tmp.name

        try:
            broken_backup = SystemBackup.objects.create(
                created_by=self.admin,
                file_name='broken_backup.json',
                file_path=temp_path,
                file_size=2,
                status='ready',
                source='manual',
            )
            with patch('accounts.admin_api.call_command', side_effect=Exception('boom')):
                broken_restore_response = self.client.post(
                    reverse('api-admin-system-backups-restore', args=[broken_backup.id]),
                    format='json',
                )
            self.assertEqual(broken_restore_response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
            broken_backup.refresh_from_db()
            self.assertEqual(broken_backup.status, 'ready')
            self.assertIsNone(broken_backup.restored_by)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_backup_retention_cleanup_removes_old_backups(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix='.json') as tmp:
            tmp.write(b'[]')
            old_backup_path = tmp.name

        try:
            old_backup = SystemBackup.objects.create(
                created_by=self.admin,
                file_name='old_backup.json',
                file_path=old_backup_path,
                file_size=2,
                status='ready',
                source='manual',
            )
            SystemBackup.objects.filter(id=old_backup.id).update(
                created_at=timezone.now() - timedelta(days=45)
            )

            with self.settings(BACKUP_RETENTION_DAYS=30):
                deleted_count = _cleanup_old_backups()

            self.assertEqual(deleted_count, 1)
            self.assertFalse(SystemBackup.objects.filter(id=old_backup.id).exists())
            self.assertFalse(os.path.exists(old_backup_path))
        finally:
            if os.path.exists(old_backup_path):
                os.remove(old_backup_path)
