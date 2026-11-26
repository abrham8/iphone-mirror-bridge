import QtQuick
import QtQuick.Window
import IPhoneMirrorBridge 1.0

// "01 · Desktop – Mirroring": floating toolbar pill above the device frame,
// the iPhone's screen inside. Click the screen to control the iPhone with the
// touchpad and keyboard; Ctrl+F8 gives them back.
Window {
    id: win
    title: "iPhone Mirror Bridge"
    color: "transparent"
    flags: Qt.Window | Qt.FramelessWindowHint | (prefs.onTop ? Qt.WindowStaysOnTopHint : 0)

    readonly property real s: prefs.windowSize === "small" ? 0.8 : prefs.windowSize === "large" ? 1.2 : 1.0
    readonly property bool landscape: receiver.landscape
    readonly property bool framed: prefs.deviceFrame
    // Device frame 330x700 in the design (portrait); swapped in landscape.
    readonly property real frameW: (landscape ? 700 : 330) * s
    readonly property real frameH: (landscape ? 330 : 700) * s
    readonly property real margin: 40

    readonly property real fixedW: frameW + margin * 2
    readonly property real fixedH: frameH + 42 * s + margin * 2
    width: fixedW; height: fixedH
    minimumWidth: fixedW; maximumWidth: fixedW
    minimumHeight: fixedH; maximumHeight: fixedH

    property bool dimmed: false
    property bool showLiveCard: false

    onActiveChanged: if (!active) bridge.releaseKeys()

    Connections {
        target: receiver
        function onStreamingChanged() {
            if (receiver.streaming) {
                win.showLiveCard = true
                liveCardTimer.restart()
            } else {
                video.clear()
            }
        }
        function onFrameReady(image) { video.setFrame(image) }
    }
    Timer { id: liveCardTimer; interval: 3500; onTriggered: win.showLiveCard = false }

    // "Hide when inactive": fade after 10 s without the pointer over the window.
    Timer {
        id: idleTimer
        interval: 10000
        running: prefs.hideInactive && !hover.hovered
        onTriggered: win.dimmed = true
    }
    HoverHandler { id: hover; onHoveredChanged: if (hovered) win.dimmed = false }

    Item {
        id: root
        anchors.fill: parent
        anchors.margins: win.margin
        opacity: win.dimmed && prefs.hideInactive ? 0.35 : 1
        Behavior on opacity { NumberAnimation { duration: 400 } }
        focus: true

        Keys.onPressed: (event) => {
            if (!event.isAutoRepeat) bridge.key(event.nativeScanCode - 8, true)
            event.accepted = true
        }
        Keys.onReleased: (event) => {
            if (!event.isAutoRepeat) bridge.key(event.nativeScanCode - 8, false)
            event.accepted = true
        }

        Column {
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: 10 * win.s

            // -- "Window Controls" pill ------------------------------------------
            Rectangle {
                id: pill
                anchors.horizontalCenter: parent.horizontalCenter
                height: 32
                width: pillRow.implicitWidth + 12 + 14
                radius: height / 2
                color: Theme.pillBg
                border.color: Theme.hairline

                DragHandler {
                    target: null
                    onActiveChanged: if (active) win.startSystemMove()
                }

                Row {
                    id: pillRow
                    x: 12
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 10
                    Image {
                        anchors.verticalCenter: parent.verticalCenter
                        width: 8; height: 8
                        source: Theme.asset(receiver.streaming ? "live-dot.svg" : "dot-offline.svg")
                    }
                    Label2 {
                        anchors.verticalCenter: parent.verticalCenter
                        // The design reads "App · Device"; the full app name plus a
                        // device name outgrows the frame, so show the device once known.
                        text: bridge.deviceName !== "" ? bridge.deviceName : "iPhone Mirror Bridge"
                        elide: Text.ElideRight
                        width: Math.min(implicitWidth, 150)
                        weight: 600
                        font.pixelSize: 12
                    }
                    Rectangle {
                        anchors.verticalCenter: parent.verticalCenter
                        width: 1; height: 14
                        color: Qt.rgba(1, 1, 1, 0.2)
                    }
                    IconButton {
                        icon: "icon-home.svg"; tip: "Home"
                        enabled2: bridge.connected && bridge.keyboard
                        onClicked: bridge.home()
                    }
                    IconButton {
                        icon: "icon-apps.svg"; tip: "App Switcher"
                        enabled2: bridge.connected && bridge.keyboard
                        onClicked: bridge.appSwitcher()
                    }
                    IconButton {
                        icon: "icon-pin.svg"; tip: prefs.onTop ? "Stop keeping on top" : "Keep on top"
                        highlighted: prefs.onTop
                        onClicked: prefs.set("onTop", !prefs.onTop)
                    }
                    IconButton { icon: "icon-settings.svg"; tip: "Settings"; onClicked: app.showSettings("display") }
                    IconButton {
                        icon: "icon-close.svg"; tip: "Stop mirroring and close"
                        onClicked: { receiver.stop(); bridge.setActive(false); win.hide() }
                    }
                }
            }

            // -- "Device Frame" ------------------------------------------------------
            Item {
                anchors.horizontalCenter: parent.horizontalCenter
                width: win.frameW
                height: win.frameH

                Panel {
                    anchors.fill: parent
                    visible: win.framed
                    color: Theme.frameBg
                    radius: 52 * win.s
                    borderColor: Theme.frameBorder
                    borderWidth: 1.5
                    shadowY: 30
                    shadowBlur: 60
                    shadowOpacity: 0.45
                }

                // "Screen": live video, or the design's gradient while waiting.
                Item {
                    id: screen
                    anchors.fill: parent
                    anchors.margins: win.framed ? 6.5 * win.s : 0
                    readonly property real radius: win.framed ? 45 * win.s : 18 * win.s

                    Rectangle {
                        anchors.fill: parent
                        radius: screen.radius
                        visible: !receiver.streaming
                        gradient: Gradient {
                            orientation: win.landscape ? Gradient.Horizontal : Gradient.Vertical
                            GradientStop { position: 0.0; color: "#1c3b57" }
                            GradientStop { position: 0.6; color: "#3c6e71" }
                            GradientStop { position: 1.0; color: "#d9a15e" }
                        }
                    }
                    VideoFrame {
                        id: video
                        anchors.fill: parent
                        radius: screen.radius
                        visible: receiver.streaming
                    }

                    // "Now Mirroring Widget" style status card.
                    StatusCard {
                        anchors.horizontalCenter: parent.horizontalCenter
                        anchors.verticalCenter: parent.verticalCenter
                        width: Math.min(274 * win.s, parent.width - 40 * win.s)
                        visible: !receiver.streaming || win.showLiveCard
                        eyebrow: receiver.streaming ? "CONNECTED TO LINUX"
                                 : receiver.running ? "READY" : "SCREEN MIRRORING IS OFF"
                        heading: receiver.streaming ? "Mirroring is live"
                                 : receiver.running ? "Waiting for your iPhone" : "Start screen mirroring"
                        detail: receiver.streaming ? prefs.fps + " fps · Wi‑Fi"
                                : receiver.running ? (receiver.firewallBlocked
                                                    ? "The firewall is blocking the iPhone. Click to allow it."
                                                    : "On the iPhone: Control Center › Screen Mirroring › iPhone Mirror Bridge")
                                : "Click to start the receiver"
                        onClicked: {
                            if (!receiver.running) receiver.start()
                            else if (receiver.firewallBlocked) receiver.allowFirewall()
                        }
                    }

                    // Click the live screen to hand the touchpad/keyboard to the iPhone.
                    MouseArea {
                        anchors.fill: parent
                        enabled: receiver.streaming
                        cursorShape: bridge.connected ? Qt.PointingHandCursor : Qt.ArrowCursor
                        onClicked: {
                            root.forceActiveFocus()
                            if (bridge.connected && !bridge.active) {
                                bridge.setActive(true)
                                toast.show("Controlling your iPhone · Ctrl+" + (bridge.settings.hotkey || "F8") + " to release")
                            } else if (!bridge.connected) {
                                toast.show("Pair the iPhone over Bluetooth to control it")
                                app.showConnect()
                            }
                        }
                    }

                    Toast {
                        id: toast
                        anchors.horizontalCenter: parent.horizontalCenter
                        anchors.bottom: parent.bottom
                        anchors.bottomMargin: 28 * win.s
                    }
                }
            }
        }
    }
}
