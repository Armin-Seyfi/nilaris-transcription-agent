import AVFoundation
import Foundation

let engine = AVAudioEngine()
let input = engine.inputNode
let format = input.outputFormat(forBus: 0)

guard format.channelCount == 3 else {
    fatalError(
        "Expected 3 channels from Nilaris Audio Input. " +
        "Found \(format.channelCount). Check macOS Sound Input."
    )
}

let timestamp = ISO8601DateFormatter()
    .string(from: Date())
    .replacingOccurrences(of: ":", with: "-")

let outputURL = URL(
    fileURLWithPath: "recordings/nilaris_\(timestamp).caf"
)

let file = try AVAudioFile(
    forWriting: outputURL,
    settings: format.settings
)

print("Nilaris Native Recorder")
print("Sample Rate: \(format.sampleRate) Hz")
print("Channels: \(format.channelCount)")
print("Output: \(outputURL.path)")

// Shared failure state between the audio callback and main thread.
let failureLock = NSLock()
var audioWriteFailure: String?
let stopSemaphore = DispatchSemaphore(value: 0)

input.installTap(
    onBus: 0,
    bufferSize: 1024,
    format: format
) { buffer, _ in
    do {
        try file.write(from: buffer)
    } catch {
        failureLock.lock()
        if audioWriteFailure == nil {
            audioWriteFailure = String(describing: error)
            stopSemaphore.signal()
        }
        failureLock.unlock()
    }
}

try engine.start()

// Signal that audio capture has successfully started.
print("NILARIS_READY")
fflush(stdout)

print("\nRecording started.")
print("Waiting for STOP command.\n")

// Hard safety cap: 9 hours.
let hardLimitSeconds: TimeInterval = 9 * 60 * 60
let recordingStarted = Date()

// Read STOP commands without blocking the safety timer.
DispatchQueue.global(qos: .userInitiated).async {
    while let command = readLine() {
        if command.trimmingCharacters(in: .whitespacesAndNewlines)
            .uppercased() == "STOP" {
            stopSemaphore.signal()
            return
        }
    }
}

// Wait until STOP arrives or the 9-hour limit is reached.
let waitResult = stopSemaphore.wait(
    timeout: .now() + hardLimitSeconds
)

if waitResult == .timedOut {
    print("NILARIS_AUTO_STOP: Hard recording limit reached.")
}

input.removeTap(onBus: 0)
engine.stop()

failureLock.lock()
let failure = audioWriteFailure
failureLock.unlock()

if let failure = failure {
    print("NILARIS_AUDIO_WRITE_ERROR: \(failure)")
    fflush(stdout)
    exit(EXIT_FAILURE)
}

print("Recording stopped.")
print("Saved: \(outputURL.path)")
