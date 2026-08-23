import QtQuick
import qs.Common
import qs.Widgets

Item {
    id: root

    required property int level
    required property string iconName
    property color accentColor: Theme.primary

    readonly property bool hasLevel: level >= 0
    property real animatedLevel: hasLevel ? level : 0

    width: 88
    height: 100

    onLevelChanged: animatedLevel = hasLevel ? Math.max(0, Math.min(100, level)) : 0

    Behavior on animatedLevel {
        NumberAnimation {
            duration: Theme.mediumDuration
            easing.type: Easing.OutCubic
        }
    }

    Item {
        id: ring

        anchors.top: parent.top
        anchors.horizontalCenter: parent.horizontalCenter
        width: 72
        height: 72

        Canvas {
            id: ringCanvas

            anchors.fill: parent

            onPaint: {
                const context = getContext("2d");
                const center = width / 2;
                const radius = center - 5;
                const startAngle = -Math.PI / 2;
                const endAngle = startAngle + 2 * Math.PI;
                const progressAngle = startAngle + 2 * Math.PI * root.animatedLevel / 100;

                context.reset();
                context.lineCap = "round";
                context.lineWidth = 6;

                context.beginPath();
                context.arc(center, center, radius, startAngle, endAngle);
                context.strokeStyle = Theme.withAlpha(root.accentColor, 0.18);
                context.stroke();

                if (root.hasLevel && root.animatedLevel > 0) {
                    context.beginPath();
                    context.arc(center, center, radius, startAngle, progressAngle);
                    context.strokeStyle = root.accentColor;
                    context.stroke();
                }
            }

            Connections {
                target: root

                function onAnimatedLevelChanged() {
                    ringCanvas.requestPaint();
                }

                function onAccentColorChanged() {
                    ringCanvas.requestPaint();
                }

                function onHasLevelChanged() {
                    ringCanvas.requestPaint();
                }
            }

            Component.onCompleted: requestPaint()
        }

        BudsIcon {
            anchors.centerIn: parent
            name: root.iconName
            size: 30
            color: root.hasLevel ? Theme.surfaceText : Theme.surfaceVariantText
        }
    }

    StyledText {
        anchors.top: ring.bottom
        anchors.topMargin: Theme.spacingXS
        anchors.horizontalCenter: parent.horizontalCenter
        text: root.hasLevel ? root.level + "%" : "—"
        color: Theme.surfaceText
        font.pixelSize: Theme.fontSizeMedium
        font.weight: Font.Bold
    }
}
