#!/bin/sh
# Name: Calendar Display 2
# Author: Local
# DontUseFBInk
# shortcut: restart the Kindle to leave the full-screen display.

CACHE=/mnt/us/.kindle-calendar.png
NEXT=/tmp/kindle-calendar-next.png
FBINK=/mnt/us/libkh/bin/fbink
URL=http://192.168.59.100:8000
PIDFILE=/tmp/kindle-calendar.pid
EVENTFILE=${CALENDAR_EVENTFILE:-/tmp/kindle-calendar-event}

paint() {
    if test "$1" = full; then
        "$FBINK" -cf -i "$CACHE" >/tmp/kindle-calendar.log 2>&1 || eips -g "$CACHE" -f
    else
        "$FBINK" -i "$CACHE" >/tmp/kindle-calendar.log 2>&1 || eips -g "$CACHE"
    fi
}

read_touch() {
    device=${2:-/dev/input/event1}
    sequence=0
    exec 3<"$device" || return 1
    while :; do
        set -- $(dd bs=16 count=1 2>/dev/null <&3 |
            hexdump -v -e '8/1 "%02x" " " 1/2 "%u" " " 1/2 "%u" " " 1/4 "%d" "\n"')
        test "$#" -eq 4 || break
        type=$2 code=$3 value=$4
        if test "$type" -eq 1 && test "$code" -eq 330; then
            if test "$value" -eq 1; then
                start_x= start_y= end_x= end_y=
            elif test -n "$start_x" && test -n "$start_y" &&
                 test -n "$end_x" && test -n "$end_y"; then
                dx=$((end_x - start_x))
                dy=$((end_y - start_y))
                abs_x=$dx abs_y=$dy
                test "$abs_x" -lt 0 && abs_x=$((-abs_x))
                test "$abs_y" -lt 0 && abs_y=$((-abs_y))
                action=
                if test "$abs_x" -lt 80 && test "$abs_y" -lt 80; then
                    action=tap:$((1648 - end_y)):$end_x
                elif test "$abs_x" -lt 300 && test "$dy" -gt 250; then
                    action=next
                elif test "$abs_x" -lt 300 && test "$dy" -lt -250; then
                    action=prev
                elif test "$abs_y" -lt 300 && test "$dx" -gt 250; then
                    action=earlier
                elif test "$abs_y" -lt 300 && test "$dx" -lt -250; then
                    action=later
                fi
                if test -n "$action"; then
                    sequence=$((sequence + 1))
                    name=$(printf '%s.%06d' "$EVENTFILE" "$sequence")
                    echo "$action" >"$EVENTFILE.next"
                    mv "$EVENTFILE.next" "$name"
                    echo "$action"
                fi
            fi
        elif test "$type" -eq 3 && test "$code" -eq 53; then
            test -n "$start_x" || start_x=$value
            end_x=$value
        elif test "$type" -eq 3 && test "$code" -eq 54; then
            test -n "$start_y" || start_y=$value
            end_y=$value
        fi
    done
}

if test "$1" = touch; then
    read_touch "$@"
    exit
fi

if test "$1" != worker; then
    lipc-set-prop com.lab126.powerd preventScreenSaver 1 || exit 1
    if test -f "$PIDFILE"; then
        old_pid=$(cat "$PIDFILE")
        case "$old_pid" in
            ''|*[!0-9]*) ;;
            *) if test -r "/proc/$old_pid/cmdline" &&
                  tr '\000' ' ' <"/proc/$old_pid/cmdline" | grep -q 'calendar_display.*sh worker'; then
                   kill "$old_pid" 2>/dev/null
                   sleep 1
               fi ;;
        esac
    fi
    nohup sh "$0" worker >/tmp/kindle-calendar-worker.log 2>&1 </dev/null &
    echo $! >"$PIDFILE"
    exit 0
fi

