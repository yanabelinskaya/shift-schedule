# Shift Schedule

Сервис планирования смен, задач и замещений сотрудников. Бэкенд на Django 5
+ DRF, PostgreSQL, серверные Django-шаблоны для веб-кабинетов сотрудника
и менеджера, мобильно-адаптивный UI без сборщика (vanilla JS/CSS).

---

## Содержание

- [Стек](#стек)
- [Структура проекта](#структура-проекта)
- [Быстрый старт через Docker](#быстрый-старт-через-docker)
- [Локальный запуск без Docker](#локальный-запуск-без-docker)
- [Переменные окружения](#переменные-окружения)
- [Команды и операции](#команды-и-операции)
- [API и веб-кабинеты](#api-и-веб-кабинеты)
- [Проверки качества](#проверки-качества)
- [CI](#ci)
- [Прод-чеклист](#прод-чеклист)

---

## Стек

- **Python 3.11**, Django 5.2, Django REST Framework 3.16, drf-spectacular
- **PostgreSQL 16** (можно подменить через `DB_ENGINE`)
- **Frontend**: серверный рендер Django + статические JS/CSS без сборки
- **Контейнеры**: Docker + docker compose

---

## Структура проекта

```
.
├── manage.py
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── docker/
│   └── entrypoint.sh           # ждёт БД, мигрирует, по флагу создаёт суперюзера
├── schedule_api/               # настройки Django, urls, wsgi
├── accounts/                   # модели, views, API, web-кабинеты
│   ├── api_urls.py             # /api/...
│   ├── workforce_api.py        # DRF-вьюхи менеджера/сотрудника
│   ├── web_views_manager.py    # серверные страницы кабинета менеджера
│   ├── web_views_shared.py     # общие страницы (login, профиль)
│   └── migrations/
├── templates/                  # HTML-шаблоны Django
│   └── dashboard/manager/      # экраны менеджера: спринты, задачи, замещения, чат
├── static/
│   ├── css/                    # app.css, manager.css, employee.css
│   └── js/                     # app.js, manager.js
├── media/                      # пользовательские загрузки (volume)
└── backups/                    # системные бэкапы (volume в проде)
```

---

## Быстрый старт через Docker

Это рекомендуемый способ — поднимает всё одной командой и держит
двустороннюю синхронизацию исходников: правки в редакторе сразу
подхватываются Django runserver внутри контейнера, а файлы, созданные
в контейнере (миграции, медиа), сохраняются на хосте.

### 1. Установите Docker Desktop / Docker Engine

[docs.docker.com/get-docker](https://docs.docker.com/get-docker/)

### 2. Подготовьте `.env`

```bash
cp .env.example .env
# при желании поменяйте порты / пароли / суперюзера
```

### 3. Поднимите стек

```bash
docker compose up --build
```

При первом запуске:
- собирается образ `web` из `Dockerfile`;
- стартует Postgres с volume `postgres_data`;
- entrypoint ждёт готовности БД, выполняет `migrate`;
- если `DJANGO_CREATE_SUPERUSER=1` — создаётся `admin / admin`
  (логин/пароль настраиваются переменными `DJANGO_SUPERUSER_*`);
- стартует `python manage.py runserver 0.0.0.0:8000`.

После старта откройте:
- Веб-кабинет: <http://localhost:8000/>
- Django admin: <http://localhost:8000/admin/>
- OpenAPI / Swagger: <http://localhost:8000/api/schema/swagger-ui/>

### 4. Что работает «само»

- **Hot reload кода.** `runserver` следит за `*.py`. Любое изменение
  внутри `accounts/`, `schedule_api/` и т. д. перезапускает сервер.
- **Шаблоны и статика** монтируются bind mount-ом, перезагрузка
  страницы — и правки видны.
- **Миграции, созданные внутри контейнера** через
  `docker compose exec web python manage.py makemigrations`,
  попадают сразу в `accounts/migrations/` на хосте.
- **БД и медиа сохраняются между перезапусками** через именованный
  volume `postgres_data` и каталог `./media`.

### 5. Полезные команды

```bash
# логи
docker compose logs -f web
docker compose logs -f db

# Django shell внутри контейнера
docker compose exec web python manage.py shell

# создать миграции
docker compose exec web python manage.py makemigrations

# применить миграции вручную (entrypoint делает это автоматически)
docker compose exec web python manage.py migrate

# собрать статику (если выкатываете в прод-режиме)
docker compose exec web python manage.py collectstatic --noinput

# открыть psql внутри контейнера БД
docker compose exec db psql -U postgres -d shift_schedule

# остановить, оставив данные
docker compose down

# остановить и удалить volumes (полный сброс)
docker compose down -v
```

### 6. Если нужно заглянуть в БД с хоста

В compose проброшен порт `${DB_PUBLIC_PORT:-5432}`. Подключайтесь
любым клиентом (psql, DBeaver, pgAdmin):

```
host:     localhost
port:     5432
database: shift_schedule
user:     postgres
password: postgres
```

---

## Локальный запуск без Docker

Если хочется запускать без контейнеров:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# в .env пропишите DB_HOST=localhost и параметры локального Postgres
# (или замените DB_ENGINE на django.db.backends.sqlite3 + DB_NAME=db.sqlite3)

python3 manage.py migrate
python3 manage.py createsuperuser
python3 manage.py runserver
```

---

## Переменные окружения

Все переменные опциональны: либо есть осмысленный дефолт, либо Django
упадёт в ImproperlyConfigured с понятным сообщением. Полный пример —
в [`.env.example`](.env.example).

### Django

| Переменная | Назначение | Дефолт |
| --- | --- | --- |
| `DJANGO_DEBUG` | Режим отладки | `False` |
| `DJANGO_SECRET_KEY` | Секрет (обязательно в проде) | dev-only при DEBUG |
| `DJANGO_ALLOWED_HOSTS` | CSV хостов | `127.0.0.1,localhost` при DEBUG |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | CSV origins для CSRF | — |
| `DJANGO_SECURE_SSL_REDIRECT` | Принудительный HTTPS | `not DEBUG` |
| `DJANGO_SESSION_COOKIE_SECURE` / `DJANGO_CSRF_COOKIE_SECURE` | Secure cookies | `not DEBUG` |
| `DJANGO_SECURE_HSTS_SECONDS` | HSTS | `31536000` в проде |

### База данных

| Переменная | Дефолт |
| --- | --- |
| `DB_ENGINE` | `django.db.backends.postgresql` |
| `DB_NAME` | `shift_schedule` |
| `DB_USER` | `postgres` |
| `DB_PASSWORD` | — |
| `DB_HOST` | `localhost` (внутри compose — `db`) |
| `DB_PORT` | `5432` |
| `DB_CONN_MAX_AGE` | `60` |
| `DB_CONN_HEALTH_CHECKS` | `1` |

### API / задачи

| Переменная | Назначение |
| --- | --- |
| `DRF_THROTTLE_ANON`, `DRF_THROTTLE_USER` | rate-limit DRF |
| `API_DEFAULT_PAGE_SIZE`, `API_MAX_PAGE_SIZE` | пагинация |
| `API_DEFAULT_LIST_LIMIT`, `API_MAX_LIST_LIMIT` | предел list-эндпоинтов |
| `TASK_SUBMISSION_MAX_FILE_SIZE` | лимит размера файла (байты) |
| `TASK_SUBMISSION_MAX_FILES` | сколько файлов за раз |
| `TASK_SUBMISSION_ALLOWED_EXTENSIONS` / `..._CONTENT_TYPES` | белый список |
| `BACKUP_STORAGE_DIR` | папка резервных копий |
| `BACKUP_RETENTION_DAYS` | срок хранения бэкапов |

### Email

`EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`,
`EMAIL_USE_SSL`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`,
`DEFAULT_FROM_EMAIL`.

### Docker compose (только для compose)

| Переменная | Дефолт | Что делает |
| --- | --- | --- |
| `WEB_PORT` | `8000` | внешний порт Django |
| `DB_PUBLIC_PORT` | `5432` | внешний порт PG |
| `DJANGO_CREATE_SUPERUSER` | `1` | создавать ли admin при старте |
| `DJANGO_SUPERUSER_USERNAME` | `admin` | логин |
| `DJANGO_SUPERUSER_EMAIL` | `admin@example.com` | email |
| `DJANGO_SUPERUSER_PASSWORD` | `admin` | пароль |

---

## Команды и операции

### Миграции

```bash
# создать
docker compose exec web python manage.py makemigrations
# применить
docker compose exec web python manage.py migrate
```

### Бэкапы

```bash
docker compose exec db pg_dump -U postgres shift_schedule > backups/dump.sql
```

### Сбросить БД полностью

```bash
docker compose down -v
docker compose up --build
```

---

## API и веб-кабинеты

- **Web** (Django templates, авторизация по сессии):
  - `/` — лендинг / редирект на кабинет
  - `/dashboard/manager/` — кабинет менеджера (спринты, задачи, замещения, команда, чат)
  - `/dashboard/employee/` — кабинет сотрудника
  - `/admin/` — Django admin
- **REST API** (`/api/...`, аутентификация по сессии или токену):
  - сотрудник: `GET/POST /api/employee/...`
  - менеджер: `GET/POST/PATCH /api/manager/...`
  - схема: `/api/schema/` (raw OpenAPI), `/api/schema/swagger-ui/`,
    `/api/schema/redoc/`

### Контракты

- ETag/`If-Match` оптимистичные блокировки на критичных update-эндпоинтах.
- Списки: без пагинации — лимит `API_DEFAULT_LIST_LIMIT`; с
  `page`/`page_size` — paginated-ответ.

---

## Проверки качества

```bash
docker compose exec web python manage.py check
docker compose exec web python manage.py check --deploy
docker compose exec web python manage.py spectacular --validate
docker compose exec web python manage.py test
```

Те же команды без `docker compose exec` работают и при локальном запуске.

---

## CI

GitHub Actions: `.github/workflows/ci.yml`. На каждый push/PR
выполняются три шага:

1. `python manage.py check`
2. `python manage.py spectacular --validate`
3. `python manage.py test`

Для CI используется sqlite и locmem email backend.

---

## Прод-чеклист

1. `DJANGO_DEBUG=0`, длинный случайный `DJANGO_SECRET_KEY`.
2. Заполнены `DJANGO_ALLOWED_HOSTS` и `DJANGO_CSRF_TRUSTED_ORIGINS`.
3. Включены HTTPS-флаги: redirect, secure cookies, HSTS.
4. `BACKUP_STORAGE_DIR` указывает на внешний volume вне репозитория.
5. Настроен SMTP, проверена отправка писем.
6. `collectstatic` отдан через nginx / S3, а не Django.
7. `python manage.py check --deploy` без замечаний.
8. `python manage.py spectacular --validate` зелёный.
9. `python manage.py test` зелёный.
10. `.env`/`backups/`/`media/` не уезжают в git и не публикуются как
    статика.
