import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

BarWidget {
  id: root
  moduleName: "io.github.gashiartim.devpulse"

  property var service: null

  readonly property int serverCount: service ? service.servers.length : 0
  readonly property int exposedCount: countServersByFlag("exposed")
  readonly property int unhealthyCount: countServersByFlag("unhealthy")
  readonly property bool warnExposed: setting("warnExposed", true) !== false
  readonly property string displayLabel: "󰆍 " + String(serverCount)
  readonly property string tooltipLabel: {
    var base = serverCount === 1
      ? "1 development server running"
      : String(serverCount) + " development servers running"
    if (unhealthyCount > 0)
      base += "\n" + String(unhealthyCount) + " unhealthy container" + (unhealthyCount === 1 ? "" : "s")
    if (exposedCount > 0)
      base += "\n" + String(exposedCount) + " reachable from the local network"
    return base
  }

  function countServersByFlag(flag) {
    if (!service || !Array.isArray(service.servers)) return 0
    var count = 0
    for (var i = 0; i < service.servers.length; i++)
      if (service.servers[i] && service.servers[i][flag] === true) count++
    return count
  }

  function configureService() {
    if (!service || typeof service.configure !== "function") return
    service.configure({
      includeContainers: setting("includeContainers", true),
      includedPorts: setting("includedPorts", ""),
      ignoredPorts: setting("ignoredPorts", ""),
      refreshIntervalSec: setting("refreshIntervalSec", 30),
      activeIntervalSec: setting("activeIntervalSec", 3)
    })
  }

  function resolveService() {
    if (bar && bar.shell && typeof bar.shell.serviceFor === "function")
      service = bar.shell.serviceFor("io.github.gashiartim.devpulse")
    configureService()
    injectPanel()
  }

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
    if ("service" in target) target.service = root.service
  }

  function refresh() {
    if (root.service) root.service.refreshNow()
  }

  function togglePanel() {
    if (panelLoader.item && panelLoader.item.toggle) panelLoader.item.toggle()
  }

  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false
  readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing === true : false

  function open() {
    if (panelLoader.item && panelLoader.item.openFromHotkey) panelLoader.item.openFromHotkey()
    else if (panelLoader.item && panelLoader.item.open) panelLoader.item.open()
  }

  function close() {
    if (panelLoader.item && panelLoader.item.close) panelLoader.item.close()
  }

  function closeForPopoutSwitch() {
    if (panelLoader.item && panelLoader.item.closeForPopoutSwitch) panelLoader.item.closeForPopoutSwitch()
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onBarChanged: {
    resolveService()
    injectPanel()
  }
  onSettingsChanged: {
    configureService()
    injectPanel()
  }
  onServiceChanged: {
    configureService()
    injectPanel()
  }

  Timer {
    interval: 500
    repeat: true
    running: root.service === null
    onTriggered: root.resolveService()
  }

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  IpcHandler {
    target: "io.github.gashiartim.devpulse"
    function refresh(): string { root.refresh(); return "ok" }
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.togglePanel() }
  }

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.bar && root.bar.vertical ? "󰆍" : root.displayLabel
    labelVisible: true
    active: root.unhealthyCount > 0 || (root.warnExposed && root.exposedCount > 0)
    tooltipText: root.tooltipLabel
    horizontalMargin: 7.5
    verticalPadding: 6

    onPressed: function(buttonCode) {
      if (buttonCode === Qt.MiddleButton) root.refresh()
      else if (buttonCode === Qt.RightButton) root.open()
      else root.togglePanel()
    }
  }
}
