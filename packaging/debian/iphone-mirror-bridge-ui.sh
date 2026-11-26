#!/bin/sh
PYTHONPATH=/usr/lib/iphone-mirror-bridge/ui${PYTHONPATH:+:$PYTHONPATH} exec /usr/bin/python3 -m iphone_mirror_bridge_ui "$@"
