import QtQuick
import qs.Modules.Plugins

PluginSettings {
    pluginId: "dankBuds"

    ToggleSetting {
        width: parent.width
        settingKey: "listeningEnabled"
        label: "Listen for Robin"
        description: "Release the control channel before using BudsLink"
        defaultValue: true
    }
}
