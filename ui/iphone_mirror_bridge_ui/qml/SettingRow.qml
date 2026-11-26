import QtQuick
import QtQuick.Layouts

// "Setting Row": title + subtitle on the left, a control on the right.
Rectangle {
    id: root
    property alias title: titleText.text
    property alias subtitle: subText.text
    default property alias control: slot.data
    Layout.fillWidth: true
    implicitHeight: 58
    radius: 12
    color: Theme.rowBg
    Column {
        anchors.left: parent.left
        anchors.leftMargin: 16
        anchors.right: slot.left
        anchors.rightMargin: 12
        anchors.verticalCenter: parent.verticalCenter
        spacing: 2
        Label2 { id: titleText; weight: 500; font.pixelSize: 14; elide: Text.ElideRight; width: parent.width }
        Label2 { id: subText; font.pixelSize: 12; color: Theme.text55; elide: Text.ElideRight; width: parent.width; visible: text !== "" }
    }
    Item {
        id: slot
        anchors.right: parent.right
        anchors.rightMargin: 16
        anchors.verticalCenter: parent.verticalCenter
        width: childrenRect.width
        height: childrenRect.height
    }
}
