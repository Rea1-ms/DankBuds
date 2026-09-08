import QtQuick
import Quickshell.Io
import qs.Common
import qs.Modules.Plugins

PluginComponent {
    id: root

    property bool stopping: false
    readonly property bool listeningEnabled: pluginData.listeningEnabled !== false
    readonly property string backendPath: Paths.strip(Qt.resolvedUrl("dank_buds.py"))

    onListeningEnabledChanged: {
        if (!listeningEnabled)
            backendRestart.stop();
    }

    Process {
        id: backendProcess

        command: ["python3", root.backendPath, "backend"]
        running: root.listeningEnabled

        stderr: SplitParser {
            onRead: line => console.warn("Dank Buds:", line)
        }

        onExited: exitCode => {
            if (!root.stopping && root.listeningEnabled) {
                console.warn("Dank Buds backend exited:", exitCode);
                backendRestart.start();
            }
        }
    }

    Timer {
        id: backendRestart

        interval: 2000
        onTriggered: {
            if (root.listeningEnabled)
                backendProcess.running = true;
        }
    }

    Component.onDestruction: {
        stopping = true;
        backendRestart.stop();
        if (backendProcess.running)
            backendProcess.signal(15);
    }
}
