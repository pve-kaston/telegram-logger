# telegram-logger

`telegram-logger` — это асинхронный сервис на **Telethon**, который автоматически сохраняет входящие/исходящие сообщения в SQLite, буферизует медиа, а при удалении или редактировании отправляет восстановленный контент в отдельный лог-чат. Проект ориентирован на запуск как «долго живущий» процесс (Docker/systemd) и включает health-check endpoint для мониторинга.

---

## Что делает сервис

Основные сценарии:

1. **Логирует сообщения** (текст + сериализованные метаданные медиа) в SQLite.
2. **Буферизует медиафайлы** в `media/` для возможности восстановления при удалении.
3. **Отслеживает удаления**:
   - для текстовых сообщений отправляет текст в лог-чат;
   - для медиа — пытается достать файл из буфера, при необходимости рефетчит сообщение, затем отправляет в лог-чат.
4. **Опционально шифрует удалённые медиа** (AES-256-GCM) при переносе в `media_deleted/`.
5. **Опционально отслеживает редактирование текста** и отправляет diff «до/после» в лог-чат.
6. **Периодически чистит старые данные**:
   - записи в БД по TTL для разных типов чатов;
   - файлы буфера по TTL.
7. **Поднимает HTTP health endpoint** (`/health`), который сигнализирует о последних ошибках и «свежести» housekeeping.

---

## Архитектура

```text
Telegram events
   │
   ├─ handlers/new_message.py
   │    └─ MessageRepository.save_message(...) + PlaintextBufferStorage.buffer_save(...)
   │
   ├─ handlers/edited_deleted.py
   │    ├─ MessageRepository.get_messages_by_event(...)
   │    ├─ PlaintextBufferStorage / EncryptedDeletedStorage
   │    └─ send_message/send_file в LOG_CHAT_ID
   │
   └─ handlers/restricted_saver.py
        └─ обработка ссылок на restricted forward сообщения

main.py
   ├─ wiring Telethon handlers
   ├─ setup_healthcheck()
   └─ housekeeping_loop() -> delete_expired_messages + purge_buffer_ttl
```

Слои:

- `settings.py` — конфигурация через `pydantic-settings`.
- `database/*` — SQLAlchemy async модель, сессия, методы и репозиторий.
- `storage/*` — plaintext-буфер и encrypted-хранилище удалённых медиа.
- `handlers/*` — бизнес-логика Telegram-событий.
- `health/*` — `/health`, флаги ошибок, heartbeat housekeeping.

---

## Структура репозитория

```text
.
├── src/
│   ├── requirements.txt
│   ├── pyproject.toml
│   └── telegram_logger/
│       ├── __main__.py                # entrypoint: инициализация директорий, запуск клиента
│       ├── main.py                    # wiring обработчиков и housekeeping loop
│       ├── settings.py                # env-конфигурация
│       ├── tg_types.py                # enum типов чатов
│       ├── encryption.py              # legacy pyAesCrypt контекст-менеджеры (сейчас не в основном флоу)
│       ├── database/
│       │   ├── models.py              # ORM модель messages + engine/session
│       │   ├── methods.py             # CRUD/queries/TTL-cleanup
│       │   └── repository.py          # thin repository-обёртка
│       ├── storage/
│       │   ├── plaintext.py           # буфер медиа в filesytem
│       │   ├── encrypted_deleted.py   # AES-GCM шифрование удалённых медиа
│       │   └── base.py                # protocol/типы для storage
│       ├── handlers/
│       │   ├── new_message.py         # обработка новых/изменённых сообщений и запись в БД
│       │   ├── edited_deleted.py      # обработка edited/deleted/read-self-destruct
│       │   └── restricted_saver.py    # сохранение сообщений по ссылкам
│       └── health/
│           ├── beats.py               # heartbeat housekeeping
│           └── healthcheck.py         # HTTP endpoint
├── scripts/
│   └── decrypt_deleted_media.py       # утилита расшифровки .enc
├── Dockerfile
├── telegram-logger.service
└── .github/workflows/ci.yaml
```

---

## Потоки данных (подробно)

### 1) Новое/изменённое сообщение

`new_message_handler`:

- проверяет «restricted link» сценарий в лог-чате (если сообщение отправлено самим владельцем аккаунта);
- вычисляет `chat_id`, `from_id`, тип чата (`USER/GROUP/CHANNEL/BOT/UNKNOWN`);
- уважает `ignored_ids`;
- при `noforwards` / self-destruct media / `buffer_all_media=true` сохраняет медиа в буфер;
- если запись в БД ещё не существует, сохраняет:
  - ids,
  - текст,
  - `pickle.dumps(media)` (если есть),
  - флаги `noforwards`, `self_destructing`,
  - timestamps.

### 2) Удаление сообщения

`edited_deleted_handler` для `MessageDeleted` и `UpdateReadMessagesContents`:

- достаёт из БД кандидатов по id события;
- фильтрует `ignored_ids`;
- для `UpdateReadMessagesContents` обрабатывает только self-destructing;
- если это медиа:
  - ищет файл в буфере;
  - если не найден, пытается рефетчить сообщение и снова буферизовать;
  - формирует caption с markdown mention автора/чата;
  - если включено шифрование, кладёт в `media_deleted/*.enc`, затем на лету расшифровывает во временный файл и отправляет в лог-чат;
  - если шифрование отключено, копирует в `media_deleted/` как plaintext и отправляет файл.
- если это текст — отправляет текст в лог-чат.

### 3) Редактирование сообщения

При `save_edited_messages=true`:

- сравнивает старый текст из БД и новый текст из события;
- если есть изменение, отправляет в лог-чат «Before/After».

### 4) Housekeeping

Фоновый цикл в `main.housekeeping_loop` каждые 300 секунд:

