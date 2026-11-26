import QtQuick
import QtQuick.Window

// "Hint Card", shown the first time the mirror window opens.
Window {
    id: win
    color: "transparent"
    flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
    width: card.width + 40
    height: card.height + 40

    function popUp() {
        if (Screen.desktopAvailableWidth > 0) {
            x = Screen.virtualX + 120
            y = Screen.virtualY + Screen.desktopAvailableHeight - height - 60
        }
        show()
        hideTimer.restart()
    }
    Timer { id: hideTimer; interval: 9000; onTriggered: win.hide() }

    Rectangle {
        id: card
        x: 20; y: 20
        width: col.implicitWidth + 40
        height: col.implicitHeight + 36
        radius: 18
        color: Theme.hintBg
        border.color: Theme.hairline
        Column {
            id: col
            x: 20; y: 18
            spacing: 6
            Label2 { text: "Your phone, on your desktop"; weight: 700; font.pixelSize: 22 }
            Label2 { text: "Click and type to control apps. Drag the window anywhere."; font.pixelSize: 14; color: Theme.text75 }
        }
        MouseArea { anchors.fill: parent; onClicked: win.hide() }
    }
}
