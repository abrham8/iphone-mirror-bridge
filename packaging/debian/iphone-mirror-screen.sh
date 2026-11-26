#!/bin/sh
# Start or stop the AirPlay screen-mirroring receiver (UxPlay) for this user.
#   iphone-mirror-screen          toggle: start if stopped, stop if running
#   iphone-mirror-screen --start | --stop | --status
NAME="iPhone Mirror Bridge"
LOG_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/iphone-mirror-bridge"
LOG="$LOG_DIR/uxplay.log"

notify() {
    if command -v notify-send >/dev/null 2>&1; then
        notify-send -a "iPhone Screen Mirroring" -i iphone-mirror-bridge "$1" "$2" || true
    fi
    echo "$1: $2"
}

running() { pgrep -u "$(id -u)" -x uxplay >/dev/null 2>&1; }

start() {
    if running; then
        notify "Already running" "On the iPhone: Control Center > Screen Mirroring > $NAME"
        return 0
    fi
    if ! command -v uxplay >/dev/null 2>&1; then
        notify "UxPlay is not installed" "Install it with: sudo apt install uxplay"
        return 1
    fi
    mkdir -p "$LOG_DIR"
    # -nh: exact name; -p: fixed ports (TCP 7000,7001,7100 / UDP 6000,6001,7011)
    # -key: keep the same identity so the iPhone recognises this receiver.
    # stdbuf: uxplay buffers output when not on a terminal; keep the log current.
    nohup stdbuf -oL -eL uxplay -n "$NAME" -nh -p -key "$LOG_DIR/uxplay.pem" >"$LOG" 2>&1 &
    sleep 2
    if running; then
        notify "Ready for screen mirroring" "On the iPhone: Control Center > Screen Mirroring > $NAME"
    else
        reason=$(grep -m1 -E "not found|ERROR|[Ff]ailed" "$LOG" 2>/dev/null)
        notify "Could not start screen mirroring" "${reason:-See $LOG} (details: $LOG)"
        return 1
    fi
}

stop() {
    if running; then
        pkill -u "$(id -u)" -x uxplay
        for _ in 1 2 3 4 5 6; do running || break; sleep 0.5; done
        if running; then pkill -KILL -u "$(id -u)" -x uxplay; fi
        notify "Screen mirroring stopped" "Open the app again to restart it."
    else
        notify "Screen mirroring is not running" ""
    fi
}

case "${1:-}" in
    --start) start ;;
    --stop) stop ;;
    --status) if running; then echo running; else echo stopped; exit 3; fi ;;
    "") if running; then stop; else start; fi ;;
    *) echo "Usage: iphone-mirror-screen [--start|--stop|--status]" >&2; exit 2 ;;
esac
