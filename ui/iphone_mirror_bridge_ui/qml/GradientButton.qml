import QtQuick

// "Button / Primary" and "Button / Done": accent gradient, radius 12.
Rectangle {
    id: root
    property alias text: label.text
    property bool enabled2: true
    signal clicked()
    implicitWidth: label.implicitWidth + 40
    implicitHeight: label.implicitHeight + 22
    radius: 12
    opacity: enabled2 ? (area.pressed ? 0.85 : 1) : 0.5
    gradient: Gradient {
        orientation: Gradient.Horizontal
        GradientStop { position: 0.0; color: Theme.accentA }
        GradientStop { position: 0.714; color: Theme.accentB }
    }
    Label2 { id: label; anchors.centerIn: parent; weight: 600 }
    MouseArea {
        id: area
        anchors.fill: parent
        enabled: root.enabled2
        cursorShape: Qt.PointingHandCursor
        onClicked: root.clicked()
    }
}
