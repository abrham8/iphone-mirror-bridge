import QtQuick
import QtQuick.Controls.Basic

// 16x16 toolbar icon from the design (icon/home, icon/apps, ...).
Item {
    id: root
    property string icon
    property string tip
    property bool enabled2: true
    property bool highlighted: false
    signal clicked()
    width: 16; height: 16
    anchors.verticalCenter: parent ? parent.verticalCenter : undefined
    opacity: enabled2 ? (area.containsMouse ? 1 : (highlighted ? 1 : 0.85)) : 0.35

    Rectangle {  // subtle state for "Keep on top" being on
        anchors.centerIn: parent
        width: 24; height: 24; radius: 12
        color: Qt.rgba(1, 1, 1, 0.14)
        visible: root.highlighted
    }
    Image { anchors.fill: parent; source: Theme.asset(root.icon); sourceSize: Qt.size(32, 32) }
    MouseArea {
        id: area
        anchors.fill: parent
        anchors.margins: -4
        hoverEnabled: true
        enabled: root.enabled2
        cursorShape: Qt.PointingHandCursor
        onClicked: root.clicked()
    }
    ToolTip.visible: area.containsMouse && tip !== ""
    ToolTip.text: tip
    ToolTip.delay: 500
}
