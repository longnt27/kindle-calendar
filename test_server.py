import http.client
import hashlib
import io
import json
import os
import struct
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from PIL import Image, ImageOps

import server
from calendar_feed import events_by_day


SAMPLE_ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:daily@example.com
DTSTAMP:20261001T000000Z
DTSTART;TZID=Asia/Ho_Chi_Minh:20261028T090000
DTEND;TZID=Asia/Ho_Chi_Minh:20261028T100000
RRULE:FREQ=DAILY;COUNT=3
SUMMARY:Daily focus
END:VEVENT
BEGIN:VEVENT
UID:offsite@example.com
DTSTAMP:20261001T000000Z
DTSTART;VALUE=DATE:20261029
DTEND;VALUE=DATE:20261031
SUMMARY:Offsite
END:VEVENT
END:VCALENDAR"""


class CalendarHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.httpd = server.make_server("127.0.0.1", 0, Path(self.tmp.name) / "reminders.json",
                                        Path(self.tmp.name) / "projects.json",
                                        Path(self.tmp.name) / "calendar_feed.json")
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def request(self, method, path, payload=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port)
        body = json.dumps(payload) if payload is not None else None
        conn.request(method, path, body, {"Content-Type": "application/json"})
        response = conn.getresponse()
        result = response.status, response.getheader("Content-Type"), response.read()
        conn.close()
        return result

    def test_add_list_delete_and_private_storage(self):
        status, _, body = self.request("POST", "/api/reminders", {"date": "2026-10-30", "text": "Pay rent"})
        self.assertEqual(status, 201)
        reminder = json.loads(body)
        self.assertEqual(reminder["text"], "Pay rent")
        self.assertEqual(json.loads(self.request("GET", "/api/reminders")[2]), [reminder])
        self.assertEqual(self.request("GET", "/reminders.json")[0], 404)
        self.assertEqual(self.request("DELETE", "/api/reminders/" + reminder["id"])[0], 204)
        self.assertEqual(json.loads(self.request("GET", "/api/reminders")[2]), [])

    def test_invalid_reminders_are_rejected(self):
        for item in ({"date": "2026-02-30", "text": "x"}, {"date": "2026-10-30", "text": " "},
                     {"date": "2026-10-30", "text": "x" * 201}):
            self.assertEqual(self.request("POST", "/api/reminders", item)[0], 400)
        self.assertEqual(json.loads(self.request("GET", "/api/reminders")[2]), [])

    def test_projects_appear_on_clock_and_can_be_removed(self):
        before = self.request("GET", "/clock-preview.png")[2]
        calendar_before = self.request("GET", "/preview.png")[2]
        status, _, body = self.request("POST", "/api/projects", {"name": "Write the thesis"})
        self.assertEqual(status, 201)
        project = json.loads(body)
        self.assertEqual(json.loads(self.request("GET", "/api/projects")[2]), [project])
        self.assertNotEqual(before, self.request("GET", "/clock-preview.png")[2])
        self.assertEqual(calendar_before, self.request("GET", "/preview.png")[2])
        self.assertEqual(self.request("GET", "/projects.json")[0], 404)
        self.assertEqual(self.request("DELETE", "/api/projects/" + project["id"])[0], 204)
        self.assertEqual(json.loads(self.request("GET", "/api/projects")[2]), [])
        self.assertEqual(before, self.request("GET", "/clock-preview.png")[2])

    def test_projects_are_bounded_to_screen_capacity(self):
        self.assertEqual(self.request("POST", "/api/projects", {"name": " "})[0], 400)
        self.assertEqual(self.request("POST", "/api/projects", {"name": "x" * 31})[0], 400)
        for number in range(9):
            self.assertEqual(self.request("POST", "/api/projects", {"name": f"Project {number}"})[0], 201)
        self.assertEqual(self.request("POST", "/api/projects", {"name": "Tenth"})[0], 400)

    def test_private_calendar_feed_is_read_only_and_expands_events(self):
        from datetime import date
        from icalendar import Calendar
        feed_url = "https://calendar.google.com/calendar/ical/test/private-abc/basic.ics"
        self.assertEqual(self.request("POST", "/api/calendar", {"url": "http://example.com/a.ics"})[0], 400)
        self.assertEqual(self.request("POST", "/api/calendar", {"url": feed_url})[0], 204)
        status = json.loads(self.request("GET", "/api/calendar")[2])
        self.assertTrue(status["configured"])
        self.assertNotIn(feed_url, json.dumps(status))
        self.assertEqual(self.request("GET", "/calendar_feed.json")[0], 404)
        with patch("calendar_feed.urlopen", return_value=io.BytesIO(SAMPLE_ICS)) as fetch:
            result = json.loads(self.request("GET", "/api/calendar/events?start=2026-10-27&end=2026-11-01")[2])
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(self.request("GET", "/api/calendar/events?start=2026-10-27&end=2026-11-01")[0], 200)
            self.assertEqual(fetch.call_count, 1)
        self.assertEqual([item["title"] for item in result["days"]["2026-10-29"]],
                         ["Offsite", "Daily focus"])
        self.assertEqual(result["days"]["2026-10-30"][0]["time"], "ALL DAY")
        self.assertEqual(self.request("DELETE", "/api/calendar")[0], 204)
        self.assertFalse(json.loads(self.request("GET", "/api/calendar")[2])["configured"])
        self.assertEqual(self.request("POST", "/api/calendar", {"url": feed_url})[0], 204)
        with patch("calendar_feed.urlopen", side_effect=HTTPError(feed_url, 404, "Not Found", {}, None)):
            result = json.loads(self.request("GET", "/api/calendar/events?start=2026-10-27&end=2026-11-01")[2])
        self.assertIn("HTTP 404", result["error"])
        self.assertNotIn(feed_url, json.dumps(result))

    def test_selected_day_and_week_images_change(self):
        from datetime import date
        events = {"2026-10-29": [{"title": "Meeting", "all_day": False, "time": "11:00",
                                  "start_minute": 660, "end_minute": 720}]}
        first = server.draw_calendar(date(2026, 10, 28), date(2026, 10, 1), "14:07", events,
                                     date(2026, 10, 28))
        second = server.draw_calendar(date(2026, 10, 28), date(2026, 10, 1), "14:07", events,
                                      date(2026, 10, 29))
        self.assertNotEqual(first.tobytes(), second.tobytes())
        two_events = {"2026-10-29": events["2026-10-29"] * 2}
        more_dots = server.draw_calendar(date(2026, 10, 28), date(2026, 10, 1),
                                         "14:07", two_events, date(2026, 10, 28))
        self.assertNotEqual(first.tobytes(), more_dots.tobytes())
        self.assertEqual(server.draw_week(date(2026, 10, 28), date(2026, 10, 29), "14:07", events).size,
                         (1648, 1236))
        self.assertEqual(self.request("GET", "/display.png?view=week&day=2026-10-29")[0], 200)
        self.assertEqual(self.request("GET", "/display.png?view=week&hour=13")[0], 400)
        self.assertEqual(self.request("GET", "/display.png?tap=500,500&month=2026-10")[0], 200)
        overlapping = [{"start_minute": 540, "end_minute": 600},
                       {"start_minute": 570, "end_minute": 630},
                       {"start_minute": 630, "end_minute": 660}]
        self.assertEqual([(lane, lanes) for _, lane, lanes in server.event_lanes(overlapping)],
                         [(0, 2), (1, 2), (0, 1)])

    def test_full_screen_image_is_generated(self):
        for device_path, preview_path in (("/clock.png", "/clock-preview.png"),
                                          ("/display.png", "/preview.png")):
            status, content_type, body = self.request("GET", device_path)
            self.assertEqual((status, content_type), (200, "image/png"))
            device = Image.open(io.BytesIO(body))
            preview = Image.open(io.BytesIO(self.request("GET", preview_path)[2]))
            self.assertEqual((device.size, device.mode), ((1236, 1648), "L"))
            self.assertEqual((preview.size, preview.mode), ((1648, 1236), "L"))
            self.assertEqual(hashlib.sha256(device.transpose(Image.Transpose.ROTATE_270).tobytes()).digest(),
                             hashlib.sha256(preview.tobytes()).digest())
        clock = Image.open(io.BytesIO(self.request("GET", "/clock-preview.png")[2]))
        clock_ink = ImageOps.invert(clock.crop((0, 250, 1648, 750))).getbbox()
        self.assertGreaterEqual(clock_ink[2] - clock_ink[0], 1400)
        self.assertEqual(self.request("GET", "/display.png?month=2026-11")[0], 200)
        self.assertEqual(self.request("GET", "/display.png?month=2026-13")[0], 400)
        self.assertEqual(self.request("GET", "/display.png?month=0000-01")[0], 400)

    def test_swiped_month_changes_image(self):
        from datetime import date
        october = server.draw_calendar(date(2026, 10, 27), date(2026, 10, 1))
        november = server.draw_calendar(date(2026, 10, 27), date(2026, 11, 1))
        self.assertNotEqual(hashlib.sha256(october.tobytes()).digest(),
                            hashlib.sha256(november.tobytes()).digest())

    def test_clock_changes_image_each_minute(self):
        from datetime import date
        for draw in (server.draw_clock, server.draw_calendar):
            at_one = draw(date(2026, 10, 27), clock_time="13:00")
            at_two = draw(date(2026, 10, 27), clock_time="13:01")
            self.assertNotEqual(hashlib.sha256(at_one.tobytes()).digest(),
                                hashlib.sha256(at_two.tobytes()).digest())

    def test_touch_emits_tap_and_landscape_swipes(self):
        with tempfile.TemporaryDirectory() as directory:
            events = Path(directory) / "events.bin"
            event_file = Path(directory) / "event"
            records = []
            for start_x, start_y, end_x, end_y in ((800, 900, 810, 400), (800, 100, 810, 600),
                                                   (800, 400, 810, 420), (500, 1100, 505, 1102),
                                                   (260, 200, 263, 202), (900, 800, 400, 810),
                                                   (100, 800, 600, 810)):
                for kind, code, value in ((1, 330, 1), (3, 53, start_x), (3, 54, start_y),
                                           (3, 53, end_x), (3, 54, end_y), (1, 330, 0)):
                    records.append(struct.pack("<IIHHi", 0, 0, kind, code, value))
            events.write_bytes(b"".join(records))
            result = subprocess.run(["sh", str(server.ROOT / "calendar_display.sh"), "touch", str(events)],
                                    env={**os.environ, "CALENDAR_EVENTFILE": str(event_file)},
                                    capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout.splitlines(), ["prev", "next", "tap:1228:810",
                                                       "tap:546:505", "tap:1446:263", "later", "earlier"])
            self.assertEqual([file.read_text().strip() for file in sorted(Path(directory).glob("event.[0-9]*"))],
                             result.stdout.splitlines())


if __name__ == "__main__":
    unittest.main()
