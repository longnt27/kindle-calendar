# Kindle Calendar

A full-screen clock and calendar for a jailbroken Kindle Paperwhite. A small web editor on the Mac manages current projects and a read-only Google Calendar feed. The Mac renders grayscale images; the Kindle downloads and paints them directly to the screen, so there is no browser bar or Kindle status bar.

For model-specific installation notes, see [KINDLE_SETUP.md](KINDLE_SETUP.md).

## What it does

- **Clock:** shows the current time, date, and up to nine project pills. Pills size to their text and wrap across the screen. Add projects in the web editor; click a pill there to remove it. Kindle clock pills are display-only.
- **Month view:** tap the clock to open a landscape month grid. Dots show each day's Google Calendar events. Tap a day to highlight it and show its events in the right column; swipe left or right to change months.
- **Week view:** tap **WEEK** for a seven-day hourly grid. Swipe left or right to change weeks and up or down to move through the hours. Tap **MONTH** to return.
- **Automatic refresh:** images refresh every minute. The calendar returns to the clock after 60 seconds without touch. The Kindle prevents sleep while the display script is running.
- **Google Calendar:** connects to one calendar through its private iCal feed. The app only reads events; it never changes Google Calendar.

## Requirements

- A Mac running the included LaunchAgent and Python 3 with the packages in `requirements.txt`.
- A jailbroken Kindle Paperwhite 11th generation running firmware 5.19.2, with SH_Integration and FBInk installed.
- The Mac and Kindle on the same home Wi-Fi network. This setup uses `192.168.59.100:8000` for the Kindle's image requests.

## Run the Mac service

The LaunchAgent expects this project at `/Users/tlng/projects/kindle-calendar` and uses `/usr/bin/python3`.

```sh
/usr/bin/python3 -m pip install --user -r /Users/tlng/projects/kindle-calendar/requirements.txt
cp /Users/tlng/projects/kindle-calendar/com.tlng.kindle-calendar.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tlng.kindle-calendar.plist
```

If the LaunchAgent is already loaded, restart it after changing server or page code:

```sh
launchctl kickstart -k gui/$(id -u)/com.tlng.kindle-calendar
```

Open the editor at [http://127.0.0.1:8000/](http://127.0.0.1:8000/) on the Mac or [http://192.168.59.100:8000/](http://192.168.59.100:8000/) on the home network. Clock and calendar previews are at `/clock-preview.png`, `/preview.png`, and `/preview.png?view=week`.

The existing service gateway also exposes the editor at `/go/kindle-calendar` and [https://mini.tailc0fd22.ts.net:8445/](https://mini.tailc0fd22.ts.net:8445/).

## Install or update the Kindle

The shell book is installed as `/mnt/us/documents/calendar_display_v2.sh`. Copying the current script to a Kindle that is already running the display requires restarting the Kindle first.

1. Connect the Kindle by USB and wait for its drive at `/Volumes/Kindle`.
2. Copy the script and seed its clock image:

   ```sh
   cp /Users/tlng/projects/kindle-calendar/calendar_display.sh /Volumes/Kindle/documents/calendar_display_v2.sh
   curl -fsS http://127.0.0.1:8000/clock.png -o /Volumes/Kindle/.kindle-calendar.png
   sync
   diskutil eject /Volumes/Kindle
   ```

3. Disconnect USB, keep the Kindle on the Mac's Wi-Fi, and open **Calendar Display 2** from the Kindle library. Hold it horizontally, rotated 90° clockwise from portrait.

The Kindle needs a full reboot after updating `calendar_display.sh`. Web editor, server, and image layout changes take effect through the Mac service; the Kindle fetches updated images automatically.

## Connect Google Calendar

In Google Calendar, open **Settings → [calendar] → Integrate calendar** and copy **Secret address in iCal format**. Paste it into the editor under Google Calendar and select **Connect**. Select **Disconnect** to stop syncing.

Treat the private iCal address like a password. It is stored in `calendar_feed.json` beside `server.py`; the editor does not show it after saving. The Mac checks for updates at most every five minutes and keeps the last good event list if a fetch fails. The Kindle image refreshes each minute.

The editor and image server can be reached by devices on the home network and through the configured gateway. People with access to those networks can see the displayed calendar details.

## Data and troubleshooting

- `projects.json` stores project pills; `calendar_feed.json` stores the private feed address. Keep both when updating the app and back them up. Any legacy `reminders.json` is preserved but unused.
- Check the service with `launchctl print gui/$(id -u)/com.tlng.kindle-calendar`. Server errors are written to `calendar-error.log`.
- If the Kindle stops updating, confirm both devices are on the same Wi-Fi, then open `http://192.168.59.100:8000/clock.png` on the Mac. Restart the Kindle and reopen **Calendar Display 2** if the screen remains stale.
- The Kindle caches the last image, so it can remain visible while the Mac or Wi-Fi is unavailable. Its displayed time will then be stale.

The included Ultra font and its Apache 2.0 license are in `fonts/`.