restore() {
    test -n "$touch_pid" && kill "$touch_pid" 2>/dev/null
    /sbin/initctl start lab126_gui >/dev/null 2>&1
    rm "$PIDFILE" 2>/dev/null
}
trap restore EXIT
sleep 8
# pillow drives the panel after the Kindle UI stops on this Paperwhite 5.
trap '' TERM
/sbin/initctl stop lab126_gui || exit 1
/sbin/initctl start pillow || exit 1
trap - TERM
sleep 2
test -s "$CACHE" && paint full
last_flash=$(date +%s)

rm "$EVENTFILE".[0-9]* "$EVENTFILE.next" 2>/dev/null
sh "$0" touch >/tmp/kindle-calendar-touch.log 2>&1 &
touch_pid=$!
mode=clock
view=month
offset=0
week_offset=0
hour=7
selection=
last_touch=$(date +%s)
last_mode=
last_query=
last_stamp=
while :; do
    for event in "$EVENTFILE".[0-9]*; do
        test -f "$event" || continue
        action=$(cat "$event")
        rm "$event"
        if test "$mode" = clock; then
            case "$action" in
                tap:*) mode=calendar; view=month; offset=0; week_offset=0; hour=7; selection=
                       last_touch=$(date +%s) ;;
            esac
        else
            case "$action" in
                tap:*)
                    coordinates=${action#tap:}
                    x=${coordinates%%:*}
                    y=${coordinates#*:}
                    if test "$x" -ge 780 && test "$x" -le 1240 &&
                       test "$y" -ge 75 && test "$y" -le 165; then
                        if test "$view" = month; then view=week; else view=month; fi
                        week_offset=0
                    elif test "$view" = month && test "$x" -ge 80 && test "$x" -lt 1030 &&
                         test "$y" -ge 350 && test "$y" -lt 1160; then
                        selection=$x,$y
                    fi ;;
                next) if test "$view" = week; then week_offset=$((week_offset + 1));
                      else offset=$((offset + 1)); selection=; fi ;;
                prev) if test "$view" = week; then week_offset=$((week_offset - 1));
                      else offset=$((offset - 1)); selection=; fi ;;
                earlier) if test "$view" = week && test "$hour" -gt 0; then hour=$((hour - 3));
                         test "$hour" -lt 0 && hour=0; fi ;;
                later) if test "$view" = week && test "$hour" -lt 12; then hour=$((hour + 3));
                       test "$hour" -gt 12 && hour=12; fi ;;
            esac
            last_touch=$(date +%s)
        fi
    done
    now=$(date +%s)
    if test "$mode" = calendar && test "$((now - last_touch))" -ge 60; then
        mode=clock
    fi
    stamp=$(date +%Y%m%d%H%M)
    if test "$mode" = clock; then
        image_url=$URL/clock.png
        query=clock
    else
        year=$(date +%Y)
        month=$(date +%m)
        total=$((year * 12 + month - 1 + offset))
        view_year=$((total / 12))
        view_month=$((total % 12 + 1))
        viewed_month=$(printf '%04d-%02d' "$view_year" "$view_month")
        query="month=$viewed_month&view=$view&week=$week_offset&hour=$hour"
        test -n "$selection" && query="$query&tap=$selection"
        image_url=$URL/display.png?$query
    fi
    if test "$mode" != "$last_mode" || test "$query" != "$last_query" || test "$stamp" != "$last_stamp"; then
        if wget -q -T 20 -O "$NEXT" "$image_url" && test -s "$NEXT"; then
            if ! cmp -s "$NEXT" "$CACHE"; then
                cp "$NEXT" "$CACHE" || exit 1
                if test "$mode" != "$last_mode" || test "$((now - last_flash))" -ge 1800; then
                    paint full || exit 1
                    last_flash=$now
                else
                    paint || exit 1
                fi
            fi
            last_mode=$mode
            last_query=$query
            last_stamp=$stamp
        fi
    fi
    sleep 1
done
