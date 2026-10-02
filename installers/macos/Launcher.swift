import AppKit
import Foundation
import Darwin

// Each bootstrap owns a new process group. Closing the installer stops its runtime.
final class ChildGroup {
    private(set) var pid: pid_t = 0
    private var pipe: Pipe?
    func start(script: String, arguments: [String], environment: [String: String], output: @escaping (String) -> Void, completion: @escaping (Int32) -> Void) throws {
        let stream = Pipe()
        var actions: posix_spawn_file_actions_t?
        var attributes: posix_spawnattr_t?
        posix_spawn_file_actions_init(&actions)
        posix_spawnattr_init(&attributes)
        defer { posix_spawn_file_actions_destroy(&actions); posix_spawnattr_destroy(&attributes) }
        posix_spawn_file_actions_adddup2(&actions, stream.fileHandleForWriting.fileDescriptor, STDOUT_FILENO)
        posix_spawn_file_actions_adddup2(&actions, stream.fileHandleForWriting.fileDescriptor, STDERR_FILENO)
        posix_spawn_file_actions_addclose(&actions, stream.fileHandleForReading.fileDescriptor)
        posix_spawn_file_actions_addopen(&actions, STDIN_FILENO, "/dev/null", O_RDONLY, 0)
        posix_spawnattr_setflags(&attributes, Int16(POSIX_SPAWN_SETPGROUP))
        posix_spawnattr_setpgroup(&attributes, 0)
        let strings = ["/bin/bash", script] + arguments
        let argv = strings.map { strdup($0) } + [nil]
        let envp = environment.map { strdup("\($0.key)=\($0.value)") } + [nil]
        defer { argv.forEach { free($0) }; envp.forEach { free($0) } }
        var child: pid_t = 0
        let result = argv.withUnsafeBufferPointer { av in envp.withUnsafeBufferPointer { ev in
            posix_spawn(&child, "/bin/bash", &actions, &attributes, UnsafeMutablePointer(mutating: av.baseAddress!), UnsafeMutablePointer(mutating: ev.baseAddress!))
        }}
        guard result == 0 else { throw NSError(domain: NSPOSIXErrorDomain, code: Int(result)) }
        pid = child
        pipe = stream
        stream.fileHandleForWriting.closeFile()
        stream.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if data.isEmpty { handle.readabilityHandler = nil; return }
            DispatchQueue.main.async { output(String(decoding: data, as: UTF8.self)) }
        }
        DispatchQueue.global(qos: .utility).async {
            var status: Int32 = 0
            while waitpid(child, &status, 0) == -1 && errno == EINTR {}
            let code = (status & 0x7f) == 0 ? (status >> 8) & 0xff : 128 + (status & 0x7f)
            DispatchQueue.main.async {
                stream.fileHandleForReading.readabilityHandler = nil
                self.pid = 0
                completion(code)
            }
        }
    }
    func stop(immediate: Bool = false) {
        guard pid > 0 else { return }
        let group = pid
        kill(-group, immediate ? SIGKILL : SIGTERM)
        if !immediate { DispatchQueue.main.asyncAfter(deadline: .now() + 2) {
            if self.pid == group { kill(-group, SIGKILL) }
        }}
    }
}

final class Installer: NSObject, NSApplicationDelegate, NSWindowDelegate {
    let child = ChildGroup()
    var window: NSWindow!
    let heading = NSTextField(labelWithString: "Set up med-lit")
    let status = NSTextField(wrappingLabelWithString: "Preparing your local review workspace…")
    let progress = NSProgressIndicator()
    let action = NSButton(title: "Cancel", target: nil, action: nil)
    let detailButton = NSButton(title: "Details", target: nil, action: nil)
    let details = NSTextView()
    let scroll = NSScrollView()
    var running = false
    var cancelled = false
    var succeeded = false
    var buffer = ""
    var log = ""
    var testRoot: URL?
    var startError: String?

