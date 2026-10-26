import time
from email.utils import formatdate

import pytest

from whoop.errors import parse_retry_after


@pytest.mark.parametrize("value,expected", [(None, None), ("", None), ("5", 5.0), ("-3", 0.0), ("junk", None)])
def test_parse_retry_after_seconds(value, expected):
    assert parse_retry_after(value) == expected


def test_parse_retry_after_http_date_utc():
    # formatdate(usegmt=False) yields "-0000", which parses as naive datetime
    value = formatdate(time.time() + 30, usegmt=False)
    assert 25 <= parse_retry_after(value) <= 31
    value_gmt = formatdate(time.time() + 30, usegmt=True)
    assert 25 <= parse_retry_after(value_gmt) <= 31
