import QtQuick

// "Device Row": phone icon, name and detail, status dot.
Rectangle {
    id: root
    property alias name: nameText.text
    property alias detail: detailText.text
    property bool online: false
    property bool dim: false
    height: 60
    radius: 14
    color: online ? Theme.deviceSelectedBg : Theme.rowBg
    border.color: online ? Theme.deviceSelectedBorder : Theme.deviceBorder
    opacity: dim ? 0.8 : 1
    Image {
        x: 15; anchors.verticalCenter: parent.verticalCenter
        width: 28; height: 28
        source: Theme.asset("icon-phone.svg")
        sourceSize: Qt.size(56, 56)
    }
    Column {
        x: 55; anchors.verticalCenter: parent.verticalCenter
        width: parent.width - 55 - 35
        spacing: 2
        Label2 { id: nameText; weight: 600; elide: Text.ElideRight; width: parent.width }
        Label2 { id: detailText; font.pixelSize: 12; color: Theme.text60; elide: Text.ElideRight; width: parent.width }
    }
    Image {
        x: parent.width - 15 - 8; anchors.verticalCenter: parent.verticalCenter
        width: 8; height: 8
        source: Theme.asset(root.online ? "dot-online.svg" : "dot-offline.svg")
    }
}
