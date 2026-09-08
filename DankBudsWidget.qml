import QtQuick
import Quickshell.Io
import qs.Common
import qs.Widgets
import qs.Modules.Plugins

PluginComponent {
    id: root

    layerNamespacePlugin: "dank-buds"

    property bool connected: false
    property bool ready: false
    property string deviceName: "MOONDROP Robin"
    property int leftBattery: -1
    property int rightBattery: -1
    property string noiseMode: ""
    property string errorMessage: ""
    property bool stopping: false

    readonly property string clientPath: Paths.strip(Qt.resolvedUrl("dank_buds.py"))
    readonly property bool listeningEnabled: pluginData.listeningEnabled !== false
    readonly property var noiseOptions: [
        {"mode": "off", "icon": "anc-off", "label": I18n.tr("Off")},
        {"mode": "anc", "icon": "anc-on", "label": I18n.tr("Noise cancellation")},
        {"mode": "transparency", "icon": "transparency", "label": I18n.tr("Transparency")}
    ]
    readonly property int modeIndex: {
        if (noiseMode === "off")
            return 0;
        if (noiseMode === "anc")
            return 1;
        if (noiseMode === "transparency")
            return 2;
        return -1;
    }

    function applyState(line) {
        try {
            const state = JSON.parse(line);
            connected = state.connected === true;
            ready = state.ready === true;
            deviceName = state.name || "MOONDROP Robin";
            leftBattery = Number.isInteger(state.left) ? state.left : -1;
            rightBattery = Number.isInteger(state.right) ? state.right : -1;
            noiseMode = state.mode || "";
            errorMessage = state.error || "";
            setVisibilityOverride(connected || !listeningEnabled);
        } catch (error) {
            console.warn("Dank Buds state error:", error);
        }
    }

    function sendCommand(action, value) {
        if (!clientProcess.running)
            return;
        const command = {"action": action};
        if (value !== undefined)
            command.value = value;
        clientProcess.write(JSON.stringify(command) + "\n");
    }

    function setListening(enabled) {
        if (pluginService?.savePluginData)
            pluginService.savePluginData(pluginId, "listeningEnabled", enabled);
    }

    onListeningEnabledChanged: setVisibilityOverride(connected || !listeningEnabled)

    Process {
        id: clientProcess

        command: ["python3", root.clientPath, "watch"]
        running: true
        stdinEnabled: true

        stdout: SplitParser {
            onRead: line => root.applyState(line)
        }

        stderr: SplitParser {
            onRead: line => console.warn("Dank Buds client:", line)
        }

        onExited: exitCode => {
            root.connected = false;
            root.ready = false;
            root.setVisibilityOverride(!root.listeningEnabled);
            if (!root.stopping) {
                console.warn("Dank Buds client exited:", exitCode);
                clientRestart.start();
            }
        }
    }

    Timer {
        id: clientRestart

        interval: 2000
        onTriggered: clientProcess.running = true
    }

    Component.onCompleted: setVisibilityOverride(!listeningEnabled)

    Component.onDestruction: {
        stopping = true;
        clientRestart.stop();
        if (clientProcess.running)
            clientProcess.signal(15);
    }

    horizontalBarPill: Component {
        DankIcon {
            name: "headphones"
            size: root.iconSize
            color: root.ready ? Theme.primary : Theme.surfaceVariantText
        }
    }

    verticalBarPill: Component {
        DankIcon {
            name: "headphones"
            size: root.iconSize
            color: root.ready ? Theme.primary : Theme.surfaceVariantText
        }
    }

    popoutContent: Component {
        PopoutComponent {
            id: content

            headerText: root.deviceName
            detailsText: !root.listeningEnabled ? I18n.tr("Listener paused; BudsLink can take control") : root.errorMessage || (root.ready ? I18n.tr("Battery and noise control") : I18n.tr("Connecting to earbuds…"))
            showCloseButton: true

            Column {
                width: parent.width
                spacing: Theme.spacingM

                Row {
                    anchors.horizontalCenter: parent.horizontalCenter
                    spacing: Theme.spacingXL

                    BudsBattery {
                        level: root.leftBattery
                        iconName: "earbuds-left"
                    }

                    BudsBattery {
                        level: root.rightBattery
                        iconName: "earbuds-right"
                    }
                }

                StyledText {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: I18n.tr("Noise control")
                    color: Theme.surfaceText
                    font.pixelSize: Theme.fontSizeMedium
                    font.weight: Font.Medium
                }

                Row {
                    anchors.horizontalCenter: parent.horizontalCenter
                    spacing: Theme.spacingS

                    Repeater {
                        model: root.noiseOptions

                        delegate: Rectangle {
                            id: modeButton

                            required property var modelData
                            readonly property bool selected: root.noiseMode === modelData.mode

                            width: 82
                            height: 52
                            radius: Theme.cornerRadius
                            color: selected ? Theme.buttonBg : Theme.surfaceContainerHigh
                            opacity: root.ready ? 1 : 0.5

                            Behavior on color {
                                DankColorAnim {
                                    duration: Theme.shortDuration
                                }
                            }

                            BudsIcon {
                                anchors.centerIn: parent
                                name: modeButton.modelData.icon
                                size: 28
                                color: modeButton.selected ? Theme.buttonText : Theme.surfaceText
                            }

                            StateLayer {
                                disabled: !root.ready
                                stateColor: modeButton.selected ? Theme.buttonText : Theme.primary
                                cornerRadius: modeButton.radius
                                tooltipText: modeButton.modelData.label

                                onClicked: {
                                    if (modeButton.selected)
                                        return;
                                    root.noiseMode = modeButton.modelData.mode;
                                    root.sendCommand("set_mode", modeButton.modelData.mode);
                                }
                            }
                        }
                    }
                }

                Row {
                    anchors.horizontalCenter: parent.horizontalCenter
                    spacing: Theme.spacingS

                    StyledText {
                        anchors.verticalCenter: parent.verticalCenter
                        text: I18n.tr("Listening")
                        color: Theme.surfaceText
                        font.pixelSize: Theme.fontSizeMedium
                    }

                    DankToggle {
                        anchors.verticalCenter: parent.verticalCenter
                        checked: root.listeningEnabled
                        hideText: true

                        onToggled: checked => root.setListening(checked)
                    }
                }
            }
        }
    }

    popoutWidth: 360
    popoutHeight: 300
}
