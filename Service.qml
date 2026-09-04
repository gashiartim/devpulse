import QtQuick
import Quickshell
import Quickshell.Io

// Long-lived, headless state for DevPulse. The shell creates one service
// instance; the bar widget and its popup only render this shared snapshot.
Item {
  id: root

  property var shell: null
  property var manifest: null
  property string omarchyPath: ""
  property var servers: []
  property bool scanning: false
  property bool queuedRefresh: false
  property string scanOutput: ""
  property string lastMessage: ""
  property int pendingStopPort: 0
  property bool panelOpen: false
  property bool includeContainers: true
  property string includedPorts: ""
  property string ignoredPorts: ""
  property int refreshIntervalSec: 30
  property int activeIntervalSec: 3

  readonly property int scanIntervalMs: (panelOpen ? activeIntervalSec : refreshIntervalSec) * 1000
  readonly property string pluginDir: Quickshell.env("HOME") + "/.config/omarchy/plugins/io.github.gashiartim.devpulse"
  readonly property string scannerPath: pluginDir + "/scripts/scan-servers.py"
  readonly property string gitInfoPath: pluginDir + "/scripts/git-info.py"
  readonly property string stopServerPath: pluginDir + "/scripts/stop-server.py"

  property var gitCache: ({})
  property double lastGitScanMs: 0

  function boundedSeconds(value, fallback, minimum, maximum) {
    var parsed = Math.floor(Number(value))
    if (!Number.isFinite(parsed)) return fallback
    return Math.max(minimum, Math.min(maximum, parsed))
  }

  function configure(values) {
    var config = values || ({})
    includeContainers = config.includeContainers !== false
    includedPorts = String(config.includedPorts || "").slice(0, 256)
    ignoredPorts = String(config.ignoredPorts || "").slice(0, 256)
    refreshIntervalSec = boundedSeconds(config.refreshIntervalSec, 30, 10, 300)
    activeIntervalSec = boundedSeconds(config.activeIntervalSec, 3, 2, 30)
  }

  function setPanelOpen(value) {
    var next = value === true
    if (panelOpen === next) return
    panelOpen = next
    if (next) refreshNow()
  }

  function refreshNow() {
    if (scanProcess.running) {
      queuedRefresh = true
      return
    }
    scanning = true
    scanOutput = ""
    var config = JSON.stringify({ includedPorts: includedPorts, ignoredPorts: ignoredPorts })
    scanProcess.command = includeContainers
      ? [scannerPath, "--include-containers", "--config", config]
      : [scannerPath, "--config", config]
    scanProcess.running = true
  }

  function normalizeServers(raw) {
    if (!Array.isArray(raw)) return []
    var result = []
    for (var i = 0; i < raw.length; i++) {
      var item = raw[i]
      if (!item || !Number.isFinite(Number(item.pid)) || !Number.isFinite(Number(item.port))) continue
      var server = {}
      for (var key in item) server[key] = item[key]
      var cached = gitCache[String(server.projectRoot || server.cwd || "")]
      if (cached) {
        server.gitAvailable = cached.available === true
        server.gitBranch = String(cached.branch || "")
        server.gitDirty = cached.dirty === true
      }
      result.push(server)
    }
    return result
  }

  function finishScanCycle() {
    scanning = false
    if (queuedRefresh) {
      queuedRefresh = false
      Qt.callLater(refreshNow)
    }
  }

  function applyScan(raw) {
    var parsed = null
    try { parsed = JSON.parse(String(raw || "[]")) } catch (e) {
      console.warn("DevPulse: scanner returned invalid JSON", e)
    }
    if (!Array.isArray(parsed)) {
      setMessage("Server scan failed — keeping the last snapshot")
      finishScanCycle()
      return
    }
    servers = normalizeServers(parsed)
    finishScanCycle()
    if (Date.now() - lastGitScanMs >= 15000) refreshGit()
  }

  function refreshGit() {
    if (gitProcess.running) return
    var paths = []
    var seen = ({})
    for (var i = 0; i < servers.length; i++) {
      var path = String(servers[i].projectRoot || servers[i].cwd || "")
      if (!path || seen[path]) continue
      seen[path] = true
      paths.push(path)
    }
    if (paths.length === 0) {
      gitCache = ({})
      return
    }
    lastGitScanMs = Date.now()
    gitProcess.command = ["python3", gitInfoPath, JSON.stringify(paths)]
    gitProcess.running = true
  }

  function applyGit(raw) {
    var parsed = ({})
    try { parsed = JSON.parse(String(raw || "{}")) } catch (e) {
      console.warn("DevPulse: git metadata returned invalid JSON", e)
      return
    }
    gitCache = parsed
    servers = normalizeServers(servers)
  }

  function setMessage(message) {
    lastMessage = String(message || "")
    messageTimer.restart()
  }

  function stopServer(server) {
    if (stopProcess.running) {
      setMessage("A stop request is already running")
      return
    }
    if (!server || !Number.isFinite(Number(server.pid))) return
    var pid = Math.floor(Number(server.pid))
    if (pid <= 1) return
    if (!String(server.startTime || "").match(/^\d+$/)) {
      setMessage("Process changed — refresh required")
      return
    }
    pendingStopPort = Math.max(0, Math.floor(Number(server.port || 0)))
    stopProcess.command = [stopServerPath, String(pid), String(server.startTime), String(pendingStopPort)]
    stopProcess.running = true
  }

  Timer {
    id: scanTimer
    interval: root.scanIntervalMs
    repeat: true
    running: true
    triggeredOnStart: true
    onTriggered: root.refreshNow()
  }

  Timer {
    id: messageTimer
    interval: 2200
    onTriggered: root.lastMessage = ""
  }

  Process {
    id: scanProcess
    command: [root.scannerPath]
    onExited: function(exitCode) {
      if (exitCode === 0) root.applyScan(root.scanOutput)
      else {
        root.setMessage("Server scan failed — keeping the last snapshot")
        root.finishScanCycle()
      }
    }
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.scanOutput = text
    }
    stderr: StdioCollector { waitForEnd: true }
  }

  Process {
    id: gitProcess
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.applyGit(text)
    }
    stderr: StdioCollector { waitForEnd: true }
  }

  Process {
    id: stopProcess
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var result = null
        try { result = JSON.parse(String(text || "{}")) } catch (e) {}
        if (result && result.ok === true) root.setMessage("Stopping server on :" + String(root.pendingStopPort || "server"))
        else if (result && result.reason === "process-changed") root.setMessage("Process changed — refresh required")
        else if (result && result.reason === "process-gone") root.setMessage("Server already stopped")
        else if (result && result.reason === "listener-changed") root.setMessage("Listener changed — refresh required")
        else root.setMessage("Could not stop that process")
        Qt.callLater(root.refreshNow)
      }
    }
    stderr: StdioCollector { waitForEnd: true }
  }
}
