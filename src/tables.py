"""R4 giant or tabular messages.

A body over 64,000 bytes gets special handling. If at least half of its non-blank lines are
table-shaped, code parses every row (keeping row numbers and file offsets) and writes a
summary; Claude reads only the prose plus that summary. A table row is a data record, never
an event. Each column's format is decided once for the whole column, never per value.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

BODY_LIMIT_BYTES = 64_000
TABLE_SHARE = 0.5

TABLE_LINE = re.compile(r"\S+(\s{2,}\S+){3,}")
_DASHES = re.compile(r"^\s*-{5,}\s*$")
_SPLIT = re.compile(r"\s{2,}")
_INT = re.compile(r"^[+-]?(\d{1,3}(,\d{3})+|\d+)$")
_DATE = re.compile(r"^(\d{1,2})/\s?(\d{1,2})/(\d{4})$")

DAY_MONTH, MONTH_DAY, AMBIGUOUS, INVALID = "day/month", "month/day", "ambiguous", "invalid"


CHUNK_BYTES = 32_000


def chunk_body(text: str, body_start: int, limit: int = CHUNK_BYTES) -> list[tuple[int, int]]:
    """Split an oversized prose body into consecutive chunks of at most `limit` characters, cutting
    at a blank line when possible, else at a line end, else hard. Returns file offsets; the chunks
    cover the body exactly, with nothing dropped or repeated."""
    chunks, pos, end = [], body_start, len(text)
    while pos < end:
        stop = min(pos + limit, end)
        if stop < end:
            window = text[pos:stop]
            cut = max(window.rfind("\n\n"), window.rfind("\r\n\r\n"))
            if cut > limit // 4:
                stop = pos + cut + (4 if window.startswith("\r\n\r\n", cut) else 2)
            else:
                nl = window.rfind("\n")
                if nl > limit // 4:
                    stop = pos + nl + 1
        chunks.append((pos, stop))
        pos = stop
    return chunks


def needs_special_handling(body: str) -> bool:
    """R4 uses the body, never the file: some files are 50 KB of headers with a 3 KB body."""
    return len(body.encode("utf-8")) > BODY_LIMIT_BYTES


def table_share(body: str) -> float:
    lines = [l for l in body.splitlines() if l.strip()]
    return sum(1 for l in lines if TABLE_LINE.search(l)) / len(lines) if lines else 0.0


@dataclass
class Row:
    row_no: int          # 1-based, in table order
    start: int           # file offsets of the line (no line ending)
    end: int
    raw: str
    values: dict


@dataclass
class TableResult:
    columns: list[str]
    column_types: dict[str, str]
    rows: list[Row] = field(default_factory=list)
    bad_rows: list[tuple[int, str]] = field(default_factory=list)    # (file offset, line)
    prose: list[str] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    header_start: int = 0            # file offsets of the column-header line
    header_end: int = 0
    # Column -> why its values were read the way they were (no silent readings). Only columns
    # whose values are read as something other than the literal text appear here.
    column_readings: dict[str, str] = field(default_factory=dict)


def _valid(y: int, m: int, d: int) -> bool:
    try:
        date(y, m, d)
        return True
    except ValueError:
        return False


def _date_format(values: list[str]) -> tuple[str, int, int]:
    """Decide one format for a whole date column. Returns (format, invalid as D/M, invalid as M/D)."""
    parts = [tuple(int(x) for x in _DATE.match(v).groups()) for v in values]
    dm_bad = sum(1 for a, b, y in parts if not _valid(y, b, a))
    md_bad = sum(1 for a, b, y in parts if not _valid(y, a, b))
    if dm_bad == 0 and md_bad > 0:
        return DAY_MONTH, dm_bad, md_bad
    if md_bad == 0 and dm_bad > 0:
        return MONTH_DAY, dm_bad, md_bad
    if dm_bad == 0 and md_bad == 0:
        return AMBIGUOUS, dm_bad, md_bad
    return INVALID, dm_bad, md_bad


def _lines_with_offsets(text: str, start: int):
    pos = start
    for line in text.splitlines(keepends=True):
        bare = line.rstrip("\r\n")
        yield pos, bare
        pos += len(line)


def parse_table(text: str, body_start: int, sent_on: date | None = None) -> TableResult | None:
    """Parse the first header + dashes table in the body. Returns None if there is none.
    sent_on is the day the message was sent (its Date header's own calendar day), if known."""
    lines = list(_lines_with_offsets(text[body_start:], body_start))
    header_i = next((i for i in range(len(lines) - 1)
                     if lines[i][1].strip() and _DASHES.match(lines[i + 1][1])), None)
    if header_i is None:
        return None
    columns = _SPLIT.split(lines[header_i][1].strip())
    h_off, h_line = lines[header_i]
    result = TableResult(columns=columns, column_types={}, header_start=h_off, header_end=h_off + len(h_line))
    result.prose = [l.strip() for _, l in lines[:header_i] if l.strip()]
    raw_rows = []
    for off, line in lines[header_i + 2:]:
        if not line.strip():
            continue
        cells = _SPLIT.split(line.strip())
        if len(cells) == len(columns):
            raw_rows.append((off, line, cells))
        elif TABLE_LINE.search(line):
            result.bad_rows.append((off, line))
        else:
            result.prose.append(line.strip())

    # Decide each column's type once, from every value in it.
    col_values = {c: [cells[i] for _, _, cells in raw_rows] for i, c in enumerate(columns)}
    date_info = {}
    for c, vals in col_values.items():
        if vals and all(_INT.match(v) for v in vals):
            result.column_types[c] = "int"
            if any("," in v or v[0] in "+-" for v in vals):
                result.column_readings[c] = ("whole numbers: commas read as thousands separators and a leading "
                                             "'+' or '-' as the sign (e.g. '+1,160' is 1160); the raw line is kept")
        elif vals and all(_DATE.match(v) for v in vals):
            fmt, dm_bad, md_bad = _date_format(vals)
            result.column_types[c] = "date " + fmt
            date_info[c] = {"format": fmt, "invalid_as_day_month": dm_bad, "invalid_as_month_day": md_bad}
            n = len(vals)
            if fmt == DAY_MONTH:
                result.column_readings[c] = (f"read as day/month for the whole column: {md_bad:,} of {n:,} values "
                                             f"are impossible as month/day and none as day/month")
            elif fmt == MONTH_DAY:
                result.column_readings[c] = (f"read as month/day for the whole column: {dm_bad:,} of {n:,} values "
                                             f"are impossible as day/month and none as month/day")
            date_info[c]["decision"] = result.column_readings.get(c) or (
                "kept as written: every value is valid both ways" if fmt == AMBIGUOUS
                else f"kept as written: {dm_bad:,} values are impossible as day/month and {md_bad:,} as month/day")
        else:
            result.column_types[c] = "text"

    for n, (off, line, cells) in enumerate(raw_rows, start=1):
        values = {}
        for c, v in zip(columns, cells):
            t = result.column_types[c]
            if t == "int":
                values[c] = int(v.replace(",", ""))
            elif t in ("date " + DAY_MONTH, "date " + MONTH_DAY):
                a, b, y = (int(x) for x in _DATE.match(v).groups())
                d, m = (a, b) if t.endswith(DAY_MONTH) else (b, a)
                values[c] = date(y, m, d).isoformat()
            else:
                values[c] = v          # text, or an ambiguous/invalid date kept as written
        result.rows.append(Row(n, off, off + len(line), line, values))
    result.summary = summarize(result, date_info, sent_on)
    return result


QUANTILES = (10, 25, 50, 75, 90)
TOP_N = 5

# Exactly how each summary figure is computed (shown with the summary, so nothing is a black box).
CALCULATIONS = {
    "min / max / sum": "over every row's parsed value",
    "negative / zero / positive": "count of rows with value < 0, = 0, > 0",
    "quantiles": "nearest-rank: the value at position ceil(p/100 * n) of the sorted values (p = 10,25,50,75,90)",
    "largest_abs": f"the {TOP_N} rows with the largest absolute value, ties broken by row number; each has its "
                   "row number and the raw line, which sits at known byte offsets in the file",
    "months": "date columns: how many distinct calendar months (year and month) the parsed dates fall in",
    "after_sent": "date columns: rows dated later than the day the message was sent (its Date header, as printed "
                  f"in the sender's time zone), with the first {TOP_N} row numbers; absent if the message has no "
                  "usable Date",
}


def _nearest_rank(sorted_vals: list[int], p: int) -> int:
    k = max(1, -(-p * len(sorted_vals) // 100))          # ceil(p * n / 100), at least 1
    return sorted_vals[k - 1]


def summarize(t: TableResult, date_info: dict, sent_on: date | None = None) -> dict:
    cols = {}
    for c in t.columns:
        kind = t.column_types[c]
        vals = [r.values[c] for r in t.rows]
        info: dict = {"type": kind}
        if kind == "int":
            s = sorted(vals)
            top = sorted(t.rows, key=lambda r: (-abs(r.values[c]), r.row_no))[:TOP_N]
            info.update(min=min(vals), max=max(vals), sum=sum(vals),
                        negative=sum(v < 0 for v in vals), zero=sum(v == 0 for v in vals),
                        positive=sum(v > 0 for v in vals),
                        quantiles={f"p{p}": _nearest_rank(s, p) for p in QUANTILES},
                        largest_abs=[{"row_no": r.row_no, "value": r.values[c], "raw": r.raw.strip()} for r in top])
        elif kind.startswith("date"):
            info.update(date_info[c])
            if kind.endswith((DAY_MONTH, MONTH_DAY)):
                info.update(min=min(vals), max=max(vals), months=len({v[:7] for v in vals}))
                if sent_on is not None:
                    later = [r.row_no for r in t.rows if r.values[c] > sent_on.isoformat()]
                    info.update(sent_on=sent_on.isoformat(), after_sent=len(later), after_sent_first_rows=later[:TOP_N])
        else:
            distinct = Counter(vals)
            info["distinct"] = len(distinct)
            if len(distinct) <= 20:
                info["counts"] = dict(sorted(distinct.items()))
        cols[c] = info
    summary = {"row_count": len(t.rows), "bad_rows": len(t.bad_rows), "columns": cols,
               "calculations": CALCULATIONS,
               "evidence_note": "rows are data records, never events; any number taken from this table is "
                                "computed by code and cites row numbers"}
    if {"SCHEDULED", "ACTUAL", "VARIANCE"} <= set(t.columns):
        summary["variance_check"] = variance_check(t.rows)
    return summary


def variance_check(rows: list[Row]) -> dict:
    """SCHEDULED - ACTUAL must equal VARIANCE, in integers."""
    failures = [r.row_no for r in rows
                if r.values["SCHEDULED"] - r.values["ACTUAL"] != r.values["VARIANCE"]]
    return {"rows": len(rows), "pass": len(rows) - len(failures), "failed_rows": failures[:50]}
