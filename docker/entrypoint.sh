#!/usr/bin/env bash
# Дожидаемся БД, выполняем миграции и собираем статику (если PRODUCTION).
# Затем запускаем переданную команду (по умолчанию runserver).
set -euo pipefail

DB_HOST="${DB_HOST:-db}"
DB_PORT="${DB_PORT:-5432}"

echo "[entrypoint] Ждём базу данных ${DB_HOST}:${DB_PORT}..."
ATTEMPTS=0
until python - <<PY 2>/dev/null
import os, socket
s = socket.socket()
s.settimeout(2)
s.connect((os.environ.get("DB_HOST", "db"), int(os.environ.get("DB_PORT", "5432"))))
s.close()
PY
do
  ATTEMPTS=$((ATTEMPTS + 1))
  if [ "$ATTEMPTS" -ge 60 ]; then
    echo "[entrypoint] База так и не поднялась за 60 попыток. Выходим."
    exit 1
  fi
  sleep 1
done
echo "[entrypoint] База доступна."

echo "[entrypoint] Применяем миграции..."
python manage.py migrate --noinput

if [ "${DJANGO_LOAD_SEED_DATA:-0}" = "1" ]; then
  SEED_FIXTURE="${DJANGO_SEED_FIXTURE:-render_data.json}"
  if [ -f "$SEED_FIXTURE" ]; then
    if [ "${DJANGO_FORCE_LOAD_SEED_DATA:-0}" = "1" ]; then
      SHOULD_LOAD_SEED="yes"
    else
      SHOULD_LOAD_SEED="$(python manage.py shell <<'PY' | tail -n 1
from django.contrib.auth import get_user_model
User = get_user_model()
print("yes" if User.objects.count() <= 1 else "no")
PY
)"
    fi
    if [ "$SHOULD_LOAD_SEED" = "yes" ]; then
      echo "[entrypoint] Загружаем демо-данные из ${SEED_FIXTURE}..."
      python manage.py loaddata "$SEED_FIXTURE"
    else
      echo "[entrypoint] Демо-данные не загружаются: пользователи уже есть."
    fi
  else
    echo "[entrypoint] Файл демо-данных ${SEED_FIXTURE} не найден, пропускаем."
  fi
fi

if [ "${DJANGO_COLLECTSTATIC:-0}" = "1" ]; then
  echo "[entrypoint] Собираем статику..."
  python manage.py collectstatic --noinput
fi

if [ "${DJANGO_CREATE_SUPERUSER:-0}" = "1" ]; then
  echo "[entrypoint] Создаём суперпользователя (если ещё нет)..."
  python manage.py shell <<'PY'
import os
from django.contrib.auth import get_user_model
User = get_user_model()
username = os.environ.get("DJANGO_SUPERUSER_USERNAME", "admin")
email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "admin@example.com")
password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "admin")
if not User.objects.filter(username=username).exists():
    User.objects.create_superuser(username=username, email=email, password=password)
    print(f"[entrypoint] superuser '{username}' создан")
else:
    print(f"[entrypoint] superuser '{username}' уже существует")
PY
fi

exec "$@"
