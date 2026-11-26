import QtQuick

// "Segmented" control: options = [{label, value}], current = selected value.
Rectangle {
    id: root
    property var options: []
    property var current
    signal picked(var value)
    implicitWidth: row.implicitWidth + 6
    implicitHeight: 31
    radius: 9
    color: Theme.segmentedBg
    Row {
        id: row
        x: 3; y: 3
        Repeater {
            model: root.options
            delegate: Rectangle {
                required property var modelData
                readonly property bool selected: modelData.value === root.current
                width: lbl.implicitWidth + 20
                height: 25
                radius: 7
                color: selected ? Theme.segmentSelected : "transparent"
                Label2 { id: lbl; anchors.centerIn: parent; text: parent.modelData.label; weight: 500; font.pixelSize: 12 }
                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.picked(parent.modelData.value) }
            }
        }
    }
}
