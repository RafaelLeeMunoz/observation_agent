"""R1 dates: strict parsing that never repairs a bad date.

Python's helpers repair bad input in three ways, and this module blocks all of them:
  * `email.utils.mktime_tz` rolls "32 Feb 2001 25:61" forward to 5 March. We never call it;
    building a `datetime` from the parts raises on impossible values instead.
  * `email.utils.parsedate_tz` treats any year below 100 as two-digit, so "26 Nov 0001"
    becomes 2001. We take the year exactly as written in the header.
  * `parsedate_tz` reports a missing or unknown zone as offset 0, the same as UTC. We require
    a written zone (+hhmm/-hhmm or a standard name) and otherwise return `no_timezone`.
A weekday name that contradicts the date makes the date invalid, and a missing offset is
`no_timezone`, never assumed to be UTC.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_tz

from .textutil import norm_ws

WINDOW = (1999, 2002)

OK = "ok"
MISSING = "missing"
INVALID = "invalid"
OUT_OF_RANGE = "out_of_range"
NO_TIMEZONE = "no_timezone"

SHIFTED = "shifted"
MATCHES = "matches"
UNTESTABLE = "untestable"

DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# "Mon, 4 Feb 2002 13:47:02 -0800": optional weekday, day, month name, year as written.
_HEAD = re.compile(r"^\s*(?:([A-Za-z]+),\s*)?(\d{1,2})\s+([A-Za-z]+)\s+(\d+)\s")
# The zone must be written out after the time. parsedate_tz reports a missing or unknown zone
# as offset 0, which is indistinguishable from UTC, so we read the zone token ourselves.
_ZONE = re.compile(r"\d{1,2}:\d{2}(?::\d{2})?\s+([+-]\d{4}|UT|UTC|GMT|Z|[ECMP][SD]T)\b", re.I)


@dataclass(frozen=True)
class ParsedDate:
    status: str
    utc: datetime | None = None        # aware, UTC; set only when status is ok
    local: datetime | None = None      # naive wall clock as printed in the header
    tz_offset_min: int | None = None   # e.g. -480 for -0800

    @property
    def ok(self) -> bool:
        return self.status == OK


def parse_header_date(raw: str | None) -> ParsedDate:
    """Parse an RFC 2822 Date header under rule R1."""
    if raw is None or not raw.strip():
        return ParsedDate(MISSING)
    t = parsedate_tz(raw)
    if t is None:
        return ParsedDate(INVALID)
    head = _HEAD.match(raw)
    if head is None:                       # the year as written can't be read
        return ParsedDate(INVALID)
    year = int(head.group(4))              # never parsedate_tz's "repaired" year
    if not WINDOW[0] <= year <= WINDOW[1]:
        return ParsedDate(OUT_OF_RANGE)
    try:
        local = datetime(year, t[1], t[2], t[3], t[4], t[5])   # raises on day 32, 25:61
    except ValueError:
        return ParsedDate(INVALID)
    weekday = head.group(1)
    if weekday is not None and weekday[:3].lower() != DAY_NAMES[local.weekday()]:
        return ParsedDate(INVALID)
    if t[9] is None or not _ZONE.search(raw):
        return ParsedDate(NO_TIMEZONE, local=local)
    utc = (local - timedelta(seconds=t[9])).replace(tzinfo=timezone.utc)
    return ParsedDate(OK, utc=utc, local=local, tz_offset_min=t[9] // 60)


# --- dates inside quoted headers (no offset: the writer's own clock, used to the day) ---
#
# No silent readings: whenever a quoted date is read as anything other than exactly what is
# written, the result says what was written, where, what it was read as, and why.

TWO_DIGIT_YEAR = "two_digit_year"
QUOTED_PRINTABLE = "quoted_printable_space"

# Lotus Notes prints two-digit years. Only years inside the corpus window are read; any other
# two-digit year gives no date at all.
TWO_DIGIT_YEARS = {"99": 1999, "00": 2000, "01": 2001, "02": 2002}


@dataclass(frozen=True)
class Reading:
    rule: str          # TWO_DIGIT_YEAR or QUOTED_PRINTABLE
    written: str       # the exact characters in the file
    read_as: str       # what code took them to mean
    why: str


@dataclass(frozen=True)
class QuotedDate:
    value: datetime                     # naive, the writer's clock; used to the day
    text: str                           # the date exactly as written in the file
    start: int                          # offsets of `text` within the text passed in
    end: int
    readings: tuple[Reading, ...] = ()  # empty when the date was read exactly as written


_SENT = re.compile(r"^[ \t>]*(?:Sent|Date):[ \t]*(.+?)\s*$", re.I | re.M)
_QP_SPACE = re.compile(r"=(09|20)")       # quoted-printable tab/space left in some Outlook headers
_LOTUS = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})\s+(\d{1,2}):(\d{2})\s*(AM|PM)", re.I)
# 'Friday, May 04, 2001 9:36 AM', 'November 15, 2001 9:05:12 PM GMT', 'Thursday, October 4, 2001 5:26:59 GMT'
_OUTLOOK = re.compile(
    r"^(?:([A-Za-z]+),\s+)?([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM)?(?:\s+[A-Z]{2,4})?$",
    re.I)
_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], start=1)}


def _clock(hh: str, mm: str, ampm: str) -> tuple[int, int] | None:
    h, m = int(hh), int(mm)
    if not 1 <= h <= 12 or not 0 <= m <= 59:
        return None
    return h % 12 + (12 if ampm.upper() == "PM" else 0), m


def _outlook(value: str) -> datetime | None:
    """'Friday, May 04, 2001 9:36 AM' with the weekday checked against the date."""
    m = _OUTLOOK.match(value.strip())
    if not m:
        return None
    wd, month, day, year, hh, mi, ss, ampm = m.groups()
    mon = _MONTHS.get(month.lower())
    hm = _clock(hh, mi, ampm) if ampm else ((int(hh), int(mi)) if int(hh) <= 23 and int(mi) <= 59 else None)
    if mon is None or hm is None:
        return None
    try:
        d = datetime(int(year), mon, int(day), hm[0], hm[1], int(ss or 0))
    except ValueError:
        return None
    if wd is not None and wd[:3].lower() != DAY_NAMES[d.weekday()]:
        return None
    return d


def _lotus(match: re.Match) -> tuple[datetime, tuple[Reading, ...]] | None:
    mo, dd, yy, hh, mi, ampm = match.groups()
    hm = _clock(hh, mi, ampm)
    if hm is None:
        return None
    readings: tuple[Reading, ...] = ()
    if len(yy) == 2:
        if yy not in TWO_DIGIT_YEARS:
            return None
        year = TWO_DIGIT_YEARS[yy]
        readings = (Reading(TWO_DIGIT_YEAR, yy, str(year),
                            f"two-digit year '{yy}' read as {year}: Lotus Notes prints two-digit years, "
                            f"and only 99, 00, 01 and 02 fall inside the corpus window 1999-2002"),)
    else:
        year = int(yy)
    try:
        return datetime(year, int(mo), int(dd), hm[0], hm[1]), readings
    except ValueError:
        return None


def parse_quoted_date(block_text: str) -> QuotedDate | None:
    """The date in a quoted block's own header (Outlook 'Sent:', RFC 'Date:' or a Lotus
    'MM/DD/YY(YY) HH:MM AM' line). None when there is none or it can't be read strictly."""
    m = _SENT.search(block_text)
    if m:
        raw = m.group(1)
        readings = tuple(Reading(QUOTED_PRINTABLE, q.group(0), " ",
                                 f"quoted-printable '{q.group(0)}' left in the header read as a space")
                         for q in _QP_SPACE.finditer(raw))
        value = _QP_SPACE.sub(" ", raw).strip()
        found = None
        d = _outlook(value)
        if d is not None:
            found = (d, ())
        else:
            p = parse_header_date(value)
            if p.status in (OK, NO_TIMEZONE):
                found = (p.local, ())
            else:
                lm = _LOTUS.fullmatch(value)
                found = _lotus(lm) if lm else None
        if found is not None:
            return QuotedDate(found[0], raw, m.start(1), m.end(1), readings + found[1])
    m = _LOTUS.search(block_text[:400])
    if m:
        found = _lotus(m)
        if found is not None:
            return QuotedDate(found[0], m.group(0), m.start(), m.end(), found[1])
    return None


