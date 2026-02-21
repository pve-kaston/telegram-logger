from datetime import datetime, timezone
from typing import Optional

LAST_HOUSEKEEPING_AT: Optional[datetime] = None


def beat_housekeeping() -> None:
    global LAST_HOUSEKEEPING_AT
    LAST_HOUSEKEEPING_AT = datetime.now(timezone.utc)
