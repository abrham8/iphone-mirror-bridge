import QtQuick

// Styled after the design's "Now Mirroring Widget".
Rectangle {
    id: root
    property alias eyebrow: eyebrowText.text
    property alias heading: headingText.text
    property alias detail: detailText.text
    signal clicked()
    implicitHeight: col.implicitHeight + 28
    radius: 22
    color: Qt.rgba(1, 1, 1, 0.16)
    border.color: Qt.rgba(1, 1, 1, 0.2)
    Column {
        id: col
        x: 16; y: 14
        width: parent.width - 32
        spacing: 4
        Label2 { id: eyebrowText; weight: 600; font.pixelSize: 10; color: Theme.text70 }
        Label2 { id: headingText; weight: 700; font.pixelSize: 17 }
        Label2 { id: detailText; font.pixelSize: 12; color: Theme.text80; wrapMode: Text.WordWrap; width: parent.width }
    }
    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.clicked() }
}