- обновляет heartbeat;
- удаляет истёкшие записи из БД по TTL-политике;
- чистит устаревшие файлы буфера.

---

## Конфигурация (ENV)

Все параметры задаются через `.env` (или реальные env vars):

### Обязательные

```env
API_ID=12345
API_HASH=xxxxxxxxxxxxxxxx
LOG_CHAT_ID=-1001234567890
```

### Рекомендуемые для продакшена

```env
# Корень данных
DATA_ROOT=/data

# Буферизация
BUFFER_ALL_MEDIA=false
MAX_BUFFER_FILE_SIZE=209715200
MEDIA_BUFFER_TTL_HOURS=24

# Шифрование удалённых медиа (AES-256-GCM, base64 от 32 байт)
ENCRYPT_DELETED_MEDIA=true
DELETED_MEDIA_KEY_B64=<base64_32_bytes_key>

# Ограничение количества удалённых сообщений за событие
MAX_DELETED_MESSAGES_PER_EVENT=5

# Логика edited
SAVE_EDITED_MESSAGES=false

# Игнорируемые id (set[int])
# Пример формата зависит от pydantic parsing окружения,
# обычно удобно задавать JSON-подобно: [123, -100999]
IGNORED_IDS=[]

# Политики хранения по типам чатов (в днях)
PERSIST_TIME_IN_DAYS_USER=1
PERSIST_TIME_IN_DAYS_GROUP=1
PERSIST_TIME_IN_DAYS_CHANNEL=1
PERSIST_TIME_IN_DAYS_BOT=1

# Healthcheck
HEALTH_PATH=/health
HEALTH_PORT=8080
HEALTH_ERROR_WINDOW_SECS=300
HEALTH_HOUSEKEEPING_STALE_SECS=900

# Отладка
DEBUG_MODE=false
```

### Сгенерировать ключ для `DELETED_MEDIA_KEY_B64`

```bash
python - <<'PY'
import base64, os
print(base64.b64encode(os.urandom(32)).decode())
PY
```

---

## Локальный запуск

### 1) Установка зависимостей

```bash
cd src
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2) Подготовка окружения

Создайте `.env` рядом с `src/telegram_logger` entrypoint (или экспортируйте env vars).

### 3) Запуск

```bash
cd src
python -m telegram_logger
```

При первом старте Telethon создаст session файл в `${DATA_ROOT}/db/user.session`.

---

## Docker

В репозитории уже есть multi-stage `Dockerfile`.

### Сборка

```bash
docker build -t telegram-logger:local .
```

### Запуск

```bash
docker run --rm -it \
  -v $(pwd)/data:/data \
  --env-file .env \
  -p 8080:8080 \
  telegram-logger:local
```

---

## systemd

Шаблон unit-файла: `telegram-logger.service`.

Типовой flow:

1. Скопировать проект в `/opt/telegram_logger`.
2. Подготовить `/etc/telegram_logger/.env`.
3. Создать пользователя `telegram_logger`.
4. Установить unit:

```bash
sudo cp telegram-logger.service /etc/systemd/system/telegram-logger.service
sudo systemctl daemon-reload
sudo systemctl enable --now telegram-logger
```

---

## Healthcheck и мониторинг

Endpoint: `GET /health` на `HEALTH_PORT`.

Ответ содержит:

- `status`: `ok` или `error`;
- `started_at`;
- `last_housekeeping_at`;
- `last_error_at`;
- `last_error_msg`.

Код ответа:

- `200` — healthy;
- `503` — ошибка в окне `HEALTH_ERROR_WINDOW_SECS` или stale housekeeping.

---

## CI/CD

GitHub Actions workflow (`.github/workflows/ci.yaml`):

- триггеры: `push` в `master`, `pull_request`, `workflow_dispatch`;
- buildx сборка образа;
- push в GHCR (кроме PR);
- автогенерация тегов (`vX.Y.Z`, `latest`, `sha-*` и т.д.).

---

## Наблюдения по коду (технический аудит)

Ниже важные детали, которые полезно знать перед эксплуатацией/доработкой:

1. `MessageRepository(sqlite_url)` принимает URL, но фактический engine создаётся глобально в `database/models.py` из `settings` во время импорта.
2. В `main.py` зарегистрированы обработчики так, что `new_message_handler` вызывается и на `MessageEdited`, и отдельно `edited_deleted_handler` тоже на `MessageEdited`.
3. В `scripts/decrypt_deleted_media.py` есть проверка `args.force`, но аргумент `--force` в parser не объявлен (скрипт потребует доработки, если нужен force overwrite).
4. В `scripts/decrypt_deleted_media.py` текст help для ключа и фактическое имя env var отличаются (`DELETED_MEDIA_KEY_B64` vs `TELEGRAM_DELETED_MEDIA_KEY_B64`).
5. Модуль `encryption.py` использует `settings.file_password`, которого нет в `Settings`; и сам модуль не задействован в основном runtime-пути.

---

## Рекомендации по развитию

1. Добавить тесты:
   - unit-тесты парсинга ссылок в `restricted_saver`;
   - unit-тесты удаления/редактирования (mock Telethon client);
   - интеграционный smoke-тест SQLite repository.
2. Унифицировать конфигурацию ключа расшифровки и поправить CLI скрипт `decrypt_deleted_media.py` (`--force`, имя env var).
3. Добавить migration/версионирование схемы БД (например, Alembic).
4. Развести обработку `MessageEdited` между storage-логикой и edit-diff, чтобы избежать двойной нагрузки.
5. Добавить метрики (Prometheus) рядом с health endpoint.

---

## Лицензия

В репозитории сейчас нет явного LICENSE файла. Если проект планируется к публичному использованию, рекомендуется добавить лицензию (MIT/Apache-2.0 и т.д.).
