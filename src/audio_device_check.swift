import Foundation
// Delegate to the same read-only CoreAudio implementation as the recorder.
let recorder = URL(fileURLWithPath: #filePath).deletingLastPathComponent().appendingPathComponent("native_recorder.swift")
let process = Process()
process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
process.arguments = ["swift", recorder.path, "--inspect"]
process.standardOutput = FileHandle.standardOutput
process.standardError = FileHandle.standardError
try process.run()
process.waitUntilExit()
exit(process.terminationStatus)
