# telegram_logger/health.py
import json
import logging
from telegram_logger.settings import settings
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timezone
from typing import Optional

# --- state ---
STARTED_AT = datetime.now(timezone.utc)
LAST_HOUSEKEEPING_AT: Optional[datetime] = None
LAST_ERROR_AT: Optional[datetime] = None
LAST_ERROR_MSG: Optional[str] = None

# --- config (через ENV можно переопределить) ---

HEALTH_PATH = settings.health_path
HEALTH_PORT = settings.health_port
ERROR_WINDOW_SECS = settings.health_error_window_secs
HOUSEKEEPING_STALE_SECS = settings.health_housekeeping_stale_secs

# --- hook на ошибки логгера ---
class _ErrorFlagHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        global LAST_ERROR_AT, LAST_ERROR_MSG
        if record.levelno >= logging.ERROR:
            LAST_ERROR_AT = datetime.now(timezone.utc)
            try:
                LAST_ERROR_MSG = record.getMessage()
            except Exception:
                LAST_ERROR_MSG = str(record)

# вызывать в housekeeping петле
def beat_housekeeping() -> None:
    global LAST_HOUSEKEEPING_AT
    LAST_HOUSEKEEPING_AT = datetime.now(timezone.utc)

def _is_healthy(now: datetime) -> bool:
    if LAST_ERROR_AT and (now - LAST_ERROR_AT).total_seconds() < ERROR_WINDOW_SECS:
        return False
    if LAST_HOUSEKEEPING_AT and (now - LAST_HOUSEKEEPING_AT).total_seconds() > HOUSEKEEPING_STALE_SECS:
        return False
    return True

def _payload() -> dict:
    now = datetime.now(timezone.utc)
    return {
        "status": "ok" if _is_healthy(now) else "error",
        "started_at": STARTED_AT.isoformat(),
        "last_housekeeping_at": LAST_HOUSEKEEPING_AT.isoformat() if LAST_HOUSEKEEPING_AT else None,
        "last_error_at": LAST_ERROR_AT.isoformat() if LAST_ERROR_AT else None,
        "last_error_msg": LAST_ERROR_MSG,
    }

class _HealthHandler(BaseHTTPRequestHandler):
    def _serve(self):
        body = json.dumps(_payload()).encode("utf-8")
        code = 200 if _is_healthy(datetime.now(timezone.utc)) else 503
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?", 1)[0].rstrip("/") == HEALTH_PATH.rstrip("/"):
            self._serve()
        else:
            self.send_error(404, "Not Found")

    def do_HEAD(self):
        if self.path.split("?", 1)[0].rstrip("/") == HEALTH_PATH.rstrip("/"):
            self._serve()
        else:
            self.send_error(404, "Not Found")

    def log_message(self, fmt, *args):
        logging.getLogger("health").debug("HTTP %s %s", self.command, self.path)

def _start_server() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", HEALTH_PORT), _HealthHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True, name="health-http")
    t.start()
    logging.getLogger("health").info("Health endpoint on 0.0.0.0:%s%s", HEALTH_PORT, HEALTH_PATH)

def setup_healthcheck() -> None:
    # цепляем флаггер ошибок и поднимаем HTTP сервер
    logging.getLogger().addHandler(_ErrorFlagHandler())
    _start_server()
