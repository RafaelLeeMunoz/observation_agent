"""R1 dates. Each case fails for the reason it names."""
from datetime import datetime
from email.utils import mktime_tz, parsedate_tz

from src import dates
from src.dates import parse_header_date, parse_quoted_date

from conftest import utc


def test_impossible_date_is_invalid_not_rolled_forward():
    # No weekday name, so only the field check can reject it (a weekday would mask a rollover).
    raw = "32 Feb 2001 25:61:00 -0600"
    rolled = datetime.fromtimestamp(mktime_tz(parsedate_tz(raw)))     # the trap: March
    assert rolled.month == 3
    assert parse_header_date(raw).status == dates.INVALID
    assert parse_header_date(raw).utc is None
    assert parse_header_date("29 Feb 2001 10:00:00 -0800").status == dates.INVALID    # not a leap year


def test_year_is_taken_as_written_never_repaired():
    raw = "Sat, 26 Nov 0001 07:35:14 -0800 (PST)"          # lokay-m/inbox/50
    assert parsedate_tz(raw)[0] == 2001                     # the stdlib "repair"
    assert parse_header_date(raw).status == dates.OUT_OF_RANGE
    assert parse_header_date("Mon, 4 Feb 02 13:47:02 -0800").status == dates.OUT_OF_RANGE


def test_weekday_that_contradicts_the_date_is_invalid():
    assert parse_header_date("Mon, 26 Nov 2001 07:35:14 -0800").status == dates.OK
    assert parse_header_date("Sat, 26 Nov 2001 07:35:14 -0800").status == dates.INVALID
    assert parse_header_date("Tues, 5 Feb 2002 10:00:00 -0800").status == dates.OK   # 'Tues' is Tuesday


def test_missing_offset_is_never_assumed_utc():
    raw = "Mon, 4 Feb 2002 13:47:02"
    assert parsedate_tz(raw)[9] == 0                       # the stdlib can't tell this from UTC
    p = parse_header_date(raw)
    assert p.status == dates.NO_TIMEZONE
    assert p.utc is None
    assert p.local == datetime(2002, 2, 4, 13, 47, 2)
    assert parse_header_date("Mon, 4 Feb 2002 13:47:02 XYZ").status == dates.NO_TIMEZONE


def test_written_zones_are_accepted():
    assert parse_header_date("Mon, 4 Feb 2002 13:47:02 -0000").utc == utc(2002, 2, 4, 13, 47, 2)
    assert parse_header_date("Mon, 4 Feb 2002 13:47:02 GMT").status == dates.OK
    assert parse_header_date("Mon, 4 Feb 2002 13:47:02 EST").utc == utc(2002, 2, 4, 18, 47, 2)


def test_valid_date_converts_to_utc_and_keeps_offset():
    p = parse_header_date("Mon, 13 Dec 1999 08:00:00 -0800 (PST)")
    assert p.status == dates.OK
    assert p.utc == utc(1999, 12, 13, 16, 0)
    assert p.local == datetime(1999, 12, 13, 8, 0)
    assert p.tz_offset_min == -480


def test_the_four_real_problem_headers():
    assert parse_header_date(None).status == dates.MISSING                                   # giron-d/sent/795
    assert parse_header_date("Tues, 32 Febuary 2001 25:61:00").status == dates.INVALID        # williams-w3/inbox/142
    assert parse_header_date("Wed, 12 Mar 2031 08:14:00 -0600 (CST)").status == dates.OUT_OF_RANGE  # lokay-m/inbox/74
    assert parse_header_date("Sat, 26 Nov 0001 07:35:14 -0800 (PST)").status == dates.OUT_OF_RANGE  # lokay-m/inbox/50


def test_window_edges():
    assert parse_header_date("Fri, 1 Jan 1999 00:00:00 -0800").status == dates.OK
    assert parse_header_date("Thu, 31 Dec 1998 23:59:59 -0800").status == dates.OUT_OF_RANGE
    assert parse_header_date("Wed, 1 Jan 2003 00:00:00 -0800").status == dates.OUT_OF_RANGE
    # The window applies to the year as written; the UTC value may cross into 2003.
    p = parse_header_date("Tue, 31 Dec 2002 23:00:00 -0800")
    assert p.status == dates.OK and p.utc.year == 2003


def test_leap_days_and_full_weekday_names():
    assert parse_header_date("Tue, 29 Feb 2000 10:00:00 -0800").status == dates.OK      # 2000 is a leap year
    assert parse_header_date("Thursday, 1 Mar 2001 10:00:00 -0800").status == dates.OK
    assert parse_header_date("Friday, 1 Mar 2001 10:00:00 -0800").status == dates.INVALID


# --- dates inside quoted headers ---

def qd(text):
    d = parse_quoted_date(text)
    return d.value if d else None


def test_outlook_sent_line_read_as_written():
    text = "From: Bob\r\nSent: Friday, May 04, 2001 9:36 AM\r\n"
    d = parse_quoted_date(text)
    assert d.value == datetime(2001, 5, 4, 9, 36)
    assert d.readings == ()                                     # nothing read other than as written
    assert text[d.start:d.end] == d.text == "Friday, May 04, 2001 9:36 AM"


def test_outlook_wrong_weekday_gives_no_date():
    assert parse_quoted_date("Sent: Monday, May 04, 2001 9:36 AM\r\n") is None


