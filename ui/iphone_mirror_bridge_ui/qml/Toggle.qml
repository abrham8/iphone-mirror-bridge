import QtQuick

// "Toggle / On" and "Toggle / Off" (40x24) from the design.
Image {
    id: root
    property bool checked: false
    signal toggled(bool checked)
    width: 40; height: 24
    sourceSize: Qt.size(80, 48)
    source: Theme.asset(checked ? "toggle-on.svg" : "toggle-off.svg")
    MouseArea {
        anchors.fill: parent
        cursorShape: Qt.PointingHandCursor
        onClicked: root.toggled(!root.checked)
    }
}