_DATE_LIKE = re.compile(
    r"(^[ \t>]*(?:Sent|Date):[ \t]*(?P<v>\S.*?)\s*$)|(?P<d>\b\d{1,2}/\d{1,2}/\d{2,4}\s+\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM)?)",
    re.I | re.M)


def explain_unread_date(block_text: str) -> tuple[str, int, int, str] | None:
    """For a quoted block whose date couldn't be read: the date-like text as written, its offsets
    in `block_text`, and why it was not read. None when the block has no date-like text at all."""
    m = _DATE_LIKE.search(block_text[:600])
    if not m:
        return None
    grp = "v" if m.group("v") else "d"
    text, start, end = m.group(grp), m.start(grp), m.end(grp)
    value = _QP_SPACE.sub(" ", text).strip()
    om = _OUTLOOK.match(value)
    lm = re.search(r"\b\d{1,2}/\d{1,2}/(\d{2})\b", value)
    if om and om.group(1):
        why = "weekday name or date fields don't check out, so the date is not read"
    elif lm and lm.group(1) not in TWO_DIGIT_YEARS:
        why = f"two-digit year '{lm.group(1)}' is outside 99, 00, 01, 02 (corpus window 1999-2002), so it is not read"
    else:
        why = "not in a date format the strict parser accepts, so it is not read (nothing is guessed)"
    return text, start, end, why


