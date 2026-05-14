# Shift Schedule

Сервис планирования смен, задач и замещений сотрудников.
Django 5 + DRF + PostgreSQL, серверный рендер шаблонов, vanilla JS/CSS.

## Демо

<https://shift-schedule-5z7x.onrender.com/>

| Роль | Логин | Пароль |
| --- | --- | --- |
| Администратор | `admin` | `Admin123!@` |
| Менеджер | `maria_samoylova` | `6gxy3IeZ794` |
| Сотрудник | `belikbelka2007` | `HokO-UNysdc` |

## Запуск через Docker

```bash
cp .env.example .env
docker compose up --build
```

Откройте <http://localhost:8000/>. Логин: `admin` / `admin`.
Подробнее — в [docker.md](docker.md).

## Запуск без Docker

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python3 manage.py migrate
python3 manage.py createsuperuser
python3 manage.py runserver
```

## Эндпоинты

- `/` — кабинеты сотрудника / менеджера
- `/admin/` — Django admin
- `/api/...` — REST API (сессия или токен)
- `/api/schema/swagger-ui/` — Swagger UI

## Стек

Python 3.11, Django 5.2, DRF 3.16, drf-spectacular, PostgreSQL 16, Docker.

## Переменные окружения

Шаблон — в [`.env.example`](.env.example). Основные:

| Переменная | Дефолт | Назначение |
| --- | --- | --- |
| `DJANGO_DEBUG` | `False` | режим отладки |
| `DJANGO_SECRET_KEY` | — | секрет (обязателен в проде) |
| `DJANGO_ALLOWED_HOSTS` | `127.0.0.1,localhost` | разрешённые хосты |
| `DB_NAME` / `DB_USER` / `DB_PASSWORD` | — | подключение к PostgreSQL |
| `DB_HOST` | `localhost` (в compose — `db`) | хост БД |

## Проверки

```bash
python manage.py check
python manage.py check --deploy
python manage.py spectacular --validate
python manage.py test
```

В Docker — через `docker compose exec web ...`.
CI: `.github/workflows/ci.yml` (check + spectacular + test на каждый push/PR).

## Прод-чеклист

- `DJANGO_DEBUG=0`, длинный `DJANGO_SECRET_KEY`
- заполнены `DJANGO_ALLOWED_HOSTS` и `DJANGO_CSRF_TRUSTED_ORIGINS`
- HTTPS: redirect, secure cookies, HSTS
- статика отдаётся через nginx / S3
- `manage.py check --deploy` без замечаний
- `.env`, `backups/`, `media/` не в git
