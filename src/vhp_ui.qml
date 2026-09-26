import QtQuick
import QtQuick.Window

Window {
    id: window
    required property var vhp
    property var preferences: null
    property bool settingsOpen: false
    onSettingsOpenChanged: {
        if (settingsOpen) {
            keypadTouch.clearTouches();
            layoutChooserOpen = false;
        }
    }
    width: 1280
    height: 800
    visible: true
    visibility: Window.FullScreen
    color: "#000000"
    title: "VirtualHerePad"

    property bool keyboardOpen: false
    property bool layoutChooserOpen: false
    property string layoutFilter: ""
    onLayoutChooserOpenChanged: {
        if (layoutChooserOpen) keypadTouch.clearTouches();
    }
    onKeyboardOpenChanged: {
        if (!keyboardOpen) keypadTouch.clearTouches();
    }
    onActiveChanged: {
        if (!active) keypadTouch.clearTouches();
    }
    // Mirrors vhp.columns. Declared here so both the hit-testing maths and the
    // key widths use one name; an undefined divisor silently made every key zero
    // wide, which drew a correctly-counted, completely invisible keyboard.
    readonly property int columns: vhp.columns

    // Exact touch maths: map a point to the key that owns its grid cell. This
    // mirrors vhp_keyboard.layout_grid, where every row spans `columns` cells.
    function codeAt(x, y, w, h) {
        if (w <= 0 || h <= 0 || x < 0 || y < 0 || x >= w || y >= h) return -1;
        var rows = vhp.rows;
        var rowHeight = h / rows.length;
        var row = Math.floor(y / rowHeight);
        if (row < 0 || row >= rows.length) {
            return -1;
        }
        var cell = Math.floor(x * window.columns / w);
        var keys = rows[row];
        var acc = 0;
        for (var i = 0; i < keys.length; i++) {
            acc += keys[i].span;
            if (cell < acc) {
                return keys[i].code;
            }
        }
        return -1;
    }

    component FlatButton: Rectangle {
        id: button
        property string text: ""
        property bool highlighted: false
        signal tapped

        radius: height * 0.22
        color: highlighted ? "#1c6b3a" : (buttonMouse.pressed ? "#2b3a4a" : "#16202b")
        border.color: highlighted ? "#43d17a" : "#3a4c60"
        border.width: 2

        Text {
            anchors.fill: parent
            anchors.margins: 5
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            fontSizeMode: Text.Fit
            minimumPixelSize: 10
            text: button.text
            color: "#e8f1f8"
            font.bold: true
            font.pixelSize: Math.max(13, button.height * 0.32)
        }
        MouseArea {
            id: buttonMouse
            anchors.fill: parent
            onClicked: button.tapped()
        }
    }

    component HoldButton: Rectangle {
        id: hold
        property string text: ""
        property int holdMilliseconds: 2000
        property real progress: 0
        signal held

        function cancelHold() {
            fill.stop();
            hold.progress = 0;
            holdTimer.stop();
        }

        Connections {
            target: window
            function onActiveChanged() {
                if (!window.active) hold.cancelHold();
            }
        }

        radius: height * 0.22
        color: "#1b1214"
        border.color: holdArea.pressed ? "#e06c6c" : "#7a3030"
        border.width: 2
        clip: true

        NumberAnimation {
            id: fill
            target: hold
            property: "progress"
            from: 0
            to: 1
            duration: hold.holdMilliseconds
        }

        Rectangle {
            anchors.left: parent.left
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            width: hold.width * hold.progress
            color: "#a13333"
        }

        Text {
            anchors.centerIn: parent
            text: holdArea.pressed ? "KEEP HOLDING" : hold.text
            color: "#f6e7e7"
            font.bold: true
            font.pixelSize: Math.max(12, hold.height * 0.28)
        }

        MouseArea {
            id: holdArea
            anchors.fill: parent
            onPressed: {
                hold.progress = 0;
                fill.restart();
                holdTimer.restart();
            }
            onReleased: hold.cancelHold()
            onCanceled: hold.cancelHold()
            onExited: hold.cancelHold()
        }

        Timer {
            id: holdTimer
            interval: hold.holdMilliseconds
            onTriggered: {
                if (holdArea.pressed && holdArea.containsMouse) hold.held();
            }
        }
    }

    Item {
        id: topBar
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        height: Math.max(64, window.height * 0.12)

        FlatButton {
            id: toggleButton
            objectName: "keyboardToggle"
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.verticalCenter: parent.verticalCenter
            width: Math.min(parent.width * 0.36, 420)
            height: parent.height * 0.68
            highlighted: window.keyboardOpen
            enabled: vhp.keyboardEnabled
            opacity: enabled ? 1 : 0.6
            text: !vhp.keyboardEnabled ? "KEYBOARD NOT RUNNING" : (window.keyboardOpen ? "HIDE KEYBOARD" : "KEYBOARD")
            onTapped: window.keyboardOpen = !window.keyboardOpen
        }

        Text {
            id: statusText
            anchors.left: parent.left
            anchors.leftMargin: 16
            anchors.verticalCenter: parent.verticalCenter
            width: Math.max(0, toggleButton.x - 32)
            elide: Text.ElideRight
            color: vhp.connected ? ((!vhp.keyboardEnabled || vhp.shared) ? "#43d17a" : "#c9a227") : "#c05a5a"
            font.pixelSize: Math.max(13, topBar.height * 0.22)
            font.bold: true
            text: vhp.connected ? (!vhp.keyboardEnabled ? "SERVER RUNNING" : (vhp.shared ? "PC KEYBOARD ACTIVE" : "WAITING FOR PC")) : "BACKEND OFFLINE"
        }

        HoldButton {
            id: quitButton
            objectName: "holdQuit"
            anchors.right: parent.right
            anchors.rightMargin: 16
            anchors.verticalCenter: parent.verticalCenter
            width: Math.min(parent.width * 0.22, 220)
            height: parent.height * 0.68
            text: "HOLD 2s TO QUIT"
            onHeld: {
                vhp.stop();
                Qt.quit();
            }
        }
    }

    Column {
        anchors.centerIn: parent
        width: parent.width * 0.9
        spacing: window.height * 0.035
        visible: !window.keyboardOpen || !vhp.keyboardEnabled

        Text {
            width: parent.width
            horizontalAlignment: Text.AlignHCenter
            text: "VIRTUALHEREPAD"
            color: "#61b8ef"
            font.bold: true
            font.pixelSize: window.height * 0.065
        }
        Row {
            width: parent.width
            Text {
                width: parent.width / 2
                horizontalAlignment: Text.AlignHCenter
                text: vhp.dashboard.clock
                color: "#da8de8"
                font.pixelSize: window.height * 0.12
            }
            Text {
                width: parent.width / 2
                horizontalAlignment: Text.AlignHCenter
                text: vhp.dashboard.battery
                color: parseInt(vhp.dashboard.battery) <= 15 ? "#e06c6c" :
                       (parseInt(vhp.dashboard.battery) <= 30 ? "#c9a227" : "#43d17a")
                font.pixelSize: window.height * 0.12
            }
        }
        Text {
            width: parent.width
            horizontalAlignment: Text.AlignHCenter
            text: "Battery: " + vhp.dashboard.batteryState
            color: "#8fb4d0"
            font.pixelSize: window.height * 0.028
        }
        Text {
            width: parent.width
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            text: (vhp.stopping ? "SHUTTING DOWN" : (vhp.connected ? "SERVER RUNNING" : "CONNECTING TO SERVICE")) + "\nLocal IP: " + vhp.dashboard.local + "\nTCP clients: " + vhp.dashboard.clients
            color: "#e8f1f8"
            font.pixelSize: window.height * 0.033
        }
        Text {
            width: parent.width
            horizontalAlignment: Text.AlignHCenter
            text: "Volume buttons adjust brightness\nTCP connections do not indicate controller ownership"
            color: "#8fb4d0"
            font.pixelSize: window.height * 0.023
        }
    }

    Column {
        id: keypad
        objectName: "keypad"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: topBar.bottom
        anchors.bottom: bottomBar.top
        anchors.margins: 12
        visible: window.keyboardOpen && vhp.keyboardEnabled

        Repeater {
            model: vhp.rows

            delegate: Row {
                id: keyRow
                // Declared explicitly: an unqualified modelData is not reachable as
                // `keyRow.modelData`, and an empty model silently draws nothing.
                required property var modelData
                height: keypad.height / vhp.rows.length
                spacing: 0

                Repeater {
                    model: keyRow.modelData

                    delegate: Item {
                        required property var modelData
                        width: keypad.width * modelData.span / Math.max(1, window.columns)
                        height: keyRow.height

                        Rectangle {
                            objectName: "keycap"
                            anchors.fill: parent
                            anchors.margins: 3
                            radius: 10
                            color: modelData.active ? "#2f6ea8" : "#1b2632"
                            border.color: modelData.active ? "#6fb6f0" : "#2d3d4d"
                            border.width: 1

                            Text {
                                objectName: "keycapLabel"
                                anchors.fill: parent
                                anchors.margins: 4
                                horizontalAlignment: Text.AlignHCenter
                                verticalAlignment: Text.AlignVCenter
                                fontSizeMode: Text.Fit
                                minimumPixelSize: 10
                                text: modelData.label
                                textFormat: Text.PlainText
                                color: "#eaf2f8"
                                font.pixelSize: Math.max(11, parent.height * 0.34)
                            }
                        }
                    }
                }
            }
        }
    }

    MultiPointTouchArea {
        id: keypadTouch
        objectName: "keypadTouch"
        anchors.fill: keypad
        enabled: window.keyboardOpen && vhp.keyboardEnabled && !window.layoutChooserOpen && !window.settingsOpen
        minimumTouchPoints: 1
        maximumTouchPoints: 10

        property var mapping: ({})

        function clearTouches() {
            mapping = ({});
            vhp.clear();
        }

        function sync(points, released) {
            var next = Object.assign({}, mapping);
            for (var i = 0; i < points.length; i++) {
                var point = points[i];
                delete next[point.pointId];
                var code = window.codeAt(point.x, point.y, keypadTouch.width, keypadTouch.height);
                if (!released && point.pressed && code !== -1) {
                    next[point.pointId] = code;
                }
            }
            // Reference counts by code: two fingers on one key must not release
            // it until the last finger leaves. Signal lists contain changed points.
            var before = ({}), after = ({});
            for (var id in mapping) before[mapping[id]] = true;
            for (var id2 in next) after[next[id2]] = true;
            for (var oldCode in before) {
                if (!(oldCode in after)) vhp.release(Number(oldCode));
            }
            for (var newCode in after) {
                if (!(newCode in before)) vhp.press(Number(newCode));
            }
            mapping = next;
        }

        onPressed: function(points) { sync(points, false); }
        onUpdated: function(points) { sync(points, false); }
        onReleased: function(points) { sync(points, true); }
        onCanceled: clearTouches()
        Component.onDestruction: {
            for (var id in mapping) {
                vhp.release(mapping[id]);
            }
        }
    }

    Item {
        id: bottomBar
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: Math.max(62, window.height * 0.11)

        Text {
            anchors.left: parent.left
            anchors.leftMargin: 16
            anchors.verticalCenter: parent.verticalCenter
            color: vhp.connected ? "#8fb4d0" : "#c05a5a"
            font.pixelSize: Math.max(13, bottomBar.height * 0.22)
            text: "Brightness " + vhp.percent + "%"
        }

        Row {
            anchors.right: parent.right
            anchors.rightMargin: 16
            anchors.verticalCenter: parent.verticalCenter
            spacing: 10

            FlatButton {
                objectName: "settingsButton"
                height: bottomBar.height * 0.66
                width: bottomBar.width * 0.16
                text: "SETTINGS"
                enabled: window.preferences !== null
                onTapped: window.settingsOpen = true
            }

            FlatButton {
                objectName: "layoutButton"
                height: bottomBar.height * 0.52
                width: Math.min(320, bottomBar.width * 0.32)
                anchors.verticalCenter: parent.verticalCenter
                enabled: vhp.connected
                opacity: enabled ? 1 : 0.5
                text: "Layout: " + vhp.layoutName
                onTapped: {
                    window.layoutFilter = "";
                    window.layoutChooserOpen = true;
                }
            }

            FlatButton {
                height: bottomBar.height * 0.66
                width: bottomBar.width * 0.17
                text: "RELEASE KEYS"
                onTapped: vhp.clear()
            }
        }
    }

    Loader {
        anchors.fill: parent
        z: 30
        active: window.settingsOpen && window.preferences !== null
        sourceComponent: Component {
            VhpSettings {
                objectName: "settingsPanel"
                preferences: window.preferences
                onClosed: window.settingsOpen = false
            }
        }
    }

    Rectangle {
        id: layoutChooser
        objectName: "layoutChooser"
        anchors.fill: parent
        color: "#e6000000"
        z: 20
        visible: window.layoutChooserOpen
        MouseArea { anchors.fill: parent } // Block input to the keyboard underneath.

        Rectangle {
            id: layoutPanel
            anchors.centerIn: parent
            width: parent.width * 0.94
            height: parent.height * 0.86
            radius: 16
            color: "#111b25"
            border.color: "#3a4c60"

            Text {
                id: layoutHeading
                anchors.left: parent.left
                anchors.top: parent.top
                anchors.margins: 18
                color: "#e8f1f8"
                text: "Keyboard layout / input method"
                font.bold: true
                font.pixelSize: 24
            }
            FlatButton {
                objectName: "layoutDone"
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: 12
                width: 110
                height: 44
                text: "DONE"
                onTapped: window.layoutChooserOpen = false
            }
            Text {
                id: layoutHelp
                anchors.top: layoutHeading.bottom
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.margins: 18
                wrapMode: Text.Wrap
                color: "#8fb4d0"
                font.pixelSize: 16
                text: "Match the active layout or IME on your PC. This changes Deck labels only—not the PC's settings. IME composition and candidates stay on the PC. Selection is remembered; there is no automatic detection."
            }
            Rectangle {
                id: searchBox
                anchors.top: layoutHelp.bottom
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.margins: 14
                height: 42
                color: "#070f17"
                radius: 6
                border.color: "#3a4c60"
                Text {
                    anchors.fill: parent
                    anchors.margins: 10
                    verticalAlignment: Text.AlignVCenter
                    text: "Scroll to choose, or filter by name with a local keyboard"
                    color: "#7792a8"
                    font.pixelSize: 16
                    visible: layoutSearch.text.length === 0
                }
                TextInput {
                    id: layoutSearch
                    objectName: "layoutSearch"
                    anchors.fill: parent
                    anchors.margins: 10
                    verticalAlignment: Text.AlignVCenter
                    color: "#e8f1f8"
                    font.pixelSize: 18
                    clip: true
                    maximumLength: 80
                    text: window.layoutFilter
                    onTextEdited: window.layoutFilter = text
                    Keys.onEscapePressed: window.layoutChooserOpen = false
                }
            }
            Text {
                id: layoutDetails
                objectName: "layoutDetails"
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.margins: 18
                height: Math.max(94, implicitHeight)
                wrapMode: Text.Wrap
                color: "#b2c9db"
                font.pixelSize: 15
                text: vhp.layoutName + "\n" + vhp.layoutNote + "\nMapping data is not a guarantee of hardware-tested compatibility."
            }
            Flickable {
                id: layoutList
                objectName: "layoutList"
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: searchBox.bottom
                anchors.bottom: layoutDetails.top
                anchors.margins: 14
                contentWidth: width
                contentHeight: layoutGrid.height
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                flickableDirection: Flickable.VerticalFlick
                onVisibleChanged: if (visible) contentY = 0

                Grid {
                    id: layoutGrid
                    width: parent.width
                    columns: 2
                    spacing: 10
                    Repeater {
                        model: window.layoutChooserOpen ? vhp.layoutNames.filter(function(entry) {
                            return (entry.label + " " + entry.id).toLowerCase().indexOf(window.layoutFilter.toLowerCase()) !== -1;
                        }) : []
                        onCountChanged: layoutList.contentY = 0
                        delegate: FlatButton {
                            required property var modelData
                            objectName: "layoutChoice-" + modelData.id
                            width: (layoutGrid.width - layoutGrid.spacing) / 2
                            height: 60
                            highlighted: modelData.id === vhp.layout
                            enabled: vhp.connected
                            text: (modelData.kind === "ime" ? "IME · " : "") + modelData.label
                            onTapped: {
                                keypadTouch.clearTouches();
                                vhp.setLayout(modelData.id);
                            }
                        }
                    }
                }
            }
            Rectangle {
                anchors.right: layoutList.right
                y: layoutList.y + layoutList.visibleArea.yPosition * layoutList.height
                width: 4
                height: Math.max(24, layoutList.visibleArea.heightRatio * layoutList.height)
                color: "#61b8ef"
                radius: 2
                visible: layoutList.contentHeight > layoutList.height
            }
        }
    }
}
