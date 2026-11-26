import QtQuick

// "Window Close": 24x24 circle with icon/x.
Rectangle {
    id: root
    signal clicked()
    width: 24; height: 24; radius: 12
    color: area.containsMouse ? Qt.rgba(1, 1, 1, 0.18) : Qt.rgba(1, 1, 1, 0.1)
    Image { anchors.centerIn: parent; source: Theme.asset("icon-x.svg"); sourceSize: Qt.size(12, 12) }
    MouseArea { id: area; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.clicked() }
}
