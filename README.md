# Telegram logger

`telegram-logger` is a service built on **Telethon** that automatically stores incoming/outgoing messages in SQLite, buffers media, and upon deletion/editing sends restored content to a separate log chat. The project is designed to run as a **systemd service** or as a **Docker container**.

> Huge thanks to [kawaiiDango](https://github.com/kawaiiDango) and their project on which this service is based: [https://github.com/kawaiiDango/telegram-delete-logger](https://github.com/kawaiiDango/telegram-delete-logger)

## What the service does

Main scenarios:

1. **Logs messages** (text + serialized media metadata) to SQLite.
2. **Buffers media files** in `media/` for recovery of:

   * deleted messages,
   * restricted messages (`noforwards`, self-destruct).
3. **Tracks message deletions**:

   * for text — sends restored text to the log chat;
   * for media — attempts to retrieve the file from the buffer, optionally re-fetches the message if needed, and sends the file to the log chat.
4. **Optionally saves text edit history** (format `before/after`).
5. **Optionally encrypts deleted media** in `media_deleted/` (AES-256-GCM).
6. **Periodically cleans up data**:

   * old DB records by TTL (separately per chat type),
   * outdated buffer files by TTL.
7. **Manual saving of restricted messages via link**:

   * send one or multiple links (space-separated) to the log chat;
   * supported link formats:
     `https://t.me/...`, `https://t.me/c/...`, `tg://openmessage...`, `tg://privatepost...`.
8. **Exposes an HTTP health endpoint** (default `/health`) for monitoring.

---

## Configuration and usage recommendations

### Channel and discussion chat are different entities

In Telegram, a **channel** and its **discussion group (comments chat)** are **two separate chats with different IDs**. A common case: the channel itself is useful, but comments (and bots inside them) generate a lot of noise/spam.

If you want to disable logging of spam, add the **ID of the discussion chat**, not the channel ID, to `IGNORED_IDS`.

### How to find a chat/channel ID

The easiest way is to look at a message link (if Telegram shows the `t.me/c/...` format).

Example:

```
https://t.me/c/1234567890/1234
```

* `1234567890` — **chat/channel ID** (internal identifier),
* `1234` — message number.

If a `username` is used instead, e.g. `https://t.me/some_channel/1234`, that is a **username**, not an ID. To convert `username → numeric ID`, you can use the bot `@username_to_id_bot`.

### Why use `IGNORED_IDS`

If certain channels/chats are not needed or create noise, add their IDs to `IGNORED_IDS`. Messages from them will **not be logged**, and your database and media buffer will not be cluttered.

---

## Obtaining `API_ID` and `API_HASH` (Telegram)

The service uses the **Telegram API (MTProto)** via Telethon — for this you need `API_ID` and `API_HASH`.

1. Open: `https://my.telegram.org`
2. Log in using your phone number (Telegram will send a code).
3. Go to **API development tools**.
4. Create an application (*Create new application*):

   * **App title**: any (e.g. `telegram-logger`)
   * **Short name**: any (e.g. `tglogger`)
   * Other fields can be filled arbitrarily.
5. After creation you will see:

   * **App api_id** → `API_ID`
   * **App api_hash** → `API_HASH`

Add them to `.env`:

```env
API_ID=123456
API_HASH="your_api_hash_here"
```

---

## Obtaining `user.session` (Telethon session)

`user.session` is the Telethon authorization file. It is created **on the first successful login** (Telegram code / 2FA password if enabled). After that, the service can run without re-authentication as long as the session remains valid.

### Option A — automatically via Docker (recommended)

On first container run, it will prompt for the confirmation code and create the session file in the mounted `/data`.

```bash
docker run --rm -it \
  -v $(pwd)/data:/data \
  -e API_ID=123456 \
  -e API_HASH="your_api_hash_here" \
  -e LOG_CHAT_ID=-1001234567890 \
  ghcr.io/pve-kaston/telegram-logger:latest
```

After successful login, the file will appear on the host:

* `./data/db/user.session`

Important:

* run with **`-it`** so you can enter the code/2FA password;
* обязательно mount `-v $(pwd)/data:/data`, otherwise the session will remain inside the container and disappear after it is removed.

### Option B — manually (local, session generation only)

You can generate `user.session` in advance using the script from `scripts/`:

```bash
cd scripts
pip install telethon
export API_ID=123456
export API_HASH="your_api_hash_here"
export SESSION_FILE="../src/telegram_logger/data/db/user.session"   # optional
python generate_session.py
```

If `SESSION_FILE` is not set, the session will be created in the current directory.

### If `user.session` already exists

If you already have a ready `user.session` file, simply place it in:

* Docker: `/data/db/user.session` (on host usually `./data/db/user.session` when using `-v $(pwd)/data:/data`)

After that, on subsequent runs, re-authentication (code and 2FA) will **not be required**.

---

## Configuration (ENV)

All parameters are set via `.env` (or actual environment variables).

### Required

```env
API_ID=123456
API_HASH="your_telegram_api_hash_here"
LOG_CHAT_ID=-1001234567890
```

### Recommended

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

### Generate a key for `DELETED_MEDIA_KEY_B64`

```bash
python - <<'PY'
import base64, os
print(base64.b64encode(os.urandom(32)).decode())
PY
```

---

## Local run

### 1) Install dependencies

```bash
cd src
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2) Prepare environment

Create `.env` next to the `src/telegram_logger` entrypoint (or export env vars).

### 3) Run

```bash
python -m telegram_logger
```

On first start, Telethon will create the session file in `${DATA_ROOT}/db/user.session`.

---

## Docker

### Run via `docker run`

```bash
docker run -it \
  -v $(pwd)/data:/data \
  -e API_ID=123456 \
  -e API_HASH="your_api_hash_here" \
  -e LOG_CHAT_ID=-1001234567890 \
  ghcr.io/pve-kaston/telegram-logger:latest
```

### Run via Docker Compose

1. Create a `.env` file with required variables (`API_ID`, `API_HASH`, `LOG_CHAT_ID`).
2. Also create the directory `data/db/` and place `user.session` there.
3. Run:

```bash
docker compose up
```

---

## systemd

Unit file template: `telegram-logger.service`.

Typical flow:

1. Copy the project to `/opt/telegram_logger`.
2. Place `user.session` in `/opt/telegram_logger/src/data/db/`.
3. Prepare `/etc/telegram_logger/.env`.
4. Create user `telegram_logger` and adjust permissions.
5. Install the unit:

```bash
sudo cp telegram-logger.service /etc/systemd/system/telegram-logger.service
sudo systemctl daemon-reload
sudo systemctl enable --now telegram-logger
```

---

## Decrypting deleted media (if encryption is enabled)

If `ENCRYPT_DELETED_MEDIA=true`, deleted media is stored encrypted.
Use the script from `scripts/` to decrypt.

Example:

```bash
export TELEGRAM_DELETED_MEDIA_KEY_B64="YOUR_BASE64_KEY_HERE"

python3 scripts/decrypt_deleted_media.py \
  --enc ./data/media_deleted \
  --out ~/telegram-logger-decrypted
```

> `TELEGRAM_DELETED_MEDIA_KEY_B64` must match the `DELETED_MEDIA_KEY_B64` used during encryption.