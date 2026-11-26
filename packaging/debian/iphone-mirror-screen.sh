#!/bin/sh
# Start or stop the AirPlay screen-mirroring receiver (UxPlay) for this user.
#   iphone-mirror-screen          toggle: start if stopped, stop if running
#   iphone-mirror-screen --start | --stop | --status
#   iphone-mirror-screen --allow-firewall   let the iPhone through Wild OS's firewall
#   iphone-mirror-screen --firewall-status  print what the firewall still blocks (exit 1 if anything)
# The desktop app sets IPHONE_MIRROR_EMBED_SOCKET (show video in its own
# window via a unix socket), IPHONE_MIRROR_FPS (30 or 60) and
# IPHONE_MIRROR_QUIET=1 (it shows its own notifications).
NAME="iPhone Mirror Bridge"
LOG_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/iphone-mirror-bridge"
LOG="$LOG_DIR/uxplay.log"
# The ports UxPlay uses with -p.
PORTS="7000/tcp 7001/tcp 7100/tcp 6000/udp 6001/udp 7011/udp"
# Wild OS (Kicksecure-based) ships a firewall that drops incoming connections
# except mDNS: the receiver shows up on the iPhone, then "Unable to connect".
# Its rules only apply on networks marked Home. Overridable for the tests.
WILDOS_FIREWALL="${WILDOS_FIREWALL:-/usr/libexec/wildos/settings-firewall}"
WILDOS_STATE="${WILDOS_STATE:-/run/wildos/firewall-state.json}"
WILDOS_CONFIG="${WILDOS_CONFIG:-/etc/wildos/firewall.json}"

notify() {
    if [ -z "$IPHONE_MIRROR_QUIET" ] && command -v notify-send >/dev/null 2>&1; then
        notify-send -a "iPhone Screen Mirroring" -i iphone-mirror-bridge "$1" "$2" || true
    fi
    echo "$1: $2"
}

# Prints what is missing in Wild OS's firewall, one per line:
# "trust UUID" (network not marked Home) or "allow PORT PROTO". Prints nothing
# when there is no such firewall or it already lets the receiver in.
wildos_missing() {
    [ -x "$WILDOS_FIREWALL" ] && [ -r "$WILDOS_STATE" ] || return 0
    python3 -I - "$WILDOS_STATE" "$WILDOS_CONFIG" $PORTS <<'EOF_PY' 2>/dev/null
import json, sys
def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}
state, cfg = load(sys.argv[1]), load(sys.argv[2])
if not state.get("enabled") or not state.get("loaded") or state.get("level") == "maximum":
    sys.exit(0)
for net in state.get("networks", []):
    if net.get("trust") != "home" and net.get("uuid"):
        print("trust", net["uuid"])
have = set()
for r in cfg.get("rules", []):
    if r.get("on", True) is not False and not r.get("until") and r.get("scope") in ("network", "anyone"):
        for proto in (("tcp", "udp") if r.get("proto") == "both" else (r.get("proto"),)):
            have.add("%s/%s" % (r.get("port"), proto))
for p in sys.argv[3:]:
    if p not in have:
        print("allow", *p.split("/"))
EOF_PY
}

allow_firewall() {
    missing=$(wildos_missing)
    if [ -z "$missing" ]; then
        notify "Nothing to change" "The firewall already lets the iPhone connect (or there is no Wild OS firewall)."
        return 0
    fi
    echo "$missing" | while read -r what a b; do
        case "$what" in
            trust) pkexec "$WILDOS_FIREWALL" trust "$a" home ;;
            allow) pkexec "$WILDOS_FIREWALL" allow "iPhone Screen Mirroring" "$a" "$b" network - forever ;;
        esac || exit 1
    done || { notify "Firewall not changed" "The change was cancelled or refused."; return 1; }
    notify "Firewall updated" "Devices on this network can now mirror to $NAME."
}

# Video options. UxPlay's default timestamp sync is kept: with -vsync no each
# frame is shown the moment it arrives, so Wi-Fi jitter makes video choppy.
# -fps 60: the iPhone's full frame rate (UxPlay caps at 30).
# vah264dec (VA-API, the GPU) is what GStreamer picks anyway, but by default it
# holds 6-8 frames (~110 ms) of the iPhone's stream for reordering the iPhone
# never does; compliance=flexible outputs each frame at once. On Wayland,
# waylandsink shows the GPU frames without copying them.
video_args() {
    set -- -fps "${IPHONE_MIRROR_FPS:-60}"
    have=$(python3 -I -c '
import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst
Gst.init(None)
print(*[n for n in ("vah264dec", "waylandsink") if Gst.ElementFactory.find(n)])
' 2>/dev/null)
    case " $have " in *" vah264dec "*) set -- "$@" -vd "vah264dec compliance=flexible" ;; esac
    if [ -n "$IPHONE_MIRROR_EMBED_SOCKET" ]; then
        # Decoded frames go to the desktop app, which draws them in its window.
        # unixfdsink accepts any caps, so the VA-API decoder would hand it GPU
        # (VAMemory/DMABuf) frames that cannot cross the socket ("Internal
        # data stream error", iPhone disconnects). The converter (-vc) is
        # spliced into UxPlay's pipeline verbatim, so a system-memory capsfilter
        # goes there; -vs must stay a single element because UxPlay appends
        # " name=<first word>_h264" to it.
        set -- "$@" -vc "videoconvert ! video/x-raw" -vs "unixfdsink socket-path=$IPHONE_MIRROR_EMBED_SOCKET"
    elif [ -n "$WAYLAND_DISPLAY" ]; then
        case " $have " in *" waylandsink "*) set -- "$@" -vs waylandsink ;; esac
    fi
    printf '%s\n' "$@"
}

# True if a live (not zombie) uxplay runs for this user.
running() {
    for pid in $(pgrep -u "$(id -u)" -x uxplay 2>/dev/null); do
        case "$(ps -o stat= -p "$pid" 2>/dev/null)" in
            Z*|"") ;;
            *) return 0 ;;
        esac
    done
    return 1
}

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
    # IPHONE_MIRROR_VIDEO_ARGS replaces the video options (e.g. "-vsync no"
    # for lower delay at the cost of smooth video).
    if [ -n "${IPHONE_MIRROR_VIDEO_ARGS+x}" ]; then
        # shellcheck disable=SC2086
        set -- $IPHONE_MIRROR_VIDEO_ARGS
    else
        set --
        nl='
'
        oldifs=$IFS; IFS=$nl
        # shellcheck disable=SC2046
        set -- $(video_args)
        IFS=$oldifs
    fi
    nohup stdbuf -oL -eL uxplay -n "$NAME" -nh -p -key "$LOG_DIR/uxplay.pem" "$@" >"$LOG" 2>&1 &
    sleep 2
    if running; then
        notify "Ready for screen mirroring" "On the iPhone: Control Center > Screen Mirroring > $NAME"
        if [ -n "$(wildos_missing)" ]; then
            notify "The firewall will block the iPhone" "Choose 'Allow through the firewall' (right-click the app) or run: iphone-mirror-screen --allow-firewall"
        fi
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
    --allow-firewall) allow_firewall ;;
    --firewall-status) missing=$(wildos_missing); [ -n "$missing" ] && { echo "$missing"; exit 1; }; echo ok ;;
    --status) if running; then echo running; else echo stopped; exit 3; fi ;;
    "") if running; then stop; else start; fi ;;
    *) echo "Usage: iphone-mirror-screen [--start|--stop|--status|--allow-firewall|--firewall-status]" >&2; exit 2 ;;
esac
