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

    Column {
        anchors.centerIn: parent
        width: Math.min(parent.width * 0.9, 820)
        spacing: Math.max(6, panel.height * 0.015)
        Text {
            text: "SETTINGS"
            color: "#61b8ef"
            font.bold: true
            font.pixelSize: panel.height * 0.04
        }
        Text {
            width: parent.width
            text: "Next launch: " + (preferences.mode === "keyboard" ? "GUI + keyboard" : preferences.mode === "gui" ? "GUI — no keyboard" : "Terminal")
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
                    enabled: !preferences.busy
                    label: modelData.label
                    onTapped: preferences.save(modelData.mode)
                }
            }
        }
        SettingButton {
            objectName: "startKeyboardOnce"
            label: "Start keyboard this session"
            enabled: preferences.canStart
            onTapped: preferences.startKeyboard(false)
        }
        SettingButton {
            objectName: "startKeyboardDefault"
            label: "Start keyboard and enable by default"
            enabled: preferences.canStart
            onTapped: preferences.startKeyboard(true)
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
            text: preferences.message
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
