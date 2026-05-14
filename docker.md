# Docker

Запуск проекта в Docker одной командой.

## Что нужно

- Docker Desktop или Docker Engine + Docker Compose v2
- Свободный порт 8000

## Запуск

```bash
cp .env.example .env
docker compose up --build
```

Откройте <http://localhost:8000/>. Логин: `admin` / `admin`.

При первом старте автоматически:
- поднимается PostgreSQL 16,
- применяются миграции,
- загружаются демо-данные,
- создаётся суперпользователь.

## Полезные команды

```bash
docker compose up -d            # запустить в фоне
docker compose logs -f web      # смотреть логи
docker compose down             # остановить (данные сохраняются)
docker compose down -v          # остановить и удалить БД (полный сброс)
```

Команды внутри контейнера:

```bash
docker compose exec web python manage.py makemigrations
docker compose exec web python manage.py migrate
docker compose exec web python manage.py shell
docker compose exec web python manage.py test
docker compose exec db psql -U postgres -d shift_schedule
```

## Основные переменные `.env`

| Переменная | Дефолт | Что делает |
| --- | --- | --- |
| `WEB_PORT` | `8000` | порт Django на хосте |
| `DB_PASSWORD` | `postgres` | пароль БД |
| `DJANGO_CREATE_SUPERUSER` | `1` | создавать ли admin |
| `DJANGO_SUPERUSER_PASSWORD` | `admin` | пароль admin |
| `DJANGO_LOAD_SEED_DATA` | `1` | грузить ли демо-данные |

Полный список — в [README.md](README.md#переменные-окружения).

## Если что-то не работает

- **Порт 8000 занят** — поменяйте `WEB_PORT` в `.env`.
- **БД в странном состоянии** — `docker compose down -v && docker compose up`.
- **Поменяли `requirements.txt`** — пересоберите: `docker compose build web`.

См. также: [Dockerfile](Dockerfile), [docker-compose.yml](docker-compose.yml).
