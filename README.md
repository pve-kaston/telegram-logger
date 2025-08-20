# Logger Service

Сервис для логирования удалённых медиа и сообщений из Telegram личных чатов и групп (в будущем и каналов).

## Установка

Клонируем репозиторий устанавиваем зависимости

`pip install -r requirements.txt`

Выполняем команду

`make install`

Команда создаст пользователя, права и скопирует файлы.

---

## Настройка

Редактируем файл окружения по примеру:

`sudo nano /etc/logger/.env`

### Пример `.env`

```
# Telegram credentials
API_ID=12345678
API_HASH="ljgewlrgwkfnewigwud234gwef"
SESSION_NAME="db/user.session"

# Chat where all deleted messages will be dumped
LOG_CHAT_ID=-1234567890
IGNORED_IDS=[]

# Message sending settings
LISTEN_OUTGOING_MESSAGES=FALSE
SAVE_EDITED_MESSAGES=True
DELETE_SENT_GIFS_FROM_SAVED=True
DELETE_SENT_STICKERS_FROM_SAVED=True

# Limits
MAX_IN_MEMORY_FILE_SIZE=5242880

# SQLite config
SQLITE_DB_FILE="db/messages.db"
PERSIST_TIME_IN_DAYS_BOT=1
PERSIST_TIME_IN_DAYS_USER=1
PERSIST_TIME_IN_DAYS_CHANNEL=1
PERSIST_TIME_IN_DAYS_GROUP=1

# Debug / Rate limit
DEBUG_MODE=0
RATE_LIMIT_NUM_MESSAGES=5
```

---

## Подготовка `user.session`

1. Вручную запустите Python скрипт и пройдите авторизацию в телеграмм аккаунт.
2. Переместите созданный `user.session` файл в директорию `/opt/logger/db/user.session`

  `sudo cp src/db/user.session /opt/logger/db/user.session`

---

## Запуск

Выполните команду для запуска сервиса

`make start`

А так же для проверки состояния сервиса

`make status`