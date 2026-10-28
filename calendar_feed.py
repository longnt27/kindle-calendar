"""Read one private Google iCal feed without granting calendar write access."""
import json
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import recurring_ical_events
from icalendar import Calendar

LOCAL_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def valid_google_feed(url):
    parsed = urlsplit(url)
    return (parsed.scheme == "https" and parsed.hostname == "calendar.google.com"
            and parsed.path.startswith("/calendar/ical/") and parsed.path.endswith(".ics")
            and not parsed.username and not parsed.password and not parsed.fragment)


def events_by_day(source, first, last):
    """Return local-day entries for the half-open date interval [first, last)."""
    days = {}
    if source is None:
        return days
    for event in recurring_ical_events.of(source).between(first, last):
        if str(event.get("STATUS", "")).upper() == "CANCELLED":
            continue
        start = event.decoded("DTSTART")
        end = event.decoded("DTEND")
        all_day = isinstance(start, date) and not isinstance(start, datetime)
        if not all_day:
            start = start.replace(tzinfo=LOCAL_TZ) if start.tzinfo is None else start.astimezone(LOCAL_TZ)
            end = end.replace(tzinfo=LOCAL_TZ) if end.tzinfo is None else end.astimezone(LOCAL_TZ)
        if end <= start:
            continue
        if all_day:
            end_day = end - timedelta(days=1)
        else:
            end_day = (end - timedelta(microseconds=1)).date()
        day = max(start if all_day else start.date(), first)
        while day <= end_day and day < last:
            entry = {
                "title": str(event.get("SUMMARY", "(Untitled)")),
                "all_day": all_day,
                "time": "ALL DAY" if all_day else (start.strftime("%H:%M") if day == start.date() else "CONT."),
                "start_minute": 0 if all_day or day > start.date() else start.hour * 60 + start.minute,
                "end_minute": 1440 if all_day or day < end.date() else end.hour * 60 + end.minute,
            }
            days.setdefault(day.isoformat(), []).append(entry)
            day += timedelta(days=1)
    for entries in days.values():
        entries.sort(key=lambda item: (not item["all_day"], item["start_minute"], item["title"]))
    return days


class Feed:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.url = None
        self.calendar = None
        self.checked_at = 0
        self.last_sync = None
        self.error = None

    def configured(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))["url"]
        except FileNotFoundError:
            return None

    def status(self):
        return {"configured": bool(self.configured()), "last_sync": self.last_sync, "error": self.error}

    def reset(self):
        with self.lock:
            self.url = None
            self.calendar = None
            self.checked_at = 0
            self.last_sync = None
            self.error = None

    def get(self):
        url = self.configured()
        if not url:
            return None
        with self.lock:
            if url != self.url:
                self.url = url
                self.calendar = None
                self.checked_at = 0
            if not self.checked_at or time.monotonic() - self.checked_at >= 300:
                self.checked_at = time.monotonic()
                try:
                    with urlopen(Request(url, headers={"User-Agent": "KindleCalendar/1.0"}), timeout=12) as response:
                        payload = response.read(5_000_001)
                    if len(payload) > 5_000_000:
                        raise ValueError("Calendar feed is too large")
                    self.calendar = Calendar.from_ical(payload)
                    self.last_sync = datetime.now(LOCAL_TZ).isoformat(timespec="minutes")
                    self.error = None
                except HTTPError as error:
                    self.error = f"Google Calendar returned HTTP {error.code}. Recopy the private iCal address."
                except Exception:
                    self.error = "Could not refresh Google Calendar; showing the last good copy."
            return self.calendar
