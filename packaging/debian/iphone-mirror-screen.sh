#!/bin/sh
# Start or stop the AirPlay screen-mirroring receiver (UxPlay) for this user.
#   iphone-mirror-screen          toggle: start if stopped, stop if running
#   iphone-mirror-screen --start | --stop | --status
#   iphone-mirror-screen --allow-firewall   let the iPhone through Wild OS's firewall
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
    if command -v notify-send >/dev/null 2>&1; then
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
    --status) if running; then echo running; else echo stopped; exit 3; fi ;;
    "") if running; then stop; else start; fi ;;
    *) echo "Usage: iphone-mirror-screen [--start|--stop|--status|--allow-firewall]" >&2; exit 2 ;;
esac
