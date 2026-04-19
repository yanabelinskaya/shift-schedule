# Shift Schedule

## Быстрый старт
1. Создайте и активируйте виртуальное окружение:
   - `python3 -m venv .venv`
   - `source .venv/bin/activate`
2. Установите зависимости:
   - `pip install -r requirements.txt`
3. Создайте локальный файл окружения:
   - `cp .env.example .env`
   - заполните значения в `.env` (база данных, почта, security-параметры)
4. Примените миграции:
   - `python3 manage.py migrate`
5. Запустите сервер:
   - `python3 manage.py runserver`

## Переменные окружения
Настройки загружаются из `.env` (поддержан встроенный загрузчик; `python-dotenv` необязателен).

Основные переменные:
- `DJANGO_SECRET_KEY`
- `DJANGO_DEBUG`
- `DJANGO_ALLOWED_HOSTS`
- `DJANGO_CSRF_TRUSTED_ORIGINS`
- `DJANGO_SECURE_SSL_REDIRECT`
- `DJANGO_SESSION_COOKIE_SECURE`
- `DJANGO_CSRF_COOKIE_SECURE`
- `DJANGO_SECURE_HSTS_SECONDS`
- `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS`
- `DJANGO_SECURE_HSTS_PRELOAD`
- `DJANGO_SECURE_PROXY_SSL_HEADER` (если приложение за reverse-proxy / ingress)
- `DB_ENGINE`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`
- `DB_CONN_MAX_AGE`, `DB_CONN_HEALTH_CHECKS`
- `BACKUP_STORAGE_DIR` (путь к runtime-хранилищу бэкапов, лучше внешний volume)
- `BACKUP_RETENTION_DAYS` (сколько дней хранить бэкапы перед автоочисткой)
- `TASK_SUBMISSION_MAX_FILE_SIZE`, `TASK_SUBMISSION_MAX_FILES`
- `TASK_SUBMISSION_ALLOWED_EXTENSIONS`, `TASK_SUBMISSION_ALLOWED_CONTENT_TYPES`
- `DRF_THROTTLE_ANON`, `DRF_THROTTLE_USER`
- `API_DEFAULT_PAGE_SIZE`, `API_MAX_PAGE_SIZE` (опциональная пагинация через `page`/`page_size`)
- `API_DEFAULT_LIST_LIMIT`, `API_MAX_LIST_LIMIT` (ограничение размера списков без пагинации)
- `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_USE_SSL`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL`

## Структура проекта
- `manage.py` — точка входа Django
- `schedule_api/` — настройки проекта, URL-маршруты
- `accounts/` — бизнес-логика, API и web-слой
- `templates/` — HTML-шаблоны
- `static/` — JS/CSS/статические ресурсы
- `backups/` — локальные системные бэкапы

## Проверки качества
- Базовая проверка:
  - `python3 manage.py check`
- Проверка прод-настроек:
  - `python3 manage.py check --deploy`
- Валидация OpenAPI-схемы:
  - `python3 manage.py spectacular --validate`
- Тесты:
  - `python3 manage.py test`

## API-конкурентность и списки
- Критичные update-эндпоинты поддерживают optimistic locking через `If-Match` или поле `if_match` (ISO datetime).
- В ответах на ресурсы с `updated_at` возвращаются `ETag` и `X-Resource-Updated-At`.
- Для list-эндпоинтов:
  - без пагинации действует ограничение `API_DEFAULT_LIST_LIMIT` (и верхний предел `API_MAX_LIST_LIMIT`);
  - при передаче `page`/`page_size` включается paginated-ответ с полем `results`.

## CI (GitHub Actions)
- На каждый push и pull request запускается `.github/workflows/ci.yml` с тремя проверками:
  - `python manage.py check`
  - `python manage.py spectacular --validate`
  - `python manage.py test`
- Для CI используется отдельная sqlite-база и локальный email backend.

## Прод-чеклист
1. Установить `DJANGO_DEBUG=0`.
2. Задать длинный случайный `DJANGO_SECRET_KEY`.
3. Настроить `DJANGO_ALLOWED_HOSTS` и `DJANGO_CSRF_TRUSTED_ORIGINS`.
4. Включить HTTPS/security-флаги (redirect, secure cookies, HSTS).
5. Вынести `BACKUP_STORAGE_DIR` на внешний volume (например `/var/lib/shift-schedule/backups`), а не в каталог приложения.
6. Настроить SMTP и проверить отправку писем.
7. Прогнать `python3 manage.py check --deploy`.
8. Прогнать `python3 manage.py spectacular --validate`.
9. Прогнать `python3 manage.py test`.

## Важно
- `.env` не должен попадать в git.
- Пароли и SMTP-секреты храните только в окружении/секрет-хранилище.
- Для прод-деплоя используйте отдельные значения переменных, отличные от локальной разработки.