# --- clock check: is this header 7-8 h behind the sender's own clock? ---

_FWD = re.compile(
    r"^[ \t]*-{2,}\s*Forwarded by\s+(.+?)\s+on\s+(\d{1,2})/(\d{1,2})/(\d{4})\s*\r?\n?\s*(\d{1,2}):(\d{2})\s*(AM|PM)",
    re.I | re.M)


def clock_check(parsed: ParsedDate, new_text: str, quoted: str) -> str:
    """Compare the Date header with the time on the message's own fresh Lotus forward line.

    Testable only when the message is a fresh forward: little new text (<= 300 chars) and a
    'Forwarded by X on MM/DD/YYYY HH:MM AM' line at the top of the quoted part. Timestamps are
    never corrected; this only records what the evidence shows.
    """
    if not parsed.ok or not quoted or len(norm_ws(new_text)) > 300:
        return UNTESTABLE
    m = _FWD.search(quoted[:300])
    if not m:
        return UNTESTABLE
    _, mo, dd, yyyy, hh, mi, ampm = m.groups()
    hm = _clock(hh, mi, ampm)
    if hm is None:
        return UNTESTABLE
    try:
        forwarded = datetime(int(yyyy), int(mo), int(dd), hm[0], hm[1])
    except ValueError:
        return UNTESTABLE
    hours = (forwarded - parsed.local).total_seconds() / 3600
    offset_hours = -parsed.tz_offset_min / 60          # 8.0 for -0800
    if abs(hours) < 0.6 or 1.4 < hours < 2.6:           # same clock, or Central vs Pacific
        return MATCHES
    if abs(hours - offset_hours) < 0.6:
        return SHIFTED
    return UNTESTABLE


def calendar_days(earlier: datetime, later: datetime) -> int:
    """Whole calendar days between two UTC datetimes (R1: durations in whole days)."""
    return (later.date() - earlier.date()).days


def month_key(utc: datetime) -> str:
    """Month bucket, by UTC date (R1)."""
    return f"{utc.year:04d}-{utc.month:02d}"
