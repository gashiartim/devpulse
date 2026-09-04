import QtQuick
import QtQuick.Controls
import Quickshell
import qs.Commons
import qs.Ui

Panel {
  id: root
  moduleName: "io.github.gashiartim.devpulse"
  ipcTarget: "io.github.gashiartim.devpulse"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  property var service: null
  property bool openedFromHotkey: false
  property string filterText: ""
  property int selectedIndex: -1
  property string selectedServerId: ""
  property bool cursorActive: false
  property bool confirmOpen: false
  property var pendingStopServer: null
  property double nowMs: Date.now()

  readonly property var barIdentity: hostWidget || root
  readonly property var allServers: service ? service.servers : []
  readonly property var serverList: filterServers(allServers, filterText)
  readonly property int serverCount: serverList.length
  readonly property int totalServerCount: allServers.length
  readonly property int exposedCount: countFlagged(allServers, "exposed")
  readonly property int unhealthyCount: countFlagged(allServers, "unhealthy")
  readonly property bool searchVisible: totalServerCount > 4 || filterText !== ""
  readonly property color foreground: bar ? bar.barForeground : Color.foreground
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property bool showMetrics: setting("showMetrics", true) !== false
  readonly property bool showGit: setting("showGit", true) !== false
  readonly property var selectedServer: selectedIndex >= 0 && selectedIndex < serverList.length
    ? serverList[selectedIndex] : null

  function filterServers(servers, query) {
    if (!Array.isArray(servers)) return []
    var needle = String(query || "").trim().toLowerCase()
    if (!needle) return servers
    return servers.filter(function(server) {
      var haystack = [
        server.projectName, server.framework, server.runtime, server.port,
        server.cwd, server.projectRoot, server.command, server.commandLine,
        server.gitBranch, server.bindAddress, server.source, server.containerName,
        server.healthStatus, server.containerStatus
      ].join(" ").toLowerCase()
      return haystack.indexOf(needle) >= 0
    })
  }

  function countFlagged(servers, flag) {
    if (!Array.isArray(servers)) return 0
    var count = 0
    for (var i = 0; i < servers.length; i++)
      if (servers[i] && servers[i][flag] === true) count++
    return count
  }

  function focusSearch() {
    if (!searchVisible) return
    searchField.forceActiveFocus()
    searchField.selectAll()
  }

  function leaveSearch(clear) {
    if (clear) filterText = ""
    searchField.focus = false
    keyCatcher.forceActiveFocus()
  }

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

  function serverIdentity(server) {
    if (!server) return ""
    return String(server.id || String(server.pid || "") + ":" + String(server.port || ""))
  }

  function indexForServerId(identity) {
    if (!identity) return -1
    for (var i = 0; i < serverList.length; i++) {
      if (serverIdentity(serverList[i]) === identity) return i
    }
    return -1
  }

  function clampSelection() {
    if (serverList.length === 0) {
      selectedIndex = -1
      selectedServerId = ""
      cursorActive = false
      return
    }

    var preservedIndex = indexForServerId(selectedServerId)
    selectedIndex = preservedIndex >= 0
      ? preservedIndex
      : Math.max(0, Math.min(serverList.length - 1, selectedIndex))
    selectedServerId = serverIdentity(serverList[selectedIndex])
  }

  function selectIndex(index, activateCursor) {
    if (serverList.length === 0) return
    selectedIndex = Math.max(0, Math.min(serverList.length - 1, index))
    selectedServerId = serverIdentity(serverList[selectedIndex])
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
    if (server.httpAvailable !== true) {
      if (service) service.setMessage("No HTTP response from :" + String(server.port || ""))
      return
    }
    Quickshell.execDetached(["omarchy-launch-browser", String(server.url)])
    root.close()
  }

  function terminalSelected() {
    var server = root.selectedServer
    if (!server || !server.cwd) return
    var cwd = String(server.cwd)
    Quickshell.execDetached([
      "xdg-terminal-exec", "--", "sh", "-c",
      "cd -- \"$1\" || exit 1; exec \"${SHELL:-/bin/sh}\"",
      "devpulse", cwd
    ])
  }

  function editorSelected() {
    var server = root.selectedServer
    if (!server || !server.cwd) return
    Quickshell.execDetached(["omarchy-launch-editor", String(server.cwd)])
  }

  function copySelected() {
    var server = root.selectedServer
    if (!server) return
    var value = server.httpAvailable === true
      ? String(server.url)
      : String((server.bindAddress && server.bindAddress !== "0.0.0.0") ? server.bindAddress : "localhost") + ":" + String(server.port)
    Quickshell.execDetached(["wl-copy", value])
    if (service) service.setMessage("Copied " + value)
  }

  function requestStop() {
    if (!root.selectedServer || root.selectedServer.canStop === false || root.confirmOpen) return
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
  onServiceChanged: if (service && typeof service.setPanelOpen === "function") service.setPanelOpen(opened)
  onOpenedChanged: {
    if (service && typeof service.setPanelOpen === "function") service.setPanelOpen(opened)
    if (opened) {
      clampSelection()
      nowMs = Date.now()
    } else {
      filterText = ""
    }
  }
  Component.onDestruction: if (service && typeof service.setPanelOpen === "function") service.setPanelOpen(false)

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
      blocked: root.confirmOpen || searchField.activeFocus

      onMoveRequested: function(dx, dy) { if (dy !== 0) root.moveSelection(dy) }
      onActivateRequested: root.openSelected()
      onCloseRequested: root.close()
      onDeleteRequested: root.requestStop()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(text) {
        if (text === "r" || text === "R") { if (root.service) root.service.refreshNow() }
        else if (text === "c" || text === "C") root.copySelected()
        else if (text === "t" || text === "T") root.terminalSelected()
        else if (text === "e" || text === "E") root.editorSelected()
        else if (text === "/") root.focusSearch()
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
            meta: root.service && root.service.stopping
              ? "Stopping selected target…"
              : (root.service && root.service.scanning
                ? "Scanning local listeners…"
                : (root.totalServerCount === 0
                ? "No development servers"
                : String(root.totalServerCount) + " development server" + (root.totalServerCount === 1 ? "" : "s")
                  + (root.unhealthyCount > 0 ? " · " + String(root.unhealthyCount) + " unhealthy" : "")
                  + (root.exposedCount > 0 ? " · " + String(root.exposedCount) + " LAN exposed" : "")))
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

          TextField {
            id: searchField
            width: parent.width
            visible: root.searchVisible
            placeholderText: "Search project, framework, port, branch, or path…"
            text: root.filterText
            foreground: root.foreground
            accent: Color.accent
            onTextChanged: if (root.filterText !== text) root.filterText = text
            Keys.onPressed: function(event) {
              if (event.key === Qt.Key_Escape) {
                root.leaveSearch(root.filterText !== "")
                event.accepted = true
              } else if (event.key === Qt.Key_Down || event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                root.leaveSearch(false)
                root.selectIndex(0, true)
                event.accepted = true
              }
            }
          }

          Text {
            width: parent.width
            visible: root.totalServerCount === 0 && !(root.service && root.service.scanning)
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

          Text {
            width: parent.width
            visible: root.totalServerCount > 0 && root.serverCount === 0
            topPadding: Style.space(18)
            bottomPadding: Style.space(18)
            textFormat: Text.PlainText
            text: "No servers match “" + root.filterText + "”"
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            horizontalAlignment: Text.AlignHCenter
            elide: Text.ElideRight
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
                    color: modelData.exposed === true || modelData.unhealthy === true
                      ? (bar ? bar.urgent : Color.urgent)
                      : (root.cursorActive && serverRow.current ? Color.accent : Color.foreground)
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
                    + (modelData.unhealthy === true ? " · unhealthy" : "")
                    + (modelData.exposed === true ? " · LAN exposed" : "")
                    + (modelData.httpAvailable === true ? "" : " · no HTTP response")
                  color: modelData.exposed === true || modelData.unhealthy === true
                    ? (bar ? bar.urgent : Color.urgent)
                    : root.dim
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
            readonly property real actionWidth: (width - spacing * 5) / 6

            ActionButton {
              width: actionRow.actionWidth
              text: "↵ Open"
              tooltipText: "Open in browser"
              foreground: root.dim
              enabled: root.selectedServer !== null && root.selectedServer.httpAvailable === true
              onClicked: root.openSelected()
            }

            ActionButton {
              width: actionRow.actionWidth
              text: "t Term"
              tooltipText: "Open project terminal"
              foreground: root.dim
              enabled: root.selectedServer !== null && !!root.selectedServer.cwd
              onClicked: root.terminalSelected()
            }

            ActionButton {
              width: actionRow.actionWidth
              text: "e Edit"
              tooltipText: "Open project in the default editor"
              foreground: root.dim
              enabled: root.selectedServer !== null && !!root.selectedServer.cwd
              onClicked: root.editorSelected()
            }

            ActionButton {
              width: actionRow.actionWidth
              text: "c Copy"
              tooltipText: "Copy web URL or listener address"
              foreground: root.dim
              enabled: root.selectedServer !== null
              onClicked: root.copySelected()
            }

            ActionButton {
              width: actionRow.actionWidth
              text: root.service && root.service.stopping ? "… Stop" : "x Stop"
              tooltipText: root.selectedServer && root.selectedServer.source === "docker"
                ? "Stop Docker container"
                : "Stop server process"
              foreground: root.dim
              accent: bar ? bar.urgent : Color.urgent
              enabled: root.selectedServer !== null && root.selectedServer.canStop !== false
                && !(root.service && root.service.stopping)
              onClicked: root.requestStop()
            }

            ActionButton {
              width: actionRow.actionWidth
              text: "r Scan"
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
            ? "Stop " + (root.pendingStopServer.source === "docker" ? "container " : "")
              + String(root.pendingStopServer.projectName || "this server")
              + " on :" + String(root.pendingStopServer.port || "") + "?"
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

  function formatDuration(seconds) {
    var value = Math.max(0, Math.floor(Number(seconds) || 0))
    if (value < 60) return value + "s"
    if (value < 3600) return Math.floor(value / 60) + "m"
    if (value < 86400) return Math.floor(value / 3600) + "h " + Math.floor((value % 3600) / 60) + "m"
    return Math.floor(value / 86400) + "d " + Math.floor((value % 86400) / 3600) + "h"
  }

  function metricsText(server) {
    if (!server) return ""
    if (server.source === "docker") return "Container · " + String(server.containerStatus || server.runningFor || "running")
    var cpu = Number(server.cpu)
    var cpuText = isFinite(cpu) ? cpu.toFixed(1) + "%" : "--"
    return cpuText + " · " + formatMemory(server.memoryBytes) + " · " + formatDuration(server.uptimeSeconds)
  }
}
