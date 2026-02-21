# telegram-logger

Refactored Telethon logger service with layered architecture:

- `telegram_logger/main.py` bootstrap + handler wiring
- `telegram_logger/settings.py` centralized env-based settings
- `telegram_logger/db/*` async repository + models
- `telegram_logger/storage/*` plaintext buffer + encrypted deleted media storage (`.enc` + `.sha256`)
- `telegram_logger/handlers/*` new/edited/deleted/restricted link handlers
- `telegram_logger/health/*` healthcheck + housekeeping beats

## Minimal env

```env
API_ID=12345
API_HASH=xxxxx
LOG_CHAT_ID=-100000000000
DELETED_MEDIA_KEY_B64=<32-byte-key-base64>
```

Run with:

```bash
python -m telegram_logger
```
