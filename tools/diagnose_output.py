#!/usr/bin/env python3
"""Read-only CoreAudio output diagnosis using the installed recorder's helpers.

Does not record, select an output, create aggregate devices, or call any API.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile

SWIFT_REPORT = r'''
do {
    let current: AudioObjectID = try value(systemObject, kAudioHardwarePropertyDefaultOutputDevice, AudioObjectID.self)
    let known: [UInt32: String] = [
        kAudioDeviceTransportTypeBuiltIn: "built_in", kAudioDeviceTransportTypeUSB: "usb",
        kAudioDeviceTransportTypeBluetooth: "bluetooth", kAudioDeviceTransportTypeBluetoothLE: "bluetooth_le",
        kAudioDeviceTransportTypeAggregate: "aggregate", kAudioDeviceTransportTypeVirtual: "virtual"]
    var report: [[String: Any]] = []
    for id in try objectIDs(systemObject, kAudioHardwarePropertyDevices) {
        let count = try? channelCount(id, kAudioObjectPropertyScopeOutput)
        let name = (try? stringValue(id, kAudioObjectPropertyName)) ?? "unreadable"
        guard id == current || (count ?? 0) > 0 || name.lowercased().contains("wh-") else { continue }
        let transport = try? value(id, kAudioDevicePropertyTransportType, UInt32.self)
        let alive = try? value(id, kAudioDevicePropertyDeviceIsAlive, UInt32.self)
        report.append([
            "id": id, "name": name, "is_default_output": id == current,
            "uid": (try? stringValue(id, kAudioDevicePropertyDeviceUID)) ?? "unreadable",
            "output_channels": count.map { Int($0) } ?? -1,
            "alive": alive.map { Int($0) } ?? -1,
            "transport": transport.map { Int($0) } ?? -1,
            "transport_name": transport.flatMap { known[$0] } ?? "other_or_unreadable"])
    }
    let inventory = try devices()
    var selection: [String: Any]
    do { selection = ["device": try chooseListeningDevice(inventory, current: current).json] }
    catch { selection = ["error": String(describing: error)] }
    let result: [String: Any] = ["default_output_id": current, "outputs": report,
        "recorder_selection": selection, "settings_changed": false,
        "recording_started": false, "api_request_sent": false]
    print(String(data: try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys, .prettyPrinted]), encoding: .utf8)!)
} catch { print("DIAGNOSTIC_ERROR: \(error)"); exit(1) }
'''


def build_source(recorder):
    source = Path(recorder).read_text(encoding='utf-8')
    marker = '\ndo {\n    if CommandLine.arguments.contains("--self-test")'
    if source.count(marker) != 1:
        raise ValueError('Recorder version is not supported; no actions performed.')
    return source.split(marker)[0] + '\n' + SWIFT_REPORT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recorder', type=Path, default=Path.home()/'Applications/Nilaris/src/native_recorder.swift')
    args = parser.parse_args()
    source = build_source(args.recorder)
    with tempfile.TemporaryDirectory(prefix='nilaris-output-diagnostic-') as directory:
        path = Path(directory)/'diagnose.swift'
        path.write_text(source, encoding='utf-8')
        result = subprocess.run(['/usr/bin/swift', '-module-cache-path', str(Path(tempfile.gettempdir())/'nilaris-swift-module-cache'), str(path)], timeout=120)
        return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
