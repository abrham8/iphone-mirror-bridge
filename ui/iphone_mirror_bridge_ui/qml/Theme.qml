pragma Singleton
import QtQuick

// Design tokens from the Figma file "Screen Mirror App UI".
QtObject {
    readonly property string font: "Inter"
    readonly property color text: "#ffffff"
    readonly property color text80: Qt.rgba(1, 1, 1, 0.8)
    readonly property color text75: Qt.rgba(1, 1, 1, 0.75)
    readonly property color text70: Qt.rgba(1, 1, 1, 0.7)
    readonly property color text60: Qt.rgba(1, 1, 1, 0.6)
    readonly property color text55: Qt.rgba(1, 1, 1, 0.55)
    readonly property color hairline: Qt.rgba(1, 1, 1, 0.12)
    readonly property color accentA: "#7C5CFF"
    readonly property color accentB: "#3AC8FF"
    readonly property color live: "#3DDC97"
    readonly property color danger: "#FF8A7A"

    readonly property color connectBg: Qt.rgba(20 / 255, 20 / 255, 28 / 255, 0.85)
    readonly property color settingsBg: Qt.rgba(22 / 255, 22 / 255, 30 / 255, 0.92)
    readonly property color pillBg: Qt.rgba(21 / 255, 21 / 255, 28 / 255, 0.72)
    readonly property color hintBg: Qt.rgba(14 / 255, 14 / 255, 20 / 255, 0.45)
    readonly property color rowBg: Qt.rgba(1, 1, 1, 0.05)
    readonly property color sidebarBg: Qt.rgba(1, 1, 1, 0.04)
    readonly property color navSelected: Qt.rgba(124 / 255, 92 / 255, 1, 0.35)
    readonly property color segmentedBg: Qt.rgba(1, 1, 1, 0.08)
    readonly property color segmentSelected: Qt.rgba(1, 1, 1, 0.2)
    readonly property color deviceSelectedBg: Qt.rgba(124 / 255, 92 / 255, 1, 0.18)
    readonly property color deviceSelectedBorder: Qt.rgba(156 / 255, 133 / 255, 1, 0.7)
    readonly property color deviceBorder: Qt.rgba(1, 1, 1, 0.08)
    readonly property color frameBg: "#0b0b10"
    readonly property color frameBorder: Qt.rgba(1, 1, 1, 0.18)

    function asset(name) { return Qt.resolvedUrl("../assets/" + name) }
}
