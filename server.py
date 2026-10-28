#!/usr/bin/env python3
"""Calendar editor and full-screen image for a Paperwhite 11th generation."""
import calendar
import io
import json
import os
import re
import sys
import tempfile
import threading
import uuid
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from PIL import Image, ImageDraw, ImageFont
from calendar_feed import Feed, events_by_day, valid_google_feed

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "reminders.json"
PROJECTS = ROOT / "projects.json"
CALENDAR_FEED = ROOT / "calendar_feed.json"
LOCK = threading.Lock()
FONT = "/System/Library/Fonts/Supplemental/Arial.ttf"
BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
CLOCK_FONT = "/System/Library/Fonts/Avenir Next.ttc"
MONTH_FONT = CLOCK_FONT
ULTRA_FONT = ROOT / "fonts" / "Ultra-Regular.ttf"


def read_items(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def save_items(path, items):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".reminders-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(items, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def draw_clock(today=None, clock_time=None, projects=()):
    today = today or date.today()
    clock_time = clock_time or datetime.now().strftime("%H:%M")
    image = Image.new("L", (1648, 1236), 255)
    draw = ImageDraw.Draw(image)
    size = 600
    while True:
        font = ImageFont.truetype(ULTRA_FONT, size)
        box = draw.textbbox((0, 0), clock_time, font=font)
        if box[2] - box[0] <= 1500:
            break
        size -= 10
    draw.text(((1648 - (box[2] - box[0])) / 2 - box[0], 304 - box[1]),
              clock_time, font=font, fill=0)
    label = today.strftime("%A · %-d %B %Y").upper()
    date_font = ImageFont.truetype(CLOCK_FONT, 55, index=7)
    date_box = draw.textbbox((0, 0), label, font=date_font)
    draw.text(((1648 - (date_box[2] - date_box[0])) / 2 - date_box[0], 880 - date_box[1]),
              label, font=date_font, fill=70)
    projects = list(projects)
    if projects:
        available_width, row_gap = 1488, 10
        for size in range(42, 16, -2):
            pill_font = ImageFont.truetype(CLOCK_FONT, size, index=7)
            pill_height = max(46, size + 26)
            rows, row_widths = [[]], [0]
            for project in projects:
                name = project["name"]
                box = draw.textbbox((0, 0), name, font=pill_font)
                width = box[2] - box[0] + 40
                if row_widths[-1] and row_widths[-1] + 20 + width > available_width:
                    rows.append([])
                    row_widths.append(0)
                rows[-1].append((name, box, width))
                row_widths[-1] += (20 if len(rows[-1]) > 1 else 0) + width
            if len(rows) * pill_height + (len(rows) - 1) * row_gap <= 286:
                break

        for row_index, row in enumerate(rows):
            row_width = sum(item[2] for item in row) + 20 * (len(row) - 1)
            left = (1648 - row_width) / 2
            top = 950 + row_index * (pill_height + row_gap)
            for name, box, width in row:
                draw.rounded_rectangle((left, top, left + width, top + pill_height),
                                       radius=11, outline=0, width=3)
                text_height = box[3] - box[1]
                draw.text((left + 20 - box[0], top + (pill_height - text_height) / 2 - box[1]),
                          name, font=pill_font, fill=0)
                left += width + 20
    return image


def fit_text(draw, text, font, width):
    if draw.textbbox((0, 0), text, font=font)[2] <= width:
        return text
    while text and draw.textbbox((0, 0), text + "…", font=font)[2] > width:
        text = text[:-1]
    return text + "…"


def draw_view_toggle(draw, view, left):
    font = ImageFont.truetype(BOLD, 25)
    for label, x in (("MONTH", left), ("WEEK", left + 124)):
        selected = view == label.lower()
        draw.rounded_rectangle((x, 87, x + 116, 147), radius=11,
                               fill=0 if selected else 255, outline=0, width=2)
        box = draw.textbbox((0, 0), label, font=font)
        draw.text((x + 58 - (box[2] - box[0]) / 2, 105), label,
                  font=font, fill=255 if selected else 0)


def event_lanes(entries):
    """Keep overlapping timed events visible in separate columns."""
    groups = []
    group = []
    group_end = -1
    for entry in sorted(entries, key=lambda item: (item["start_minute"], item["end_minute"])):
        if group and entry["start_minute"] >= group_end:
            groups.append(group)
            group = []
        group.append(entry)
        group_end = max(group_end, entry["end_minute"]) if len(group) > 1 else entry["end_minute"]
    if group:
        groups.append(group)
    for group in groups:
        lane_ends = []
        placements = []
        for entry in group:
            lane = next((index for index, end in enumerate(lane_ends) if end <= entry["start_minute"]), None)
            if lane is None:
                lane = len(lane_ends)
                lane_ends.append(0)
            lane_ends[lane] = entry["end_minute"]
            placements.append((entry, lane))
        for entry, lane in placements:
            yield entry, lane, len(lane_ends)


def draw_calendar(today=None, viewed_month=None, clock_time=None, events=None,
                  selected_date=None, feed_error=None):
    today = today or date.today()
    viewed_month = viewed_month or date(today.year, today.month, 1)
    clock_time = clock_time or datetime.now().strftime("%H:%M")
    events = events or {}
    selected_date = selected_date or (today if (today.year, today.month) ==
                                     (viewed_month.year, viewed_month.month) else viewed_month)
    image = Image.new("L", (1648, 1236), 255)
    draw = ImageDraw.Draw(image)
    bold = lambda size: ImageFont.truetype(BOLD, size)
    regular = lambda size: ImageFont.truetype(FONT, size)
    black, gray, pale = 0, 95, 220
    month_font = ImageFont.truetype(MONTH_FONT, 78, index=2)
    draw.text((80, 70), viewed_month.strftime("%B %Y").upper(), font=month_font, fill=black)
    clock_box = draw.textbbox((0, 0), clock_time, font=month_font)
    draw.text((1568 - clock_box[2], 70), clock_time, font=month_font, fill=black)
    draw.line((80, 203, 1568, 203), fill=black, width=4)
    draw_view_toggle(draw, "month", 780)

    left, right, top, bottom = 80, 1030, 350, 1160
    cell_width, cell_height = (right - left) / 7, (bottom - top) / 6
    for column, label in enumerate(("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT")):
        label_font = bold(32)
        label_box = draw.textbbox((0, 0), label, font=label_font)
        draw.text((left + (column + .5) * cell_width - (label_box[2] - label_box[0]) / 2,
                   283), label, font=label_font, fill=black)
    first_weekday, days = calendar.monthrange(viewed_month.year, viewed_month.month)
    offset = (first_weekday + 1) % 7
    for row in range(6):
        for column in range(7):
            x0, y0 = round(left + column * cell_width), round(top + row * cell_height)
            x1, y1 = round(left + (column + 1) * cell_width), round(top + (row + 1) * cell_height)
            day = row * 7 + column - offset + 1
            active = 1 <= day <= days
            cell_date = date(viewed_month.year, viewed_month.month, day) if active else None
            is_today = cell_date == today
            is_selected = cell_date == selected_date
            if is_selected:
                draw.rectangle((x0 + 3, y0 + 3, x1 - 3, y1 - 3), fill=black)
            draw.rectangle((x0, y0, x1, y1), outline=gray if active else pale, width=2)
            if is_today and not is_selected:
                draw.rectangle((x0 + 5, y0 + 5, x1 - 5, y1 - 5), outline=gray, width=5)
            if active:
                ink = 255 if is_selected else black
                draw.text((x0 + 12, y0 + 10), str(day), font=bold(43), fill=ink)
                count = len(events.get(cell_date.isoformat(), []))
                # shortcut: 16 dots fit a square; a plus marks overflow until a denser layout is needed.
                for dot in range(min(count, 16)):
                    dx = x0 + 14 + (dot % 8) * 14
                    dy = y1 - 33 + (dot // 8) * 13
                    draw.ellipse((dx, dy, dx + 8, dy + 8), fill=ink)
                if count > 16:
                    draw.text((x1 - 36, y1 - 39), "+", font=bold(20), fill=ink)
            else:
                adjacent = viewed_month.toordinal() + day - 1
                draw.text((x0 + 15, y0 + 12), str(date.fromordinal(adjacent).day),
                          font=regular(38), fill=180)

    draw.line((1080, 250, 1080, 1160), fill=gray, width=2)
    heading = selected_date.strftime("%a %-d %b").upper()
    draw.text((1120, 275), heading, font=ImageFont.truetype(MONTH_FONT, 50, index=2), fill=black)
    selected_events = events.get(selected_date.isoformat(), [])
    if feed_error and not selected_events:
        draw.text((1120, 370), "Calendar unavailable", font=regular(29), fill=gray)
    elif not selected_events:
        draw.text((1120, 370), "No events", font=regular(30), fill=gray)
    for index, item in enumerate(selected_events[:9]):
        y = 368 + index * 76
        draw.text((1120, y), item["time"], font=bold(25), fill=gray)
        title = fit_text(draw, item["title"], regular(31), 440)
        draw.text((1120, y + 29), title, font=regular(31), fill=black)
    if len(selected_events) > 9:
        draw.text((1120, 1135), f"+{len(selected_events) - 9} more", font=regular(25), fill=gray)
    return image


def draw_week(today=None, selected_date=None, clock_time=None, events=None,
              week_offset=0, hour=7):
    today = today or date.today()
    selected_date = selected_date or today
    clock_time = clock_time or datetime.now().strftime("%H:%M")
    events = events or {}
    week_start = selected_date - timedelta(days=(selected_date.weekday() + 1) % 7) + timedelta(weeks=week_offset)
    image = Image.new("L", (1648, 1236), 255)
    draw = ImageDraw.Draw(image)
    bold = ImageFont.truetype(BOLD, 27)
    regular = ImageFont.truetype(FONT, 22)
    title_font = ImageFont.truetype(MONTH_FONT, 78, index=2)
    draw.text((80, 70), week_start.strftime("%d %b").upper() + " – " +
              (week_start + timedelta(days=6)).strftime("%d %b %Y").upper(),
              font=title_font, fill=0)
    clock_box = draw.textbbox((0, 0), clock_time, font=title_font)
    draw.text((1568 - clock_box[2], 70), clock_time, font=title_font, fill=0)
    draw.line((80, 203, 1568, 203), fill=0, width=4)
    draw_view_toggle(draw, "week", 1000)
    left, right, top, bottom = 166, 1568, 425, 1160
    col_width = (right - left) / 7
    hour_height = (bottom - top) / 12
    for column in range(7):
        day = week_start + timedelta(days=column)
        x0 = round(left + column * col_width)
        x1 = round(left + (column + 1) * col_width)
        if day == selected_date + timedelta(weeks=week_offset):
            draw.rectangle((x0, 305, x1, 353), fill=0)
        elif day == today:
            draw.rectangle((x0 + 3, 308, x1 - 3, 350), outline=95, width=3)
        ink = 255 if day == selected_date + timedelta(weeks=week_offset) else 0
        draw.text((x0 + 14, 311), day.strftime("%a %-d").upper(), font=bold, fill=ink)
        draw.rectangle((x0, 360, x1, 410), outline=180, width=1)
        all_day = [item for item in events.get(day.isoformat(), []) if item["all_day"]]
        if all_day:
            draw.text((x0 + 5, 369), fit_text(draw, all_day[0]["title"], regular, col_width - 10),
                      font=regular, fill=0)
            if len(all_day) > 1:
                draw.text((x1 - 38, 388), f"+{len(all_day)-1}", font=regular, fill=0)
        draw.line((x0, top, x0, bottom), fill=160, width=1)
    draw.line((right, top, right, bottom), fill=160, width=1)
    for row in range(13):
        y = round(top + row * hour_height)
        draw.line((left, y, right, y), fill=180, width=1)
        if row < 12:
            draw.text((80, y + 2), f"{hour + row:02d}:00", font=regular, fill=95)
    for column in range(7):
        day = week_start + timedelta(days=column)
        x0 = round(left + column * col_width)
        timed = [item for item in events.get(day.isoformat(), []) if not item["all_day"]]
        for item, lane, lanes in event_lanes(timed):
            start = max(item["start_minute"], hour * 60)
            end = min(item["end_minute"], (hour + 12) * 60)
            if end <= start:
                continue
            lane_width = col_width / lanes
            left_edge = round(x0 + lane * lane_width) + 3
            right_edge = round(x0 + (lane + 1) * lane_width) - 3
            y0 = round(top + (start - hour * 60) * hour_height / 60)
            y1 = round(top + (end - hour * 60) * hour_height / 60)
            draw.rectangle((left_edge, y0 + 1, right_edge, max(y0 + 21, y1 - 1)),
                           fill=235, outline=0, width=2)
            draw.text((left_edge + 4, y0 + 3), fit_text(draw, item["title"], regular, lane_width - 14),
                      font=regular, fill=0)
    return image


def make_server(host="", port=8000, data_path=DATA, projects_path=PROJECTS, feed_path=CALENDAR_FEED):
    feed = Feed(feed_path)

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, body=b"", content_type="application/json"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/":
                self.reply(200, (ROOT / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif self.path == "/api/reminders":
                with LOCK:
                    items = read_items(data_path)
                self.reply(200, json.dumps(items, ensure_ascii=False).encode())
            elif self.path == "/api/projects":
                with LOCK:
                    items = read_items(projects_path)
                self.reply(200, json.dumps(items, ensure_ascii=False).encode())
            elif self.path == "/api/calendar":
                self.reply(200, json.dumps(feed.status()).encode())
            elif urlsplit(self.path).path == "/api/calendar/events":
                query = parse_qs(urlsplit(self.path).query)
                try:
                    first = date.fromisoformat(query["start"][0])
                    last = date.fromisoformat(query["end"][0])
                    if not 0 < (last - first).days <= 42:
                        raise ValueError
                except (ValueError, KeyError, IndexError):
                    self.reply(400, b'{"error":"Use start and end dates, up to 42 days apart."}')
                    return
                days = events_by_day(feed.get(), first, last)
                self.reply(200, json.dumps({"days": days, **feed.status()}, ensure_ascii=False).encode())
            elif urlsplit(self.path).path in {"/display.png", "/preview.png", "/clock.png", "/clock-preview.png"}:
                path = urlsplit(self.path).path
                query = parse_qs(urlsplit(self.path).query)
                raw_month = query.get("month")
                if raw_month and (len(raw_month) != 1 or not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", raw_month[0])):
                    self.reply(400, b'{"error":"Use month=YYYY-MM."}')
                    return
                try:
                    viewed_month = date.fromisoformat(raw_month[0] + "-01") if raw_month else None
                except ValueError:
                    self.reply(400, b'{"error":"Use month=YYYY-MM."}')
                    return
                viewed_month = viewed_month or date.today().replace(day=1)
                try:
                    selected = date.fromisoformat(query["day"][0]) if "day" in query else None
                    if selected is None and "tap" in query:
                        x, y = (int(part) for part in query["tap"][0].split(","))
                        if 80 <= x < 1030 and 350 <= y < 1160:
                            column = (x - 80) * 7 // 950
                            row = (y - 350) * 6 // 810
                            first_weekday = (viewed_month.weekday() + 1) % 7
                            day_number = row * 7 + column - first_weekday + 1
                            if 1 <= day_number <= calendar.monthrange(viewed_month.year, viewed_month.month)[1]:
                                selected = viewed_month.replace(day=day_number)
                    selected = selected or (date.today() if viewed_month.year == date.today().year and
                                            viewed_month.month == date.today().month else viewed_month)
                    view = query.get("view", ["month"])[0]
                    week_offset = int(query.get("week", ["0"])[0])
                    hour = int(query.get("hour", ["7"])[0])
                    if view not in {"month", "week"} or not -520 <= week_offset <= 520 or not 0 <= hour <= 12:
                        raise ValueError
                except (ValueError, IndexError):
                    self.reply(400, b'{"error":"Invalid calendar selection."}')
                    return
                output = io.BytesIO()
                if path.startswith("/clock"):
                    with LOCK:
                        projects = read_items(projects_path)
                    image = draw_clock(projects=projects)
                else:
                    if view == "week":
                        first = selected - timedelta(days=(selected.weekday() + 1) % 7) + timedelta(weeks=week_offset)
                        last = first + timedelta(days=7)
                    else:
                        first = viewed_month
                        last = (viewed_month.replace(day=28) + timedelta(days=4)).replace(day=1)
                    events = events_by_day(feed.get(), first, last)
                    image = (draw_week(selected_date=selected, events=events, week_offset=week_offset,
                                       hour=hour) if view == "week" else
                             draw_calendar(viewed_month=viewed_month, selected_date=selected,
                                           events=events, feed_error=feed.error))
                if path in {"/display.png", "/clock.png"}:
                    image = image.transpose(Image.Transpose.ROTATE_90)
                image.save(output, format="PNG", optimize=True)
                self.reply(200, output.getvalue(), "image/png")
            else:
                self.send_error(404)

        def do_POST(self):
            if self.path not in {"/api/reminders", "/api/projects", "/api/calendar"}:
                self.send_error(404)
                return
            if self.headers.get("Content-Type", "").split(";", 1)[0].lower() != "application/json":
                self.reply(415, b'{"error":"Use application/json."}')
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 8192:
                    raise ValueError("Invalid request length")
                payload = json.loads(self.rfile.read(length))
                if self.path == "/api/calendar":
                    url = payload["url"].strip()
                    if not valid_google_feed(url):
                        raise ValueError("Use a private Google iCal URL")
                    with LOCK:
                        save_items(feed_path, {"url": url})
                    feed.reset()
                    self.reply(204)
                    return
                if self.path == "/api/projects":
                    name = payload["name"].strip()
                    if not 0 < len(name) <= 30:
                        raise ValueError("Project name must be 1 to 30 characters")
                    with LOCK:
                        items = read_items(projects_path)
                        if len(items) >= 9 or any(item["name"].casefold() == name.casefold() for item in items):
                            raise ValueError("Project list is full or name already exists")
                        item = {"id": uuid.uuid4().hex, "name": name}
                        items.append(item)
                        save_items(projects_path, items)
                    self.reply(201, json.dumps(item, ensure_ascii=False).encode())
                    return
                reminder_date = date.fromisoformat(payload["date"])
                note = payload["text"].strip()
                if not 0 < len(note) <= 200:
                    raise ValueError("Text must be 1 to 200 characters")
                item = {"id": uuid.uuid4().hex, "date": reminder_date.isoformat(), "text": note}
            except (ValueError, KeyError, TypeError, AttributeError, UnicodeDecodeError):
                error = (b'{"error":"Enter a private Google iCal address."}' if self.path == "/api/calendar" else
                         b'{"error":"Enter a unique project name of 1-30 characters (maximum 9)."}'
                         if self.path == "/api/projects" else
                         b'{"error":"Enter a valid date and 1-200 characters of text."}')
                self.reply(400, error)
                return
            with LOCK:
                items = read_items(data_path)
                items.append(item)
                save_items(data_path, items)
            self.reply(201, json.dumps(item, ensure_ascii=False).encode())

        def do_DELETE(self):
            if self.path == "/api/calendar":
                with LOCK:
                    try:
                        Path(feed_path).unlink()
                    except FileNotFoundError:
                        pass
                feed.reset()
                self.reply(204)
                return
            if not self.path.startswith(("/api/reminders/", "/api/projects/")):
                self.send_error(404)
                return
            wanted = self.path.rsplit("/", 1)[-1]
            is_project = self.path.startswith("/api/projects/")
            path = projects_path if is_project else data_path
            with LOCK:
                items = read_items(path)
                remaining = [item for item in items if item["id"] != wanted]
                if len(items) == len(remaining):
                    self.send_error(404)
                    return
                save_items(path, remaining)
            self.reply(204)

        def log_message(self, *_):
            pass

    return ThreadingHTTPServer((host, port), Handler)


def main():
    try:
        port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
        with make_server("", port) as httpd:
            print(f"Kindle calendar running on port {port}", flush=True)
            httpd.serve_forever()
    except (OSError, ValueError) as error:
        print(f"Calendar server could not start: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
