# Telegram logger

`telegram-logger` — это сервис на **Telethon**, который автоматически сохраняет входящие/исходящие сообщения в SQLite, буферизует медиа, а при удалении или редактировании отправляет восстановленный контент в отдельный лог-чат. Проект ориентирован на запуск как процесс systemd или Docker контейнер.

Большое спасибо https://github.com/kawaiiDango/telegram-delete-logger

## Что делает сервис

Основные сценарии:

1. **Логирует сообщения** (текст + сериализованные метаданные медиа) в SQLite.
2. **Буферизует медиафайлы** в `media/` для восстановления удалённых и ограниченных (`noforwards`, self-destruct) сообщений.
3. **Отслеживает удаление сообщений**:
	- для текста отправляет текст в лог-чат,
	- для медиа пытается взять файл из буфера, при необходимости рефетчит сообщение, затем отправляет в лог-чат.
4. **Опционально сохраняет историю редактирования текста** (`before/after`).
5. **Опционально шифрует удалённые медиа** в `media_deleted/` через AES-256-GCM.
6. **Периодически чистит данные**:
	- старые записи БД по TTL,
	- устаревшие файлы буфера по TTL.
7. **Ручное сохранение restricted сообщений по ссылке**:
	- отправьте одну или несколько ссылок (через пробел) в лог-чат,
	- поддерживаются `https://t.me/...`, `https://t.me/c/...`, `tg://openmessage...`, `tg://privatepost...`.
8. **Поднимает HTTP health endpoint**

## Рекомендации по настройке и использованию

### Канал и чат комментариев — это разные сущности

В Telegram **канал** и его **чат комментариев (discussion group)** технически являются **двумя разными чатами с разными ID**. Частая ситуация: сам канал вам интересен, но в комментариях (чате) много спама от пользователей или ботов.

Если вы хотите отключить логирование спама, **добавляйте в `IGNORED_IDS` именно ID чата комментариев**, а не ID самого канала.

### Как узнать ID чата/канала

Самый простой способ — посмотреть ссылку на сообщение (если Telegram показывает формат `t.me/c/...`):

Пример:  
`https://t.me/c/1234567890/1234`

- `1234567890` — это **ID чата/канала** (внутренний идентификатор).
    
- `1234` — номер сообщения.
    

Если вместо числа в ссылке используется текст (username), например `https://t.me/some_channel/1234`, то это **username**, а не ID. Для преобразования username → numeric ID можно использовать бота `@username_to_id_bot`.

### Зачем использовать `IGNORED_IDS`

Если какие-то каналы/чаты вам не нужны или создают шум, добавьте их ID в `IGNORED_IDS` — тогда сообщения из них **не будут логироваться**, и база/буфер медиа не будут засоряться.

## Конфигурация (ENV)

Все параметры задаются через `.env` (или реальные env vars):
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
MAX_BUFFER_FILE_SIZE=104857600 #100 Mb
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

При первом старте Telethon создаст session файл в `${DATA_ROOT}/db/user.session`.

## Docker

### Запуск

```bash
docker run --rm -it \
  -v $(pwd)/data:/data \
  -e API_ID=1234567 \
  -e API_HASH="abcdefg12345678" \
  -e LOG_CHAT_ID=-100129391242 \
  ghcr.io/pve-kaston/telegram-logger:latest
```

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
