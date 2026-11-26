import QtQuick
import QtQuick.Window
import QtQuick.Layouts

// "03 · Settings": sidebar navigation and setting rows.
Window {
    id: win
    title: "iPhone Mirror Bridge Settings"
    color: "transparent"
    flags: Qt.Window | Qt.FramelessWindowHint
    readonly property real margin: 40
    width: 760 + margin * 2
    height: 600 + margin * 2
    minimumWidth: 760 + margin * 2; maximumWidth: 760 + margin * 2
    minimumHeight: 600 + margin * 2; maximumHeight: 600 + margin * 2

    property string page: "display"
    readonly property var pages: [
        { key: "general", label: "General" },
        { key: "display", label: "Display" },
        { key: "controls", label: "Controls" },
        { key: "notifications", label: "Notifications" },
        { key: "devices", label: "Devices" },
        { key: "privacy", label: "Privacy" }
    ]
    readonly property var cfg: bridge.settings

    Panel {
        x: win.margin; y: win.margin
        width: 760; height: 600
        color: Theme.settingsBg
        radius: 20

        DragHandler { target: null; onActiveChanged: if (active) win.startSystemMove() }

        // -- "Sidebar" -----------------------------------------------------------
        Rectangle {
            id: sidebar
            x: 1; y: 1
            width: 200; height: parent.height - 2
            color: Theme.sidebarBg
            topLeftRadius: 19; bottomLeftRadius: 19
            Column {
                x: 12; y: 20
                width: 176
                spacing: 4
                Label2 { text: "iPhone Mirror Bridge"; weight: 700; font.pixelSize: 13; color: Theme.text60 }
                Repeater {
                    model: win.pages
                    delegate: Rectangle {
                        required property var modelData
                        readonly property bool selected: win.page === modelData.key
                        width: 176; height: 32
                        radius: 8
                        color: selected ? Theme.navSelected : (navArea.containsMouse ? Qt.rgba(1, 1, 1, 0.05) : "transparent")
                        Label2 {
                            x: 10; anchors.verticalCenter: parent.verticalCenter
                            text: parent.modelData.label
                            weight: parent.selected ? 600 : 400
                            font.pixelSize: 13
                        }
                        MouseArea {
                            id: navArea
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: win.page = parent.modelData.key
                        }
                    }
                }
            }
        }

        // -- "Content" ---------------------------------------------------------------
        Item {
            x: 201; y: 1
            width: 558; height: 598

            Label2 {
                id: pageTitle
                x: 28; y: 24
                text: win.pages.find(p => p.key === win.page).label
                weight: 700
                font.pixelSize: 20
            }

            Flickable {
                x: 28; y: 62
                width: 502; height: 598 - 62 - 70
                contentHeight: pageLoader.item ? pageLoader.item.implicitHeight : 0
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                Loader {
                    id: pageLoader
                    width: parent.width
                    sourceComponent: ({ general: generalPage, display: displayPage, controls: controlsPage,
                                        notifications: notificationsPage, devices: devicesPage,
                                        privacy: privacyPage })[win.page]
                }
            }

            GradientButton {
                x: 558 - 28 - width; y: 539
                text: "Done"
                onClicked: win.hide()
            }
        }

        CloseButton { x: parent.width - width - 13; y: 13; onClicked: win.hide() }
    }

    // ---------------------------------------------------------------------------
    Component {
        id: displayPage
        ColumnLayout {
            spacing: 14
            SettingRow {
                title: "Window size"; subtitle: "How large the phone window appears"
                Segmented {
                    options: [{ label: "Small", value: "small" }, { label: "Actual", value: "actual" },
                              { label: "Large", value: "large" }]
                    current: prefs.windowSize
                    onPicked: (v) => prefs.set("windowSize", v)
                }
            }
            SettingRow {
                title: "Frame rate"; subtitle: "Higher is smoother, uses more battery"
                Segmented {
                    options: [{ label: "30", value: 30 }, { label: "60", value: 60 }]
                    current: prefs.fps
                    onPicked: (v) => { prefs.set("fps", v); receiver.restart() }
                }
            }
            SettingRow {
                title: "Keep window on top"; subtitle: "Float above other apps"
                Toggle { checked: prefs.onTop; onToggled: (c) => prefs.set("onTop", c) }
            }
            SettingRow {
                title: "Show device frame"; subtitle: "Draw a phone outline around the screen"
                Toggle { checked: prefs.deviceFrame; onToggled: (c) => prefs.set("deviceFrame", c) }
            }
            SettingRow {
                title: "Hide when inactive"; subtitle: "Fade the window after 10 seconds"
                Toggle { checked: prefs.hideInactive; onToggled: (c) => prefs.set("hideInactive", c) }
            }
            SettingRow {
                title: "Display server"
                subtitle: "Detected automatically · " + app.displayServer + (prefs.displayServer !== "auto" ? " · applies after restart" : "")
                Segmented {
                    options: [{ label: "Auto", value: "auto" }, { label: "Wayland", value: "wayland" },
                              { label: "X11", value: "x11" }]
                    current: prefs.displayServer
                    onPicked: (v) => prefs.set("displayServer", v)
                }
            }
        }
    }

    Component {
        id: generalPage
        ColumnLayout {
            spacing: 14
            SettingRow {
                title: "Start at login"; subtitle: "Keep the bridge in the system tray"
                Toggle { checked: prefs.startAtLogin; onToggled: (c) => prefs.setStartAtLogin(c) }
            }
            SettingRow {
                title: "Screen mirroring receiver"
                subtitle: receiver.running ? (receiver.streaming ? "Mirroring now" : "Ready · waiting for the iPhone") : "Off"
                SecondaryButton {
                    text: receiver.running ? "Stop" : "Start"; fontSize: 12
                    implicitHeight: 31
                    onClicked: receiver.running ? receiver.stop() : receiver.start()
                }
            }
            SettingRow {
                title: "Start the receiver automatically"; subtitle: "Be ready for screen mirroring when the app starts"
                Toggle { checked: prefs.autoStartMirroring; onToggled: (c) => prefs.set("autoStartMirroring", c) }
            }
            SettingRow {
                title: "Show the window when mirroring starts"; subtitle: "Open it as soon as the iPhone connects"
                Toggle { checked: prefs.showOnMirror; onToggled: (c) => prefs.set("showOnMirror", c) }
            }
            SettingRow {
                title: "Version"
                subtitle: "App " + app.version + (bridge.serviceVersion ? " · Service " + bridge.serviceVersion : " · Service not running")
            }
        }
    }

    Component {
        id: controlsPage
        ColumnLayout {
            spacing: 14
            SettingRow {
                title: "Control with"; subtitle: "Restarts the bridge"
                Segmented {
                    options: [{ label: "Auto", value: "auto" }, { label: "Touchpad", value: "touchpad" },
                              { label: "Mouse", value: "mouse" }]
                    current: win.cfg.input || "auto"
                    onPicked: (v) => bridge.setSetting("input", v)
                }
            }
            SettingRow {
                title: "Pointer speed"; subtitle: "Touchpad pointer"
                Segmented {
                    options: [{ label: "Slow", value: 12 }, { label: "Normal", value: 20 },
                              { label: "Fast", value: 30 }, { label: "Faster", value: 42 }]
                    current: win.cfg.touchpad_speed
                    onPicked: (v) => bridge.setSetting("touchpad_speed", v)
                }
            }
            SettingRow {
                title: "Scroll speed"; subtitle: "Two-finger scrolling"
                Segmented {
                    options: [{ label: "Slow", value: 0.5 }, { label: "Normal", value: 1.0 },
                              { label: "Fast", value: 2.0 }]
                    current: win.cfg.scroll_speed
                    onPicked: (v) => bridge.setSetting("scroll_speed", v)
                }
            }
            SettingRow {
                title: "Natural scrolling"; subtitle: "Content moves with your fingers"
                Toggle { checked: win.cfg.natural_scroll !== false; onToggled: (c) => bridge.setSetting("natural_scroll", c) }
            }
            SettingRow {
                title: "Tap to click"; subtitle: "Two-finger tap right-clicks"
                Toggle { checked: win.cfg.tap_to_click !== false; onToggled: (c) => bridge.setSetting("tap_to_click", c) }
            }
            SettingRow {
                title: "Three-finger drag"; subtitle: "Rest three fingers to hold, move to drag"
                Toggle { checked: win.cfg.three_finger_drag !== false; onToggled: (c) => bridge.setSetting("three_finger_drag", c) }
            }
            SettingRow {
                title: "Switch shortcut"; subtitle: "Moves control between this computer and the iPhone"
                Segmented {
                    options: [{ label: "Ctrl+F8", value: "F8" }, { label: "Ctrl+F9", value: "F9" },
                              { label: "Ctrl+F12", value: "F12" }]
                    current: win.cfg.hotkey || "F8"
                    onPicked: (v) => bridge.setSetting("hotkey", v)
                }
            }
        }
    }

    Component {
        id: notificationsPage
        ColumnLayout {
            spacing: 14
            SettingRow {
                title: "iPhone connects or disconnects"; subtitle: "Bluetooth control"
                Toggle { checked: prefs.notifyConnection; onToggled: (c) => prefs.set("notifyConnection", c) }
            }
            SettingRow {
                title: "Screen mirroring is ready"; subtitle: "When the receiver starts or stops"
                Toggle { checked: prefs.notifyMirroring; onToggled: (c) => prefs.set("notifyMirroring", c) }
            }
        }
    }

    Component {
        id: devicesPage
        ColumnLayout {
            spacing: 14
            Repeater {
                model: bridge.devices
                delegate: SettingRow {
                    required property var modelData
                    title: modelData.name
                    subtitle: (modelData.connected ? "Connected" : "Paired") + " · " + modelData.address
                    SecondaryButton {
                        text: "Forget"; fontSize: 12; implicitHeight: 31
                        onClicked: bridge.forget(parent.parent.modelData.address)
                    }
                }
            }
            SettingRow {
                visible: bridge.devices.length === 0
                title: "No iPhone paired yet"; subtitle: "Pair one to control it from this computer"
            }
            SettingRow {
                title: "Pair a new iPhone"; subtitle: "Shows the 6-digit pairing code"
                SecondaryButton { text: "Connect…"; fontSize: 12; implicitHeight: 31; onClicked: app.showConnect() }
            }
        }
    }

    Component {
        id: privacyPage
        ColumnLayout {
            spacing: 14
            SettingRow {
                title: "Type on the iPhone"; subtitle: "Acts as a Bluetooth keyboard too · re-pair after changing"
                Toggle { checked: win.cfg.keyboard !== false; onToggled: (c) => bridge.setSetting("keyboard", c) }
            }
            SettingRow {
                title: "Require a pairing code"; subtitle: "The iPhone must enter the code shown here"
                Toggle {
                    checked: (win.cfg.pairing || "code") === "code"
                    onToggled: (c) => bridge.setSetting("pairing", c ? "code" : "just-works")
                }
            }
            SettingRow {
                title: "Firewall"
                subtitle: receiver.firewallBlocked ? "Blocks screen mirroring on this network" : "Screen mirroring is allowed"
                SecondaryButton {
                    visible: receiver.firewallBlocked
                    text: "Allow"; fontSize: 12; implicitHeight: 31
                    onClicked: receiver.allowFirewall()
                }
            }
            Label2 {
                Layout.fillWidth: true
                Layout.topMargin: 4
                wrapMode: Text.WordWrap
                font.pixelSize: 12
                color: Theme.text55
                text: "Video and input stay on this computer, the local network and the Bluetooth link. Nothing is sent to the internet. Anyone on the same network can mirror to the receiver while it is running."
            }
        }
    }
}
