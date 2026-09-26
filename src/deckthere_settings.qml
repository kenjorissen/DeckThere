import QtQuick
import QtQuick.Window

Window {
    id: settingsWindow
    required property var preferences
    width: 1280
    height: 800
    visible: true
    visibility: Window.FullScreen
    title: "DeckThere Settings"
    color: "black"
    DeckThereSettings {
        anchors.fill: parent
        preferences: settingsWindow.preferences
        onClosed: Qt.quit()
    }
}
