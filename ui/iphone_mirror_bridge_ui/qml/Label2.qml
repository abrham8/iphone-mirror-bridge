import QtQuick

// Text in the design's typeface. weight: 400 regular, 500 medium, 600 semibold, 700 bold.
Text {
    property int weight: 400
    color: Theme.text
    font.family: Theme.font
    font.pixelSize: 14
    font.weight: weight === 700 ? Font.Bold : weight === 600 ? Font.DemiBold : weight === 500 ? Font.Medium : Font.Normal
    renderType: Text.NativeRendering
}
