# Telegram logger

`telegram-logger` — сервис на **Telethon**, который автоматически сохраняет входящие/исходящие сообщения в SQLite, буферизует медиа и при удалении/редактировании отправляет восстановленный контент в отдельный лог-чат. Проект рассчитан на запуск как **systemd service** или как **Docker-контейнер**.

> Огромное спасибо [kawaiiDango](https://github.com/kawaiiDango) и его проекту на основе которого был построен данный сервис: https://github.com/kawaiiDango/telegram-delete-logger

## Что делает сервис

Основные сценарии:

1. **Логирует сообщения** (текст + сериализованные метаданные медиа) в SQLite.
2. **Буферизует медиафайлы** в `media/` для восстановления:
	   - удалённых сообщений,
	   - сообщений с ограничениями (`noforwards`, self-destruct).
3. **Отслеживает удаление сообщений**:
	   - для текста отправляет восстановленный текст в лог-чат;
	   - для медиа пытается взять файл из буфера, при необходимости повторно получает сообщение и отправляет файл в лог-чат.
4. **Опционально сохраняет историю редактирования текста** (формат `before/after`).
5. **Опционально шифрует удалённые медиа** в `media_deleted/` (AES-256-GCM).
6. **Периодически чистит данные**:
	   - старые записи в БД по TTL (раздельно для типов чатов),
	   - устаревшие файлы буфера по TTL.
7. **Ручное сохранение restricted сообщений по ссылке**:
	   - отправьте одну или несколько ссылок (разделённых пробелом) в лог-чат;
	   - поддерживаются ссылки форматов:  
	     `https://t.me/...`, `https://t.me/c/...`, `tg://openmessage...`, `tg://privatepost...`.
8. **Поднимает HTTP health endpoint** (по умолчанию `/health`) для мониторинга состояния.

## Рекомендации по настройке и использованию

### Канал и чат комментариев — разные сущности

В Telegram **канал** и его **чат комментариев (discussion group)** — это **два разных чата с разными ID**. Частая ситуация: канал полезный, а комментарии (и боты в них) генерируют много шума/спама.

Если вы хотите отключить логирование спама, добавляйте в `IGNORED_IDS` **ID именно чата комментариев**, а не ID самого канала.

### Как узнать ID чата/канала

Самый простой способ — посмотреть ссылку на сообщение (если Telegram показывает формат `t.me/c/...`).

Пример:

`https://t.me/c/1234567890/1234`

- `1234567890` — **ID чата/канала** (внутренний идентификатор),
- `1234` — номер сообщения.

Если вместо числа используется `username`, например `https://t.me/some_channel/1234`, то это **username**, а не ID. Для преобразования `username → numeric ID` можно использовать бота `@username_to_id_bot`.

### Зачем использовать `IGNORED_IDS`

Если какие-то каналы/чаты вам не нужны или создают шум, добавьте их ID в `IGNORED_IDS`. Тогда сообщения из них **не будут логироваться**, а база и буфер медиа не будут засоряться.

## Получение `API_ID` и `API_HASH` (Telegram)

Сервис использует **Telegram API (MTProto)** через Telethon — для этого нужны `API_ID` и `API_HASH`.

1. Откройте: `https://my.telegram.org`
2. Войдите по номеру телефона (Telegram отправит код).
3. Перейдите в **API development tools**.
4. Создайте приложение (*Create new application*):
	   - **App title**: любое (например `telegram-logger`)
	   - **Short name**: любое (например `tglogger`)
	   - Остальные поля можно заполнить произвольно.
5. После создания появятся значения:
	   - **App api_id** → `API_ID`
	   - **App api_hash** → `API_HASH`

Добавьте их в `.env`:

```env
API_ID=123456
API_HASH="your_api_hash_here"
````

## Получение `user.session` (Telethon session)

`user.session` — файл авторизации Telethon. Он создаётся **при первом успешном входе** (код из Telegram / пароль 2FA, если включён). После этого сервис может работать без повторной авторизации, пока сессия валидна.

### Вариант A — автоматически через Docker (рекомендуется)

При первом запуске контейнер запросит код подтверждения и создаст session-файл в примонтированном `/data`.

```bash
docker run --rm -it \
  -v $(pwd)/data:/data \
  -e API_ID=123456 \
  -e API_HASH="your_api_hash_here" \
  -e LOG_CHAT_ID=-1001234567890 \
  ghcr.io/pve-kaston/telegram-logger:latest
```

После успешного логина файл появится на хосте:

- `./data/db/user.session`

Важно:
- запускайте **с `-it`**, чтобы можно было ввести код/пароль 2FA;
- обязательно монтируйте `-v $(pwd)/data:/data`, иначе session останется внутри контейнера и исчезнет после удаления контейнера.

### Вариант B — вручную (локально, только генерация session)

Вы можете создать `user.session` заранее скриптом из `scripts/`:

```bash
cd scripts
pip install telethon
export API_ID=123456
export API_HASH="your_api_hash_here"
export SESSION_FILE="../src/telegram_logger/data/db/user.session"   # опционально
python generate_session.py
```

Если `SESSION_FILE` не задан, session создастся в текущей директории.

### Если `user.session` уже есть

Если у вас уже имеется готовый файл `user.session`, просто поместите его в:
- Docker: `/data/db/user.session` (на хосте обычно `./data/db/user.session` при `-v $(pwd)/data:/data`)

После этого при следующих запусках повторная авторизация (код и 2FA) **не потребуется**.

## Конфигурация (ENV)

Все параметры задаются через `.env` (или реальные env vars).
### Обязательные

```env
API_ID=123456
API_HASH="your_telegram_api_hash_here"
LOG_CHAT_ID=-1001234567890
```
### Рекомендуемые

```env
IGNORED_IDS=[-1002222222222222222222, -10033333333333333333333]
LISTEN_OUTGOING_MESSAGES=true

# DATA_ROOT controls where sessions/db/media are stored.
# Usually you DON'T need to set it.
# Docker default: /data
# Non-docker default: <working_directory>/src/data
# DATA_ROOT=/custom/path

BUFFER_ALL_MEDIA=true
MAX_BUFFER_FILE_SIZE=104857600 # 100 MB
MEDIA_BUFFER_TTL_HOURS=24

ENCRYPT_DELETED_MEDIA=false
DELETED_MEDIA_KEY_B64="base64_32_bytes_key"

MAX_DELETED_MESSAGES_PER_EVENT=100

SAVE_EDITED_MESSAGES=true
DELETE_SENT_GIFS_FROM_SAVED=true
DELETE_SENT_STICKERS_FROM_SAVED=true

PERSIST_TIME_IN_DAYS_BOT=7
PERSIST_TIME_IN_DAYS_USER=7
PERSIST_TIME_IN_DAYS_CHANNEL=7
PERSIST_TIME_IN_DAYS_GROUP=7

HEALTH_PATH=/health
HEALTH_PORT=8080
HEALTH_ERROR_WINDOW_SECS=120
HEALTH_HOUSEKEEPING_STALE_SECS=600

DEBUG_MODE=false
```

### Сгенерировать ключ для `DELETED_MEDIA_KEY_B64`
```bash
python - <<'PY'
import base64, os
print(base64.b64encode(os.urandom(32)).decode())
PY
```

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
python -m telegram_logger
```

При первом старте Telethon создаст session-файл в `${DATA_ROOT}/db/user.session`.

## Docker

### Запуск через `docker run`

```bash
docker run -it \
  -v $(pwd)/data:/data \
  -e API_ID=123456 \
  -e API_HASH="your_api_hash_here" \
  -e LOG_CHAT_ID=-1001234567890 \
  ghcr.io/pve-kaston/telegram-logger:latest
```

### Запуск через Docker Compose

1. Создайте файл `.env` с обязательными переменными (`API_ID`, `API_HASH`, `LOG_CHAT_ID`).
2. Так же создайте директорию `data/db/` и положите туда файл `user.session`
3. Запустите:

```bash
docker compose up
```

## systemd

Шаблон unit-файла: `telegram-logger.service`.
Типовой flow:

1. Скопировать проект в `/opt/telegram_logger`.
2. Положить. в `/opt/telegram_logger/src/data/db/` файл user.session.
3. Подготовить `/etc/telegram_logger/.env`.
4. Создать пользователя `telegram_logger` и изменить права на созданные файлы.
5. Установить unit:

```bash
sudo cp telegram-logger.service /etc/systemd/system/telegram-logger.service
sudo systemctl daemon-reload
sudo systemctl enable --now telegram-logger
```


## Расшифровка удалённых медиа (если включено шифрование)

Если вы включили `ENCRYPT_DELETED_MEDIA=true`, удалённые медиа сохраняются в зашифрованном виде.  
Для расшифровки используйте скрипт из `scripts/`.

Пример:

```bash
export TELEGRAM_DELETED_MEDIA_KEY_B64="YOUR_BASE64_KEY_HERE"

python3 scripts/decrypt_deleted_media.py \
  --enc ./data/media_deleted \
  --out ~/telegram-logger-decrypted
```

> `TELEGRAM_DELETED_MEDIA_KEY_B64` должен совпадать с ключом `DELETED_MEDIA_KEY_B64`, который использовался при шифровании.
