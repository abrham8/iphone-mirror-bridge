import QtQuick

// "Button / Secondary".
Rectangle {
    id: root
    property alias text: label.text
    property int fontSize: 14
    signal clicked()
    implicitWidth: label.implicitWidth + 40
    implicitHeight: label.implicitHeight + 22
    radius: 12
    color: area.pressed ? Qt.rgba(1, 1, 1, 0.14) : Qt.rgba(1, 1, 1, 0.08)
    border.color: Qt.rgba(1, 1, 1, 0.14)
    border.width: 1
    Label2 { id: label; anchors.centerIn: parent; weight: 600; font.pixelSize: root.fontSize }
    MouseArea { id: area; anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.clicked() }
}
