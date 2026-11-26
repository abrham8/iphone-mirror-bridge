import QtQuick
import QtQuick.Window

// Holds the app's windows; Python (app) asks for them through signals.
QtObject {
    id: main
    property Window mirrorWindow: MirrorWindow { visible: false }
    property Window connectWindow: ConnectWindow { visible: false }
    property Window settingsWindow: SettingsWindow { visible: false }
    property Window hintCard: HintCard { visible: false }

    function raise(w) { w.show(); w.raise(); w.requestActivate() }

    property Connections appSignals: Connections {
        target: app
        function onMirrorRequested() {
            main.raise(main.mirrorWindow)
            if (!prefs.hintShown) { main.hintCard.popUp(); prefs.set("hintShown", true) }
        }
        function onMirrorToggleRequested() {
            if (main.mirrorWindow.visible) main.mirrorWindow.hide()
            else app.showMirror()
        }
        function onConnectRequested() { main.raise(main.connectWindow) }
        function onSettingsRequested(page) {
            if (page !== "") main.settingsWindow.page = page
            main.raise(main.settingsWindow)
        }
    }
}
