import QtQuick

Rectangle {
    id: panel
    required property var preferences
    signal closed
    color: "#f0000000"
    MouseArea { anchors.fill: parent }

    component SettingButton: Rectangle {
        id: button
        property string label
        signal tapped
        width: parent.width
        height: Math.max(42, panel.height * 0.075)
        radius: 8
        color: enabled ? "#19334a" : "#20252a"
        border.color: "#52738e"
        Text {
            anchors.fill: parent
            anchors.margins: 6
            text: button.label
            color: button.enabled ? "#eaf2f8" : "#89939c"
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            font.pixelSize: Math.max(13, panel.height * 0.027)
            fontSizeMode: Text.Fit
        }
        MouseArea { anchors.fill: parent; onClicked: button.tapped() }
    }

    Flickable {
        anchors.fill: parent
        clip: true
        contentHeight: Math.max(height, settingsColumn.height + 32)
        boundsBehavior: Flickable.StopAtBounds
    Column {
        id: settingsColumn
        x: (panel.width - width) / 2
        y: Math.max(16, (panel.height - height) / 2)
        width: Math.min(panel.width * 0.9, 820)
        spacing: Math.max(6, panel.height * 0.015)
        Text {
            text: "SETTINGS"
            color: "#61b8ef"
            font.bold: true
            font.pixelSize: panel.height * 0.04
        }
        Text {
            width: parent.width
            text: !preferences ? "" : "Next launch: " + (preferences.mode === "keyboard" ? "GUI + keyboard" : preferences.mode === "gui" ? "GUI — no keyboard" : "Terminal")
            color: "#eaf2f8"
            font.pixelSize: panel.height * 0.028
        }
        Row {
            width: parent.width
            spacing: 8
            Repeater {
                model: [{mode:"gui",label:"GUI"}, {mode:"keyboard",label:"GUI + keyboard"}, {mode:"terminal",label:"Terminal"}]
                delegate: SettingButton {
                    required property var modelData
                    objectName: "default_" + modelData.mode
                    width: (parent.width - 16) / 3
                    label: modelData.label
                    onTapped: preferences.save(modelData.mode)
                }
            }
        }
        Row {
            width: parent.width
            spacing: 8
            SettingButton {
                objectName: "autoDimNow"
                width: (parent.width - 8) / 2
                label: "Auto-dim now: " + (!preferences || !preferences.hasSession ? "Unavailable" : (preferences.autoDimNow ? "On" : "Off"))
                enabled: preferences !== null && preferences.canDim
                onTapped: preferences.toggleAutoDimNow()
            }
            SettingButton {
                objectName: "autoDimLaunch"
                width: (parent.width - 8) / 2
                label: "Auto-dim on launch: " + (preferences && preferences.autoDimLaunch ? "On" : "Off")
                onTapped: preferences.toggleAutoDimLaunch()
            }
        }
        Text {
            width: parent.width
            wrapMode: Text.Wrap
            text: "Now changes this GUI session only. On launch saves the default."
            color: "#8fb4d0"
            font.pixelSize: Math.max(12, panel.height * 0.022)
        }
        Text {
            width: parent.width
            text: !preferences ? "" : "Sleep after inactivity: " + (preferences.sleepMinutes === 0 ? "Never" : preferences.sleepMinutes + " minutes")
            color: "#eaf2f8"
            font.pixelSize: Math.max(13, panel.height * 0.027)
        }
        Row {
            width: parent.width
            spacing: 8
            Repeater {
                model: [0, 5, 15, 30, 60]
                delegate: SettingButton {
                    required property int modelData
                    objectName: "sleep_" + modelData
                    width: (parent.width - 32) / 5
                    label: modelData === 0 ? "Never" : modelData + " min"
                    onTapped: preferences.saveSleep(modelData)
                }
            }
        }
        Text {
            width: parent.width
            wrapMode: Text.Wrap
            text: !preferences ? "" : preferences.sleepMessage || "Applies now and next launch. 30-second warning; gyro ignored."
            color: "#8fb4d0"
            font.pixelSize: Math.max(12, panel.height * 0.022)
        }
        Row {
            width: parent.width
            spacing: 8
            SettingButton {
                objectName: "hapticsToggle"
                width: (parent.width - 24) / 4
                label: "Haptics: " + (preferences && preferences.hapticsEnabled ? "On" : "Off")
                onTapped: preferences.toggleHaptics()
            }
            Repeater {
                model: ["quiet", "normal", "strong"]
                delegate: SettingButton {
                    required property string modelData
                    objectName: "haptics_" + modelData
                    width: (parent.width - 24) / 4
                    label: (preferences && preferences.hapticsStrength === modelData ? "✓ " : "") + modelData.charAt(0).toUpperCase() + modelData.slice(1)
                    onTapped: preferences.saveHapticsStrength(modelData)
                }
            }
        }
        Text {
            width: parent.width
            wrapMode: Text.Wrap
            text: "Saved for this and future sessions. Two short buzzes on controller handoff; two long on client disconnect, after USB release (may be delayed). No speaker audio."
            color: "#8fb4d0"
            font.pixelSize: Math.max(12, panel.height * 0.022)
        }
        Rectangle {
            objectName: "sessionSeparator"
            width: parent.width
            height: 1
            color: "#52738e"
        }
        SettingButton {
            objectName: "sessionKeyboard"
            label: (preferences && preferences.keyboardEnabled ? "Stop" : "Start") + " keyboard for this session ONLY"
            enabled: preferences !== null && preferences.canToggle
            onTapped: preferences.toggleKeyboard()
        }
        Text {
            width: parent.width
            wrapMode: Text.Wrap
            text: "Live keyboard startup is available in the GUI only.\nController + keyboard sharing requires a VirtualHere license."
            color: "#8fb4d0"
            font.pixelSize: Math.max(12, panel.height * 0.022)
        }
        Text {
            objectName: "settingsMessage"
            width: parent.width
            wrapMode: Text.Wrap
            text: preferences ? preferences.message : ""
            color: "#eaf2f8"
            font.pixelSize: Math.max(13, panel.height * 0.026)
        }
        SettingButton {
            objectName: "closeSettings"
            label: "Close settings"
            onTapped: panel.closed()
        }
    }
    }
}
