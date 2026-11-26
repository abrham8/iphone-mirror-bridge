import QtQuick

// Short message pill shown over the screen.
Rectangle {
    id: root
    function show(message) { label.text = message; opacity = 1; hideTimer.restart() }
    width: label.implicitWidth + 28
    height: 30
    radius: 15
    color: Theme.pillBg
    border.color: Theme.hairline
    opacity: 0
    visible: opacity > 0
    Behavior on opacity { NumberAnimation { duration: 250 } }
    Label2 { id: label; anchors.centerIn: parent; weight: 600; font.pixelSize: 12 }
    Timer { id: hideTimer; interval: 3500; onTriggered: root.opacity = 0 }
}
