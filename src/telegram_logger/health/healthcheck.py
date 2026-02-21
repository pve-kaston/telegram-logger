import json
import logging
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from telegram_logger.health.beats import LAST_HOUSEKEEPING_AT
from telegram_logger.settings import settings

STARTED_AT = datetime.now(timezone.utc)
LAST_ERROR_AT: Optional[datetime] = None
LAST_ERROR_MSG: Optional[str] = None


class _ErrorFlagHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        global LAST_ERROR_AT, LAST_ERROR_MSG
        if record.levelno >= logging.ERROR:
            LAST_ERROR_AT = datetime.now(timezone.utc)
            LAST_ERROR_MSG = record.getMessage()


def _is_healthy(now: datetime) -> bool:
    if LAST_ERROR_AT and (now - LAST_ERROR_AT).total_seconds() < settings.health_error_window_secs:
        return False
    if LAST_HOUSEKEEPING_AT and (
        now - LAST_HOUSEKEEPING_AT
    ).total_seconds() > settings.health_housekeeping_stale_secs:
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
    def log_message(self, *_) -> None:
        pass
    
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
        if self.path.split("?", 1)[0].rstrip("/") == settings.health_path.rstrip("/"):
            self._serve()
        else:
            self.send_error(404, "Not Found")

    def do_HEAD(self):
        self.do_GET()


def setup_healthcheck() -> None:
    logging.getLogger().addHandler(_ErrorFlagHandler())
    server = ThreadingHTTPServer(("0.0.0.0", settings.health_port), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="health-http")
    thread.start()
