from enum import Enum


class ChatType(Enum):
    USER = 1
    CHANNEL = 2
    GROUP = 3
    BOT = 4
    UNKNOWN = 0
