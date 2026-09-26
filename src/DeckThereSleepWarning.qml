import QtQuick
import QtQuick.Window

Rectangle {
    id: warning
    objectName: "sleepWarningBanner"
    required property var preferences
    readonly property bool windowShown: Window.window !== null && Window.window.visible && Window.window.visibility !== Window.Minimized
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.top: parent.top
    height: Math.max(64, parent.height * 0.13)
    z: 100
    visible: preferences !== null && preferences.sleepWarning
    color: "#923a17"
    border.color: "#ffc172"
    Text {
        anchors.fill: parent
        anchors.margins: 10
        text: warning.preferences ? warning.preferences.sleepMessage + "\nTap here to cancel — sharing stops before sleep" : ""
        color: "white"
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        font.pixelSize: Math.max(14, warning.height * 0.23)
        fontSizeMode: Text.Fit
        wrapMode: Text.Wrap
    }
    MouseArea { anchors.fill: parent; onClicked: warning.preferences.cancelSleep() }
    Timer {
        interval: 500
        repeat: true
        running: warning.visible && warning.windowShown
        triggeredOnStart: true
        onTriggered: warning.preferences.confirmSleepWarning()
    }
}
