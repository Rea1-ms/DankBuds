import QtQuick
import qs.Common

QtObject {
    function check(done) {
        Proc.runCommand(
            "dankBuds.dependencyCheck",
            [
                "python3",
                "-c",
                "import dbus; from gi.repository import GLib"
            ],
            (stdout, exitCode) => {
                if (exitCode === 0) {
                    done(null);
                    return;
                }
                done({
                    "title": "Dank Buds dependencies are unavailable",
                    "details": "Install Python 3, python-dbus, PyGObject, and BlueZ, then make sure the Bluetooth service is running before re-enabling the plugin."
                });
            }
        );
    }
}
