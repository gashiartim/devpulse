import QtQuick
import QtQuick.Controls
import Quickshell
import qs.Commons
import qs.Ui

Panel {
  id: root
  moduleName: "artim.devpulse"
  ipcTarget: "artim.devpulse"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  property var service: null
  property bool openedFromHotkey: false
  property int selectedIndex: -1
  property bool cursorActive: false
  property bool confirmOpen: false
  property var pendingStopServer: null
  property double nowMs: Date.now()

  readonly property var barIdentity: hostWidget || root
  readonly property var serverList: service ? service.servers : []
  readonly property int serverCount: serverList.length
  readonly property color foreground: bar ? bar.barForeground : Color.foreground
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property bool showMetrics: setting("showMetrics", true) !== false
  readonly property bool showGit: setting("showGit", true) !== false
  readonly property var selectedServer: selectedIndex >= 0 && selectedIndex < serverList.length
    ? serverList[selectedIndex] : null

  function setCenterHoverRevealSuppressed(value) {
    if (bar && "centerHoverRevealSuppressed" in bar) bar.centerHoverRevealSuppressed = value
  }

  function open() {
    openedFromHotkey = false
    setCenterHoverRevealSuppressed(false)
    if (service) service.refreshNow()
    root.controller.show()
    Qt.callLater(function() {
      if (root.opened) keyCatcher.forceActiveFocus()
    })
  }

  function openFromHotkey() {
    openedFromHotkey = true
    root.controller.show()
    if (service) service.refreshNow()
    Qt.callLater(function() {
      if (root.opened) {
        setCenterHoverRevealSuppressed(true)
        keyCatcher.forceActiveFocus()
      }
    })
  }

  function close() {
    confirmOpen = false
    pendingStopServer = null
    setCenterHoverRevealSuppressed(false)
    root.controller.hide()
  }

  function toggle() { root.opened ? root.close() : root.open() }

  function switchPanel(direction) {
    if (bar && typeof bar.switchPanelFrom === "function") return bar.switchPanelFrom(root.barIdentity, direction)
    return false
  }

  function clampSelection() {
    if (serverList.length === 0) {
      selectedIndex = -1
      cursorActive = false
    } else if (selectedIndex < 0 || selectedIndex >= serverList.length) {
      selectedIndex = 0
    }
  }

  function selectIndex(index, activateCursor) {
    if (serverList.length === 0) return
    selectedIndex = Math.max(0, Math.min(serverList.length - 1, index))
    if (activateCursor) cursorActive = true
    if (serverListView) serverListView.positionViewAtIndex(selectedIndex, ListView.Contain)
  }

  function moveSelection(delta) {
    if (serverList.length === 0) return
    if (!cursorActive) {
      cursorActive = true
      selectIndex(delta > 0 ? 0 : serverList.length - 1, false)
      return
    }
    selectIndex((selectedIndex + delta + serverList.length) % serverList.length, false)
  }

  function openSelected() {
    var server = root.selectedServer
    if (!server) return
    Quickshell.execDetached(["omarchy-launch-browser", String(server.url || "http://localhost:" + server.port)])
    root.close()
  }

  function terminalSelected() {
    var server = root.selectedServer
    if (!server || !server.cwd) return
    Quickshell.execDetached(["xdg-terminal-exec", "--dir=" + String(server.cwd)])
  }

  function copySelected() {
    var server = root.selectedServer
    if (!server) return
    Quickshell.execDetached(["wl-copy", String(server.url || "http://localhost:" + server.port)])
    if (service) service.setMessage("Copied " + String(server.url || "localhost:" + server.port))
  }

  function requestStop() {
    if (!root.selectedServer || root.confirmOpen) return
    pendingStopServer = root.selectedServer
    confirmOpen = true
    Qt.callLater(function() { if (confirmOpen) confirmOverlay.forceActiveFocus() })
  }

  function cancelStop() {
    confirmOpen = false
    pendingStopServer = null
    Qt.callLater(function() { if (root.opened) keyCatcher.forceActiveFocus() })
  }

  function confirmStop() {
    var server = pendingStopServer
    confirmOpen = false
    pendingStopServer = null
    if (service && server) service.stopServer(server)
    Qt.callLater(function() { if (root.opened) keyCatcher.forceActiveFocus() })
  }

  onServerListChanged: clampSelection()
  onOpenedChanged: if (opened) {
    clampSelection()
    nowMs = Date.now()
  }

  Timer {
    interval: 30000
    repeat: true
    running: root.opened
    onTriggered: root.nowMs = Date.now()
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(460))
    contentHeight: panel.fittedContentHeight(contentColumn.implicitHeight, Style.space(680))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      blocked: root.confirmOpen

      onMoveRequested: function(dx, dy) { if (dy !== 0) root.moveSelection(dy) }
      onActivateRequested: root.openSelected()
      onCloseRequested: root.close()
      onDeleteRequested: root.requestStop()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(text) {
        if (text === "r" || text === "R") { if (root.service) root.service.refreshNow() }
        else if (text === "c" || text === "C") root.copySelected()
        else if (text === "t" || text === "T") root.terminalSelected()
      }

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: contentColumn.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: contentColumn
          width: panelFlick.width
          spacing: Style.space(10)

          PanelHero {
            width: parent.width
            title: "DevPulse"
            meta: root.service && root.service.scanning
              ? "Scanning local listeners…"
              : (root.serverCount === 0
                ? "No development servers"
                : String(root.serverCount) + " development server" + (root.serverCount === 1 ? "" : "s"))
            foreground: root.foreground
            fontFamily: root.fontFamily
            iconComponent: Component {
              Text {
                textFormat: Text.PlainText
                text: "󰆍"
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.display
              }
            }
          }

          Text {
            width: parent.width
            visible: root.service && root.service.lastMessage !== ""
            textFormat: Text.PlainText
            text: root.service ? root.service.lastMessage : ""
            color: Color.accent
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            elide: Text.ElideRight
          }

          Text {
            width: parent.width
            visible: root.serverCount === 0 && !(root.service && root.service.scanning)
            topPadding: Style.space(22)
            bottomPadding: Style.space(22)
            textFormat: Text.PlainText
            text: "No development servers running\n\nStart a local dev server and it will\nappear here automatically."
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
          }

          ListView {
            id: serverListView
            width: parent.width
            visible: root.serverCount > 0
            implicitHeight: Math.min(contentHeight, Style.space(520))
            height: implicitHeight
            model: root.serverList
            spacing: Style.space(5)
            clip: true
            interactive: contentHeight > height
            boundsBehavior: Flickable.StopAtBounds
            delegate: CursorSurface {
              id: serverRow
              required property var modelData
              required property int index
              width: serverListView.width
              height: Style.space(root.showGit || root.showMetrics ? 98 : 82)
              foreground: root.foreground
              accent: Color.accent
              current: index === root.selectedIndex
              hasCursor: root.cursorActive && current

              MouseArea {
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onEntered: root.selectIndex(index, true)
                onClicked: root.selectIndex(index, true)
              }

              Column {
                anchors.fill: parent
                anchors.leftMargin: Style.space(12)
                anchors.rightMargin: Style.space(12)
                anchors.topMargin: Style.space(8)
                anchors.bottomMargin: Style.space(8)
                spacing: Style.space(2)

                Row {
                  width: parent.width
                  spacing: Style.space(6)

                  Text {
                    textFormat: Text.PlainText
                    text: "●"
                    color: root.cursorActive && serverRow.current ? Color.accent : Color.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    anchors.verticalCenter: parent.verticalCenter
                  }

                  Text {
                    width: Math.max(0, parent.width - portLabel.implicitWidth - Style.space(18))
                    textFormat: Text.PlainText
                    text: String(modelData.projectName || "Local server")
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.subtitle
                    font.bold: true
                    elide: Text.ElideRight
                    anchors.verticalCenter: parent.verticalCenter
                  }

                  Text {
                    id: portLabel
                    textFormat: Text.PlainText
                    text: ":" + String(modelData.port || "")
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.body
                    anchors.verticalCenter: parent.verticalCenter
                  }
                }

                Text {
                  width: parent.width
                  textFormat: Text.PlainText
                  text: String(modelData.framework || modelData.runtime || "Process")
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  elide: Text.ElideRight
                }

                Text {
                  width: parent.width
                  textFormat: Text.PlainText
                  text: root.compactPath(String(modelData.cwd || ""))
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  elide: Text.ElideMiddle
                }

                Row {
                  width: parent.width
                  spacing: Style.space(8)
                  visible: root.showGit || root.showMetrics

                  Text {
                    visible: root.showGit && !!modelData.gitBranch
                    textFormat: Text.PlainText
                    text: "󰘬 " + String(modelData.gitBranch || "") + (modelData.gitDirty ? "  ●" : "")
                    color: modelData.gitDirty ? Color.accent : root.dim
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    elide: Text.ElideRight
                  }

                  Text {
                    visible: root.showMetrics
                    textFormat: Text.PlainText
                    text: root.metricsText(modelData)
                    color: root.dim
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    elide: Text.ElideRight
                  }
                }
              }
            }
          }

          PanelSeparator { visible: root.serverCount > 0; foreground: root.foreground }

          Row {
            id: actionRow
            width: parent.width
            spacing: Style.space(3)
            readonly property real actionWidth: (width - spacing * 4) / 5

            ActionButton {
              width: actionRow.actionWidth
              text: "↵ Open"
              tooltipText: "Open in browser"
              foreground: root.dim
              enabled: root.selectedServer !== null
              onClicked: root.openSelected()
            }

            ActionButton {
              width: actionRow.actionWidth
              text: "t Terminal"
              tooltipText: "Open project terminal"
              foreground: root.dim
              enabled: root.selectedServer !== null && !!root.selectedServer.cwd
              onClicked: root.terminalSelected()
            }

            ActionButton {
              width: actionRow.actionWidth
              text: "c Copy"
              tooltipText: "Copy server URL"
              foreground: root.dim
              enabled: root.selectedServer !== null
              onClicked: root.copySelected()
            }

            ActionButton {
              width: actionRow.actionWidth
              text: "x Stop"
              tooltipText: "Stop server"
              foreground: root.dim
              accent: bar ? bar.urgent : Color.urgent
              enabled: root.selectedServer !== null
              onClicked: root.requestStop()
            }

            ActionButton {
              width: actionRow.actionWidth
              text: "r Refresh"
              tooltipText: "Refresh servers"
              foreground: root.dim
              enabled: root.service !== null && !root.service.scanning
              onClicked: root.service.refreshNow()
            }
          }
        }

      Item {
        id: confirmOverlay
        anchors.fill: parent
        visible: root.confirmOpen
        z: 20
        focus: visible
        onVisibleChanged: if (visible) forceActiveFocus()
        Keys.onPressed: function(event) {
          if (root.confirmOpen && confirmDialog.handleKey(event)) event.accepted = true
        }

        ConfirmDialog {
          id: confirmDialog
          anchors.fill: parent
          opened: root.confirmOpen
          message: root.pendingStopServer
            ? "Stop " + String(root.pendingStopServer.projectName || "this server") + " on :" + String(root.pendingStopServer.port || "") + "?"
            : "Stop this server?"
          confirmText: "Stop"
          background: Color.popups.background
          foreground: root.foreground
          scrim: Util.alpha(Color.popups.background, 0.82)
          selectedBackground: Style.selectedFillFor(root.foreground, Color.accent)
          selectedText: Color.accent
          fontFamily: root.fontFamily
          cornerRadius: Style.cornerRadius
          onCanceled: root.cancelStop()
          onConfirmed: root.confirmStop()
        }
      }
    }
  }
  }

  function compactPath(path) {
    var home = Quickshell.env("HOME")
    if (path === home) return "~"
    if (home && path.indexOf(home + "/") === 0) return "~" + path.slice(home.length)
    return path || "cwd unavailable"
  }

  function formatMemory(bytes) {
    var value = Number(bytes)
    if (!isFinite(value) || value <= 0) return "-- MB"
    if (value >= 1024 * 1024 * 1024) return (value / (1024 * 1024 * 1024)).toFixed(1) + " GB"
    return Math.max(1, Math.round(value / (1024 * 1024))) + " MB"
  }

  function metricsText(server) {
    if (!server) return ""
    var cpu = Number(server.cpu)
    var cpuText = isFinite(cpu) ? cpu.toFixed(1) + "%" : "--"
    return cpuText + " · " + formatMemory(server.memoryBytes)
  }
}
