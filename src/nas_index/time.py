from datetime import datetime
from zoneinfo import ZoneInfo


BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def now_beijing() -> datetime:
    return datetime.now(BEIJING_TZ)


def from_timestamp_beijing(timestamp: int | float) -> datetime:
    return datetime.fromtimestamp(timestamp, BEIJING_TZ)


def format_beijing(value: datetime | None) -> str:
    if value is None:
        return "—"
    return value.strftime("%Y-%m-%d %H:%M:%S 北京时间")
