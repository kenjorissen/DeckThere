import QtQuick

Rectangle {
    id: panel
    required property var preferences
    signal closed
    color: "#f0000000"
    readonly property int margin: 24
    readonly property int labelWidth: 148
    MouseArea { anchors.fill: parent }

    component SettingButton: Rectangle {
        id: button
        property string label
        property bool selected: false
        signal tapped
        width: parent.width
        height: 48
        radius: 8
        color: !enabled ? "#20252a" : (selected ? "#245674" : "#19334a")
        border.color: !enabled ? "#39434d" : (selected ? "#61b8ef" : "#52738e")
        Text {
            anchors.fill: parent
            anchors.margins: 6
            text: button.label
            color: button.enabled ? "#eaf2f8" : "#707b86"
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            font.pixelSize: 20
            fontSizeMode: Text.Fit
        }
        MouseArea { anchors.fill: parent; onClicked: button.tapped() }
    }

    component Section: Item {
        id: section
        property string title
        property string hint
        property alias body: controls.data
        readonly property bool stacked: width < 620
        width: parent.width
        height: Math.max(heading.height, controls.y + controls.height) + (hint ? helper.height + 6 : 0)
        Text {
            id: heading
            width: section.stacked ? parent.width : panel.labelWidth
            height: 28
            y: section.stacked ? 0 : 10
            text: section.title
            color: "#61b8ef"
            font.bold: true
            font.pixelSize: 20
        }
        Item {
            id: controls
            x: section.stacked ? 0 : panel.labelWidth + 12
            y: section.stacked ? 36 : 0
            width: parent.width - x
            height: childrenRect.height
        }
        Text {
            id: helper
            x: controls.x
            y: controls.y + controls.height + 6
            width: controls.width
            text: section.hint
            visible: text.length > 0
            wrapMode: Text.Wrap
            color: "#8fb4d0"
            font.pixelSize: 16
        }
    }

    component Divider: Rectangle {
        width: parent.width
        height: 1
        color: "#354a5d"
    }

    Text {
        id: title
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.margins: panel.margin
        text: "SETTINGS"
        color: "#61b8ef"
        font.bold: true
        font.pixelSize: 26
    }

    Flickable {
        id: scroller
        objectName: "settingsScroller"
        anchors.top: title.bottom
        anchors.bottom: footer.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.leftMargin: panel.margin
        anchors.rightMargin: panel.margin
        anchors.topMargin: 14
        anchors.bottomMargin: 12
        clip: true
        contentHeight: settingsColumn.height
        boundsBehavior: Flickable.StopAtBounds
        Column {
            id: settingsColumn
            width: scroller.width
            spacing: 12
            Section {
                title: "Next launch"
                hint: "Saved default; current session unchanged."
                body: Row {
                    width: parent.width
                    spacing: 8
                    Repeater {
                        model: [{mode:"gui",label:"GUI"}, {mode:"keyboard",label:"GUI + keyboard"}, {mode:"terminal",label:"Terminal"}]
                        delegate: SettingButton {
                            required property var modelData
                            objectName: "default_" + modelData.mode
                            width: (parent.width - 16) / 3
                            label: modelData.label
                            selected: preferences !== null && preferences.mode === modelData.mode
                            onTapped: preferences.save(modelData.mode)
                        }
                    }
                }
            }
            Divider { }
            Section {
                title: "Auto-dim"
                hint: "Now: this session. On launch: saved default."
                body: Row {
                    width: parent.width
                    spacing: 8
                    SettingButton {
                        objectName: "autoDimNow"
                        width: (parent.width - 8) / 2
                        label: "Now: " + (!preferences || !preferences.hasSession ? "Unavailable" : (preferences.autoDimNow ? "On" : "Off"))
                        enabled: preferences !== null && preferences.canDim
                        onTapped: preferences.toggleAutoDimNow()
                    }
                    SettingButton {
                        objectName: "autoDimLaunch"
                        width: (parent.width - 8) / 2
                        label: "On launch: " + (preferences && preferences.autoDimLaunch ? "On" : "Off")
                        onTapped: preferences.toggleAutoDimLaunch()
                    }
                }
            }
            Divider { }
            Section {
                title: "Idle sleep"
                hint: preferences && preferences.sleepMessage ? preferences.sleepMessage : "Saved timer. 30s warning; gyro ignored."
                body: Row {
                    width: parent.width
                    spacing: 8
                    Repeater {
                        model: [0, 5, 15, 30, 60]
                        delegate: SettingButton {
                            required property int modelData
                            objectName: "sleep_" + modelData
                            width: (parent.width - 32) / 5
                            label: modelData === 0 ? "Never" : modelData + " min"
                            selected: preferences !== null && preferences.sleepMinutes === modelData
                            onTapped: preferences.saveSleep(modelData)
                        }
                    }
                }
            }
            Divider { }
            Section {
                title: "Haptics"
                hint: "Saved. Connect: 2 short. Disconnect: 2 long, after USB release."
                body: Row {
                    width: parent.width
                    spacing: 8
                    SettingButton {
                        objectName: "hapticsToggle"
                        width: (parent.width - 24) / 4
                        label: preferences && preferences.hapticsEnabled ? "On" : "Off"
                        onTapped: preferences.toggleHaptics()
                    }
                    Repeater {
                        model: ["quiet", "normal", "strong"]
                        delegate: SettingButton {
                            required property string modelData
                            objectName: "haptics_" + modelData
                            width: (parent.width - 24) / 4
                            label: (selected ? "✓ " : "") + modelData.charAt(0).toUpperCase() + modelData.slice(1)
                            selected: preferences !== null && preferences.hapticsStrength === modelData
                            enabled: preferences !== null && preferences.hapticsEnabled
                            onTapped: preferences.saveHapticsStrength(modelData)
                        }
                    }
                }
            }
            Divider { objectName: "sessionSeparator" }
            Section {
                title: "Keyboard"
                hint: "This session only. Sharing both devices requires a VirtualHere license."
                body: SettingButton {
                    objectName: "sessionKeyboard"
                    label: (preferences && preferences.keyboardEnabled ? "Stop" : "Start") + " keyboard for this session ONLY"
                    enabled: preferences !== null && preferences.canToggle
                    onTapped: preferences.toggleKeyboard()
                }
            }
        }
    }

    Column {
        id: footer
        anchors.bottom: parent.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.margins: panel.margin
        spacing: 8
        Text {
            objectName: "settingsMessage"
            width: parent.width
            text: preferences ? preferences.message : ""
            visible: text.length > 0
            wrapMode: Text.Wrap
            maximumLineCount: 2
            elide: Text.ElideRight
            color: "#eaf2f8"
            font.pixelSize: 16
        }
        SettingButton {
            objectName: "closeSettings"
            label: "Close settings"
            onTapped: panel.closed()
        }
    }
}
