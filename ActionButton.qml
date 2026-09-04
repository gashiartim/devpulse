import QtQuick
import qs.Commons
import qs.Ui

BorderSurface {
  id: root

  property string text: ""
  property string tooltipText: ""
  property color foreground: Color.foreground
  property color accent: Color.accent

  signal clicked()

  implicitWidth: label.implicitWidth + Style.space(12)
  implicitHeight: Style.space(28)
  radius: Style.cornerRadius

  readonly property bool hot: mouse.containsMouse && root.enabled

  color: hot ? Style.hoverFillFor(foreground, accent) : "transparent"
  borderSpec: hot
    ? Border.controlSpec("hover-cursor", foreground, accent)
    : Border.none()

  Behavior on color { ColorAnimation { duration: 60 } }

  Text {
    id: label
    anchors.centerIn: parent
    textFormat: Text.PlainText
    text: root.text
    color: root.enabled
      ? (root.hot ? root.accent : root.foreground)
      : Qt.darker(root.foreground, 2.0)
    font.family: Style.font.family
    font.pixelSize: Style.font.caption
    elide: Text.ElideRight
  }

  MouseArea {
    id: mouse
    anchors.fill: parent
    enabled: root.enabled
    hoverEnabled: true
    cursorShape: root.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
    onClicked: root.clicked()
  }

  PanelToolTip {
    visible: root.tooltipText !== "" && mouse.containsMouse
    text: root.tooltipText
    fontFamily: Style.font.family
  }
}
