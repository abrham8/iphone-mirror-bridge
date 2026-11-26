import QtQuick
import QtQuick.Window

// "02 · Connect iPhone". Pairing happens in the iPhone's Bluetooth settings;
// the 6-digit code the iPhone asks for is shown here.
Window {
    id: win
    title: "Connect your iPhone"
    color: "transparent"
    flags: Qt.Window | Qt.FramelessWindowHint
    readonly property real margin: 40
    width: 460 + margin * 2
    readonly property real fixedHeight: panel.implicitHeight + margin * 2
    height: fixedHeight
    minimumWidth: 460 + margin * 2; maximumWidth: 460 + margin * 2
    minimumHeight: fixedHeight; maximumHeight: fixedHeight

    readonly property bool waiting: bridge.pairing && bridge.pairingCode === ""
    readonly property var digits: bridge.pairingCode !== "" ? bridge.pairingCode.split("") : ["", "", "", "", "", ""]

    onVisibleChanged: if (visible) bridge.refreshDevices()

    Connections {
        target: bridge
        // Close shortly after the iPhone connects while this window is open.
        function onConnectionChanged(connected, name) {
            if (connected && win.visible) closeTimer.restart()
        }
    }
    Timer { id: closeTimer; interval: 1500; onTriggered: win.hide() }

    Panel {
        id: panel
        x: win.margin; y: win.margin
        width: 460
        implicitHeight: col.implicitHeight + 36 + 32
        height: implicitHeight
        color: Theme.connectBg
        radius: 28

        DragHandler { target: null; onActiveChanged: if (active) win.startSystemMove() }

        Column {
            id: col
            x: 36; y: 36
            width: 386
            spacing: 18

            Image {
                anchors.horizontalCenter: parent.horizontalCenter
                width: 72; height: 72
                source: Theme.asset("app-logo.svg")
                sourceSize: Qt.size(144, 144)
            }
            Label2 {
                anchors.horizontalCenter: parent.horizontalCenter
                text: "Connect your iPhone to Linux"
                weight: 700
                font.pixelSize: 24
            }
            Label2 {
                width: parent.width
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
                text: "Keep your phone nearby, unlocked, and on the same Wi‑Fi as this computer."
                color: Theme.text70
            }

            // "Device List"
            Column {
                width: parent.width
                spacing: 8
                Repeater {
                    model: bridge.devices
                    delegate: DeviceRow {
                        required property var modelData
                        width: parent.width
                        name: modelData.name
                        detail: modelData.connected ? "Connected · Bluetooth" : "Paired · not connected"
                        online: modelData.connected
                    }
                }
                DeviceRow {
                    width: parent.width
                    visible: bridge.devices.length === 0
                    name: bridge.available ? "No iPhone paired yet" : "Bridge service is not running"
                    detail: bridge.available ? "Follow the steps below" : "Check: systemctl status iphone-mirror-bridge"
                    online: false
                    dim: true
                }
            }

            // "Steps"
            Column {
                width: parent.width
                spacing: 8
                Repeater {
                    model: ["1  Turn on AssistiveTouch: Settings › Accessibility › Touch",
                            "2  Open Settings › Bluetooth and tap iPhone Mirror Bridge",
                            "3  Enter the 6‑digit code shown here"]
                    delegate: Label2 {
                        required property string modelData
                        text: modelData
                        weight: 500
                        font.pixelSize: 13
                        color: Theme.text80
                    }
                }
            }

            // "Pair Code": 3 + 3 digit boxes.
            Row {
                anchors.horizontalCenter: parent.horizontalCenter
                spacing: 8
                Repeater {
                    model: 6
                    delegate: Row {
                        required property int index
                        spacing: 8
                        Item { width: 8; height: 8; visible: index === 3 }
                        Rectangle {
                            width: 44; height: 54
                            radius: 12
                            color: Qt.rgba(1, 1, 1, 0.07)
                            border.color: Qt.rgba(1, 1, 1, 0.14)
                            Label2 {
                                anchors.centerIn: parent
                                text: win.digits[index] !== "" ? win.digits[index] : "–"
                                color: win.digits[index] !== "" ? Theme.text : Qt.rgba(1, 1, 1, 0.25)
                                weight: 700
                                font.pixelSize: 22
                            }
                        }
                    }
                }
            }

            Label2 {
                width: parent.width
                horizontalAlignment: Text.AlignHCenter
                font.pixelSize: 12
                color: bridge.error !== "" ? Theme.danger : Theme.text60
                visible: text !== ""
                text: bridge.error !== "" ? bridge.error
                      : bridge.connected ? "Connected to " + (bridge.deviceName || "your iPhone")
                      : bridge.pairingCode !== "" ? "Type this code on your iPhone"
                      : win.waiting ? "Waiting for your iPhone… tap iPhone Mirror Bridge in its Bluetooth settings"
                      : ""
            }

            // "Actions"
            Row {
                width: parent.width
                spacing: 10
                SecondaryButton {
                    width: (parent.width - 10) / 2
                    text: "Cancel"
                    onClicked: { bridge.stopPairing(); win.hide() }
                }
                GradientButton {
                    width: (parent.width - 10) / 2
                    text: bridge.connected ? "Done" : win.waiting ? "Waiting…" : "Connect"
                    enabled2: bridge.available
                    onClicked: bridge.connected ? win.hide() : bridge.startPairing()
                }
            }
        }

        CloseButton {
            x: parent.width - width - 13; y: 13
            onClicked: { bridge.stopPairing(); win.hide() }
        }
    }
}
