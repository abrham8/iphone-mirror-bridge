import QtQuick
import QtQuick.Effects

// Rounded translucent panel with the design's drop shadow.
Item {
    id: root
    property color color: Theme.connectBg
    property real radius: 28
    property color borderColor: Theme.hairline
    property real borderWidth: 1
    property real shadowY: 24
    property real shadowBlur: 50
    property real shadowOpacity: 0.4
    default property alias content: inner.data

    Rectangle {
        id: bg
        anchors.fill: parent
        radius: root.radius
        color: root.color
        border.color: root.borderColor
        border.width: root.borderWidth
        layer.enabled: true
        layer.effect: MultiEffect {
            shadowEnabled: true
            shadowColor: Qt.rgba(0, 0, 0, root.shadowOpacity)
            shadowVerticalOffset: root.shadowY
            shadowBlur: Math.min(1.0, root.shadowBlur / 64)
            blurMax: 64
        }
    }
    Item {
        id: inner
        anchors.fill: parent
    }
}
