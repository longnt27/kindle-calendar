# Kindle desk clock and calendar

This setup is running on a Kindle Paperwhite 11th generation (firmware 5.19.2) and a Mac mini. The Kindle is already jailbroken, with SH_Integration (shell books) and FBInk installed. The Kindle opens a local shell book, fetches images from the Mac over home Wi-Fi, and paints them directly to the E Ink panel. It does not use the Kindle browser, so no address bar or system time/battery bar appears.

## What it shows

- Idle screen: large Ultra clock, date, and up to nine current-project pills. Add and remove project names in the web editor; the Kindle refreshes within one minute. Names are limited to 30 characters so they fit the screen.
- Tap: landscape month calendar with an Avenir Next Demi clock in the header. The left grid has square days and one dot per Google Calendar activity. Tap a day to select it and show its events in the right column. The selected day is solid black; today keeps a lighter border when another day is selected.
- Tap **WEEK** beside the title for seven day columns and a 12-hour timeline. Swipe left/right for the next/previous week; swipe vertically to shift the visible hours by three. Tap **MONTH** to return. In month view, swipe left/right for the next/previous month. After 60 seconds without touch, the idle clock returns.
- Both screens update each minute. The Kindle makes a full E Ink refresh when switching screens and about every 30 minutes to clear ghosting.
- The old reminder form is no longer shown in the web editor. Any legacy `reminders.json` file is preserved but is not shown on the Kindle.

## Mac setup

This installation uses `/Users/tlng/projects/kindle-calendar` and `/usr/bin/python3`. Keep that path if using the included LaunchAgent.

1. Put `server.py`, `calendar_feed.py`, `index.html`, `calendar_display.sh`, `requirements.txt`, `com.tlng.kindle-calendar.plist`, and the `fonts/` directory in `/Users/tlng/projects/kindle-calendar`.
2. Install the Python packages with `/usr/bin/python3 -m pip install --user -r /Users/tlng/projects/kindle-calendar/requirements.txt`.
3. Install and start the login service:

   ```sh
   cp /Users/tlng/projects/kindle-calendar/com.tlng.kindle-calendar.plist ~/Library/LaunchAgents/
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tlng.kindle-calendar.plist
   ```

   If the service is already loaded, restart it after code changes with `launchctl kickstart -k gui/$(id -u)/com.tlng.kindle-calendar`.
4. Check `http://127.0.0.1:8000/clock-preview.png` and `http://127.0.0.1:8000/preview.png` on the Mac. `http://192.168.59.100:8000/` is the editor on the home network. The existing service gateway links to it at `/go/kindle-calendar`; its tailnet URL is `https://mini.tailc0fd22.ts.net:8445/`.

`projects.json` is created beside `server.py` when project names are saved. Keep it and any legacy `reminders.json` file when updating the app. The Ultra font and its Apache 2.0 license are in `fonts/`.

## Connect Google Calendar (read only)

1. On a computer, open Google Calendar → Settings → choose the calendar → **Integrate calendar**. Copy its **Secret address in iCal format**. [Google's instructions](https://support.google.com/calendar/answer/37648) explain where it appears. Treat the address like a password.
2. Open the Kindle Calendar web editor at `https://mini.tailc0fd22.ts.net:8445/`. Paste that address into **Google Calendar → Private iCal address**, then select **Connect calendar**. The editor shows connection status and the selected day's events.
3. The Mac stores the private address in `calendar_feed.json` beside `server.py`; the editor never displays the address again. The Mac fetches it at most once every five minutes and keeps the last good copy if a refresh fails. The Kindle still fetches its rendered image each minute. To stop syncing, select **Disconnect** in the editor.

The private address gives read access to one Google calendar. The app does not request Google write permission and never edits Google events. The editor and image endpoints are reachable from the home LAN and this Mac's tailnet gateway, so people with access to those networks can see the event details shown here.

## Kindle install or update

The Kindle must already have SH_Integration and `/mnt/us/libkh/bin/fbink`. Its USB drive appears on the Mac as `/Volumes/Kindle`. The shell book is `/mnt/us/documents/calendar_display_v2.sh`; its first lines supply the **Calendar Display 2** library name. The distinct filename forces a fresh library import after an older cached entry kept launching a stale script.

1. If Calendar Display is running, restart the Kindle to return to the library. Connect it by USB and wait for the Kindle drive.
2. Copy the shell book and seed its cached clock image:

   ```sh
   cp /Users/tlng/projects/kindle-calendar/calendar_display.sh /Volumes/Kindle/documents/calendar_display_v2.sh
   curl -fsS http://127.0.0.1:8000/clock.png -o /Volumes/Kindle/.kindle-calendar.png
   sync
   diskutil eject /Volumes/Kindle
   ```

3. Disconnect USB, leave the Kindle on the same Wi-Fi as the Mac, and open **Calendar Display 2** in the Kindle library. Hold the Kindle horizontally, rotated 90° clockwise from portrait. The clock appears after about 10 seconds. A wall charger is fine.
4. Tap for the month grid, tap a date, switch to the week grid, and leave it untouched for one minute to check that the clock returns. The script sets `com.lab126.powerd preventScreenSaver` to `1` for this session.

Changes to the web editor, image layout, project names, and calendar feed only require the Mac service to update; the Kindle fetches fresh images. Changes to `calendar_display.sh` require the USB copy and a full Kindle reboot before reopening **Calendar Display 2**. A running worker keeps its old touch controls even while new images appear. To return to normal Kindle use, restart the Kindle by holding the power button until its restart menu or startup sequence appears.

## If it stops updating

1. Check that the Kindle and Mac are on the home network. From the Mac, open `http://192.168.59.100:8000/clock.png` and `http://192.168.59.100:8000/display.png`.
2. Check the Mac service with `launchctl print gui/$(id -u)/com.tlng.kindle-calendar` and read `/Users/tlng/projects/kindle-calendar/calendar-error.log` for image-rendering errors.
3. If the screen stays on an old image, restart the Kindle and reopen **Calendar Display 2**. The last image is cached on the Kindle, so it can remain visible while Wi-Fi or the Mac is down; its time and date will then be stale.
4. For device-side diagnosis, the worker writes `/tmp/kindle-calendar-worker.log`, the touch reader writes `/tmp/kindle-calendar-touch.log`, and FBInk writes `/tmp/kindle-calendar.log`. These temporary logs require shell access to the jailbroken Kindle and disappear on restart.

The Kindle script expects this Mac at `192.168.59.100:8000` and reads touch events from `/dev/input/event1` with 16-byte Linux input records. Those values were calibrated on this Paperwhite. If the Mac IP or Kindle model changes, update and reinstall the script. The old `TESTD_PREVENT_SCREENSAVER` marker did not disable sleep on this firmware; the working control is `lipc-set-prop com.lab126.powerd preventScreenSaver 1`.