def test_quoted_printable_is_read_as_a_space_and_flagged():
    # The M1 notebook read this as 03:56; the strict parser keeps PM.
    text = "Sent:=09Wednesday, October 10, 2001 3:56 PM\r\n"
    d = parse_quoted_date(text)
    assert d.value == datetime(2001, 10, 10, 15, 56)
    assert text[d.start:d.end] == "=09Wednesday, October 10, 2001 3:56 PM"     # stored as written
    assert [(r.rule, r.written, r.read_as) for r in d.readings] == [(dates.QUOTED_PRINTABLE, "=09", " ")]


def test_outlook_without_weekday_and_with_zone_name():
    assert qd("Sent: Thursday, October 4, 2001 5:26:59 GMT\r\n") == datetime(2001, 10, 4, 5, 26, 59)
    assert qd("Sent: November 15, 2001 9:05:12 PM GMT\r\n") == datetime(2001, 11, 15, 21, 5, 12)


def test_lotus_name_date_lines():
    assert qd("Jane S Saladino\r\n01/31/2001 07:24 AM\r\nTo: Darron") == datetime(2001, 1, 31, 7, 24)
    assert qd("Sent: 03/27/2001 01:36 PM\r\n") == datetime(2001, 3, 27, 13, 36)
    assert qd("Jane\r\n02/30/2001 07:24 AM\r\nTo: x") is None                 # no 30 Feb
    assert qd("Jane\r\n02/03/2001 13:24 PM\r\nTo: x") is None                 # no 13 PM


def test_two_digit_year_is_read_and_flagged_with_why():
    text = "Howard B Camp\r\n12/16/99 12:27 PM\r\nTo: Daren J Farmer"
    d = parse_quoted_date(text)
    assert d.value == datetime(1999, 12, 16, 12, 27)
    assert text[d.start:d.end] == d.text == "12/16/99 12:27 PM"                # where, as written
    (r,) = d.readings
    assert (r.rule, r.written, r.read_as) == (dates.TWO_DIGIT_YEAR, "99", "1999")
    assert "Lotus Notes prints two-digit years" in r.why and "1999-2002" in r.why
    assert qd("Name\r\n06/25/01 02:47 PM\r\nTo: x") == datetime(2001, 6, 25, 14, 47)


def test_lotus_date_with_seconds():
    assert qd('"X" <x@y.com> on 08/23/2000 01:40:40 PM\r\nTo: z') == datetime(2000, 8, 23, 13, 40, 40)
    assert qd('"X" <x@y.com> on 08/23/2000 01:40:61 PM\r\nTo: z') is None          # no 61st second


def test_two_digit_year_outside_the_window_gives_no_date():
    assert parse_quoted_date("Name\r\n12/16/87 12:27 PM\r\nTo: x") is None
    assert parse_quoted_date("Name\r\n12/16/03 12:27 PM\r\nTo: x") is None
    assert parse_quoted_date("Name\r\n12/16/98 12:27 PM\r\nTo: x") is None             # just below
    assert qd("Name\r\n02/16/02 12:27 PM\r\nTo: x") == datetime(2002, 2, 16, 12, 27)  # last inside


def test_dates_that_cannot_be_read_are_explained_not_dropped():
    for text, why_part in [
        ("Sent: Wednesday, March 14, 2001\r\n", "not in a date format"),            # no time (giron-d/sent/135)
        ("Sent: Domingo 25 de Noviembre de 2001 08:02\r\n", "not in a date format"),  # Spanish (giron-d/inbox/473)
        ("Sent: Monday, May 04, 2001 9:36 AM\r\n", "weekday"),                       # May 4 2001 was a Friday
        ("Name\r\n12/16/87 12:27 PM\r\nTo: x", "two-digit year '87'"),
    ]:
        assert parse_quoted_date(text) is None
        written, start, end, why = dates.explain_unread_date(text)
        assert text[start:end] == written and why_part in why, (text, why)
    assert dates.explain_unread_date("Just a note, no dates here.") is None


def test_header_dates_never_read_two_digit_years():
    # R1 for the message's own Date header is unchanged: the year is taken as written.
    assert parse_header_date("Thu, 16 Dec 99 12:27:00 -0800").status == dates.OUT_OF_RANGE


# --- clock check ---

FWD = ("---------------------- Forwarded by Daren J Farmer/HOU/ECT on 01/04/2000 {t} "
       "---------------------------\r\n\r\nOriginal text.\r\n")


def test_clock_check_shifted_matches_untestable():
    p = parse_header_date("Tue, 4 Jan 2000 02:35:00 -0800 (PST)")
    assert dates.clock_check(p, "", FWD.format(t="10:35 AM")) == dates.SHIFTED     # +8 h = the offset
    assert dates.clock_check(p, "", FWD.format(t="02:35 AM")) == dates.MATCHES
    assert dates.clock_check(p, "", FWD.format(t="04:35 AM")) == dates.MATCHES     # Central vs Pacific
    assert dates.clock_check(p, "", "> no forward line here\r\n") == dates.UNTESTABLE
    assert dates.clock_check(p, "x" * 400, FWD.format(t="10:35 AM")) == dates.UNTESTABLE   # not a fresh forward
    assert dates.clock_check(parse_header_date(None), "", FWD.format(t="10:35 AM")) == dates.UNTESTABLE


def test_calendar_days_and_month_key():
    assert dates.calendar_days(utc(2001, 1, 31, 23, 0), utc(2001, 2, 1, 1, 0)) == 1
    assert dates.month_key(utc(2001, 2, 1, 1, 0)) == "2001-02"
