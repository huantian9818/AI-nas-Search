from datetime import datetime

from nas_index.time import BEIJING_TZ
from nas_index.time import from_timestamp_beijing
from nas_index.time import now_beijing


def test_now_beijing_uses_asia_shanghai_timezone():
    current = now_beijing()

    assert current.tzinfo is BEIJING_TZ
    assert current.utcoffset().total_seconds() == 8 * 60 * 60


def test_from_timestamp_beijing_converts_unix_timestamp():
    value = from_timestamp_beijing(0)

    assert value == datetime(1970, 1, 1, 8, 0, tzinfo=BEIJING_TZ)