    func applicationDidFinishLaunching(_ notification: Notification) {
        let args = Array(CommandLine.arguments.dropFirst())
        if !args.isEmpty {
            if args.count == 2 && args[0] == "--test-state-dir" && args[1].hasPrefix("/") {
                testRoot = URL(fileURLWithPath: args[1], isDirectory: true).standardizedFileURL
            } else { startError = "The developer test option requires --test-state-dir and an absolute folder path." }
        }
        buildWindow()
        NSApp.activate(ignoringOtherApps: true)
        start()
    }
    func buildWindow() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 560, height: 280), styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        window.title = "med-lit Installer"
        window.center()
        window.delegate = self
        let view = window.contentView!
        heading.font = .systemFont(ofSize: 24, weight: .semibold)
        heading.frame = NSRect(x: 32, y: 214, width: 496, height: 35)
        status.frame = NSRect(x: 32, y: 133, width: 496, height: 62)
        status.font = .systemFont(ofSize: 14)
        progress.frame = NSRect(x: 32, y: 112, width: 496, height: 12)
        progress.style = .bar
        progress.isIndeterminate = true
        let note = NSTextField(wrappingLabelWithString: "Your email and optional keys are entered in a local browser form. Reviews stay on this Mac.")
        note.frame = NSRect(x: 32, y: 54, width: 496, height: 40)
        note.font = .systemFont(ofSize: 12)
        note.textColor = .secondaryLabelColor
        action.frame = NSRect(x: 410, y: 12, width: 118, height: 32)
        action.bezelStyle = .rounded
        action.target = self; action.action = #selector(act)
        detailButton.frame = NSRect(x: 25, y: 12, width: 90, height: 32)
        detailButton.bezelStyle = .rounded
        detailButton.target = self; detailButton.action = #selector(toggleDetails)
        details.isEditable = false
        details.font = .monospacedSystemFont(ofSize: 11, weight: .regular)
        details.textContainerInset = NSSize(width: 10, height: 10)
        scroll.hasVerticalScroller = true
        scroll.borderType = .bezelBorder
        scroll.documentView = details
        scroll.frame = NSRect(x: 32, y: 280, width: 496, height: 180)
        scroll.isHidden = true
        for element in [heading, status, progress, note, action, detailButton, scroll] { view.addSubview(element) }
        window.makeKeyAndOrderFront(nil)
    }
    func start() {
        guard !running else { return }
        if let error = startError { fail(error); return }
        guard let resources = Bundle.main.resourceURL,
              let data = try? Data(contentsOf: resources.appendingPathComponent("manifest.json")),
              let manifest = try? JSONSerialization.jsonObject(with: data) as? [String: String],
              let wheel = manifest["wheel"], !wheel.contains("/"), wheel.hasSuffix(".whl"),
              let digest = manifest["wheel_sha256"], digest.count == 64,
              let version = manifest["version"] else { fail("The installer package is incomplete. Download it again."); return }
        var environment = ProcessInfo.processInfo.environment
        if let root = testRoot {
            do {
                try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
                environment["MED_LIT_CONFIG_DIR"] = root.appendingPathComponent("config").path
                environment["MED_LIT_STATE_DIR"] = root.appendingPathComponent("state").path
                environment["MED_LIT_PROJECTS_DIR"] = root.appendingPathComponent("reviews").path
                environment["CODEX_HOME"] = root.appendingPathComponent("codex").path
                environment["MED_LIT_RUNTIME_DIR"] = root.appendingPathComponent("runtime").path
                environment["MED_LIT_INSTALLER_FORCE_RUNTIME"] = "1"
                environment["UV_CACHE_DIR"] = root.appendingPathComponent("uv-cache").path
                environment["UV_PYTHON_INSTALL_DIR"] = root.appendingPathComponent("python").path
                environment["UV_TOOL_DIR"] = root.appendingPathComponent("uv-tools").path
            } catch { fail("Cannot use the isolated test folder. Choose a writable folder."); return }
        }
        environment["PYTHONUNBUFFERED"] = "1"
        running = true; cancelled = false; succeeded = false; buffer = ""; log = ""
        details.string = ""
        heading.stringValue = "Set up med-lit"
        status.stringValue = "Preparing your local review workspace…"
        action.title = "Cancel"
        progress.startAnimation(nil)
        do {
            try child.start(script: resources.appendingPathComponent("bootstrap.sh").path, arguments: [resources.appendingPathComponent(wheel).path, digest, version], environment: environment, output: { [weak self] text in self?.receive(text) }, completion: { [weak self] code in self?.finish(code) })
        } catch { running = false; fail("Could not start setup. Reopen the installer to retry.") }
    }
    func receive(_ text: String) {
        guard running else { return }
        log += text
        if log.count > 24000 { log = String(log.suffix(24000)) }
        details.string = log
        details.scrollToEndOfDocument(nil)
        buffer += text
        while let newline = buffer.firstIndex(of: "\n") {
            let line = String(buffer[..<newline]); buffer.removeSubrange(...newline)
            if line == "MED_LIT_PHASE:runtime" { status.stringValue = "Finding or installing the verified runtime…" }
            if line == "MED_LIT_PHASE:package" { status.stringValue = "Saving the bundled med-lit package on this Mac…" }
            if line == "MED_LIT_PHASE:settings" { status.stringValue = "Preparing Python and med-lit. Complete the settings form when your browser opens." }
            if line.hasPrefix("med-lit settings: ") { status.stringValue = "Complete the form in your browser, then select Save and connect. Keep this window open." }
        }
    }
    func finish(_ code: Int32) {
        running = false
        progress.stopAnimation(nil)
        if cancelled {
            heading.stringValue = "Setup closed"
            status.stringValue = "Reopen setup when you’re ready to continue. Any settings already saved are kept."
            action.title = "Retry"
        } else if code == 0 {
            succeeded = true
            heading.stringValue = "med-lit is ready"
            status.stringValue = "Your settings are saved and the server connection passed verification. Follow the next steps in your browser."
            action.title = "Close"
        } else {
            heading.stringValue = "Setup did not finish"
            status.stringValue = "Check your internet connection and retry. Details may explain the problem. Any settings already saved are kept."
            action.title = "Retry"
        }
    }
    func fail(_ message: String) {
        progress.stopAnimation(nil)
        heading.stringValue = "Could not start med-lit"
        status.stringValue = message
        action.title = "Retry"
    }
    @objc func act() {
        if running { cancelled = true; action.isEnabled = false; child.stop(); status.stringValue = "Closing setup…"; DispatchQueue.main.asyncAfter(deadline: .now() + 2.1) { self.action.isEnabled = true } }
        else if succeeded { NSApp.terminate(nil) }
        else { start() }
    }
    @objc func toggleDetails() {
        scroll.isHidden.toggle()
        detailButton.title = scroll.isHidden ? "Details" : "Hide details"
        let height: CGFloat = scroll.isHidden ? 280 : 480
        // The normal controls stay at the bottom when diagnostic output is shown.
        var frame = window.frame
        frame.size.height = height + 28
        window.setFrame(frame, display: true, animate: true)
    }
    func windowShouldClose(_ sender: NSWindow) -> Bool { child.stop(immediate: true); return true }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationWillTerminate(_ notification: Notification) { child.stop(immediate: true) }
}
#if !MED_LIT_CHILD_GROUP_TEST
let application = NSApplication.shared
let delegate = Installer()
application.setActivationPolicy(.regular)
application.delegate = delegate
application.run()

#endif
