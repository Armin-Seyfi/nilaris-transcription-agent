import AVFoundation
import AudioToolbox
import CoreAudio
import Foundation

struct AudioFailure: Error, CustomStringConvertible {
    let description: String
    init(_ message: String) { description = message }
}
func checked(_ status: OSStatus, _ operation: String) throws {
    if status != noErr { throw AudioFailure("\(operation): CoreAudio error \(status)") }
}
func address(_ selector: AudioObjectPropertySelector,
             _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) -> AudioObjectPropertyAddress {
    AudioObjectPropertyAddress(mSelector: selector, mScope: scope, mElement: kAudioObjectPropertyElementMain)
}
// Trivial C values only; CF references have a separate reader.
func value<T>(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector, _ type: T.Type,
              _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) throws -> T {
    var a = address(selector, scope), size = UInt32(MemoryLayout<T>.size)
    let storage = UnsafeMutableRawPointer.allocate(byteCount: Int(size), alignment: MemoryLayout<T>.alignment)
    defer { storage.deallocate() }
    try checked(AudioObjectGetPropertyData(object, &a, 0, nil, &size, storage),
                "Read property \(selector) on object \(object)")
    guard size == MemoryLayout<T>.size else { throw AudioFailure("Unexpected property size") }
    return storage.load(as: T.self)
}
func cfValue(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector) throws -> CFTypeRef {
    var a = address(selector), size = UInt32(MemoryLayout<Unmanaged<CFTypeRef>?>.size)
    var result: Unmanaged<CFTypeRef>?
    let status = withUnsafeMutablePointer(to: &result) {
        AudioObjectGetPropertyData(object, &a, 0, nil, &size, $0)
    }
    try checked(status, "Read CF property")
    guard let result else { throw AudioFailure("Missing CF property") }
    return result.takeRetainedValue()
}
func stringValue(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector) throws -> String {
    guard let result = try cfValue(object, selector) as? String else { throw AudioFailure("Invalid string property") }
    return result
}
func objectIDs(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector,
               _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) throws -> [AudioObjectID] {
    var a = address(selector, scope), size: UInt32 = 0
    try checked(AudioObjectGetPropertyDataSize(object, &a, 0, nil, &size), "Read list size")
    guard size % 4 == 0 else { throw AudioFailure("Invalid device list size") }
    if size == 0 { return [] }
    var result = [AudioObjectID](repeating: 0, count: Int(size) / 4)
    let status = result.withUnsafeMutableBytes {
        AudioObjectGetPropertyData(object, &a, 0, nil, &size, $0.baseAddress!)
    }
    try checked(status, "Read device list")
    return Array(result.prefix(Int(size) / 4))
}
func channelCount(_ object: AudioObjectID, _ scope: AudioObjectPropertyScope) throws -> Int {
    var a = address(kAudioDevicePropertyStreamConfiguration, scope), size: UInt32 = 0
    try checked(AudioObjectGetPropertyDataSize(object, &a, 0, nil, &size), "Read channel size")
    if size == 0 { return 0 }
    let storage = UnsafeMutableRawPointer.allocate(byteCount: Int(size), alignment: MemoryLayout<AudioBufferList>.alignment)
    defer { storage.deallocate() }
    try checked(AudioObjectGetPropertyData(object, &a, 0, nil, &size, storage), "Read channels")
    return UnsafeMutableAudioBufferListPointer(storage.assumingMemoryBound(to: AudioBufferList.self))
        .reduce(0) { $0 + Int($1.mNumberChannels) }
}
struct Device {
    let id: AudioObjectID, uid: String, name: String
    let inputChannels: Int, outputChannels: Int
    let transport: UInt32
    var json: [String: Any] { ["id": id, "uid": uid, "name": name,
        "input_channels": inputChannels, "output_channels": outputChannels, "transport": transport] }
}
let systemObject = AudioObjectID(kAudioObjectSystemObject)
// Verified against this Mac's working Audio MIDI Setup Multi-Output composition.
let mirroredOutputMode = 1
func devices() throws -> [Device] {
    try objectIDs(systemObject, kAudioHardwarePropertyDevices).compactMap { id in
        guard let uid = try? stringValue(id, kAudioDevicePropertyDeviceUID),
              let name = try? stringValue(id, kAudioObjectPropertyName),
              let transport = try? value(id, kAudioDevicePropertyTransportType, UInt32.self),
              let alive = try? value(id, kAudioDevicePropertyDeviceIsAlive, UInt32.self), alive != 0 else { return nil }
        return Device(id: id, uid: uid, name: name,
            inputChannels: (try? channelCount(id, kAudioObjectPropertyScopeInput)) ?? 0,
            outputChannels: (try? channelCount(id, kAudioObjectPropertyScopeOutput)) ?? 0, transport: transport)
    }
}
struct CapturePlan {
    let aggregate: Device, microphone: Device, blackhole: Device
    let local: [Int], remote: [Int], orderedUIDs: [String]
    let sampleRate: Double
    var json: [String: Any] { ["device": aggregate.json, "microphone": microphone.json,
        "blackhole": blackhole.json, "subdevice_uids": orderedUIDs, "sample_rate": sampleRate,
        "channel_count": 3, "channels": ["local": local, "remote": remote]] }
}
func capturePlan(_ inventory: [Device]) throws -> CapturePlan {
    let matches = inventory.filter { $0.name == "Nilaris Audio Input" }
    guard matches.count == 1 else { throw AudioFailure("Expected one Nilaris Audio Input; found \(matches.count)") }
    let aggregate = matches[0]
    guard aggregate.transport == kAudioDeviceTransportTypeAggregate,
          let order = try cfValue(aggregate.id, kAudioAggregateDevicePropertyFullSubDeviceList) as? [String]
        else { throw AudioFailure("Cannot verify aggregate order") }
    let active = try objectIDs(aggregate.id, kAudioAggregateDevicePropertyActiveSubDeviceList)
    let activeUIDs = try Set(active.map { try stringValue($0, kAudioDevicePropertyDeviceUID) })
    var local: [Int] = [], remote: [Int] = [], offset = 0
    var microphone: Device?, blackhole: Device?
    for uid in order where activeUIDs.contains(uid) {
        guard let d = inventory.first(where: { $0.uid == uid }) else { throw AudioFailure("Unresolved subdevice \(uid)") }
        let channels = Array(offset..<(offset + d.inputChannels))
        if d.name == "BlackHole 2ch" {
            guard blackhole == nil, d.inputChannels == 2 else { throw AudioFailure("Invalid BlackHole input") }
            blackhole = d; remote = channels
        } else if d.inputChannels > 0 {
            guard microphone == nil, d.transport == kAudioDeviceTransportTypeBuiltIn, d.inputChannels == 1
                else { throw AudioFailure("Expected BlackHole 2ch and one built-in microphone") }
            microphone = d; local = channels
        }
        offset += d.inputChannels
    }
    guard let mic = microphone, let bh = blackhole, offset == 3, aggregate.inputChannels == 3,
          Set(local).isDisjoint(with: Set(remote)) else { throw AudioFailure("Ambiguous input configuration") }
    let rate = try value(aggregate.id, kAudioDevicePropertyNominalSampleRate, Float64.self)
    guard rate.isFinite, rate > 0 else { throw AudioFailure("Invalid sample rate") }
    return CapturePlan(aggregate: aggregate, microphone: mic, blackhole: bh, local: local,
                       remote: remote, orderedUIDs: order, sampleRate: rate)
}
func chooseListeningDevice(_ inventory: [Device], current: AudioObjectID) throws -> Device {
    // Bluetooth call mode can expose a one-channel output. It is still a
    // listening device; excluding it silently redirected the user to speakers.
    let physical = inventory.filter { (1...2).contains($0.outputChannels) &&
        [kAudioDeviceTransportTypeBuiltIn, kAudioDeviceTransportTypeUSB,
         kAudioDeviceTransportTypeBluetooth, kAudioDeviceTransportTypeBluetoothLE].contains($0.transport) }
    let bluetooth = physical.filter {
        [kAudioDeviceTransportTypeBluetooth, kAudioDeviceTransportTypeBluetoothLE].contains($0.transport) }
    if let d = bluetooth.first(where: { $0.id == current }) { return d }
    // Preserve the explicitly selected Bluetooth endpoint, including mono.
    // Without a selected endpoint prefer one unambiguous stereo profile.
    let stereoBluetooth = bluetooth.filter { $0.outputChannels == 2 }
    if stereoBluetooth.count == 1 { return stereoBluetooth[0] }
    if bluetooth.count == 1 { return bluetooth[0] }
    if bluetooth.count > 1 { throw AudioFailure("Multiple Bluetooth outputs; cannot choose safely") }
    if let d = physical.first(where: { $0.id == current }) { return d }
    let usb = physical.filter { $0.transport == kAudioDeviceTransportTypeUSB }
    if usb.count == 1 { return usb[0] }
    if usb.count > 1 { throw AudioFailure("Multiple USB outputs; cannot choose safely") }
    let builtin = physical.filter { $0.transport == kAudioDeviceTransportTypeBuiltIn }
    guard builtin.count == 1 else { throw AudioFailure("Cannot choose listening output") }
    return builtin[0]
}
func listeningDevice(_ inventory: [Device]) throws -> Device {
    let current: AudioObjectID = try value(systemObject, kAudioHardwarePropertyDefaultOutputDevice, AudioObjectID.self)
    return try chooseListeningDevice(inventory, current: current)
}
func setDefaultOutput(_ id: AudioObjectID) throws {
    var a = address(kAudioHardwarePropertyDefaultOutputDevice), target = id
    try checked(AudioObjectSetPropertyData(systemObject, &a, 0, nil,
        UInt32(MemoryLayout<AudioObjectID>.size), &target), "Select output")
    let actual: AudioObjectID = try value(systemObject, kAudioHardwarePropertyDefaultOutputDevice, AudioObjectID.self)
    guard actual == id else { throw AudioFailure("Output selection not applied") }
}
func writeJSON(_ object: [String: Any], to url: URL, exclusive: Bool = false) throws {
    let data = try JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys])
    try data.write(to: url, options: exclusive ? [.withoutOverwriting] : [.atomic])
}
func validateOutputMembers(_ composition: [String: Any], expectedUIDs: Set<String>, clockUID: String) throws {
    guard let members = composition[kAudioAggregateDeviceSubDeviceListKey] as? [[String: Any]],
          members.count == expectedUIDs.count else { throw AudioFailure("Automatic output members are missing") }
    var observed = Set<String>()
    for member in members {
        guard let uid = member[kAudioSubDeviceUIDKey] as? String,
              expectedUIDs.contains(uid), !observed.contains(uid),
              let drift = member[kAudioSubDeviceDriftCompensationKey] as? NSNumber,
              drift.intValue == (uid == clockUID ? 0 : 1)
            else { throw AudioFailure("Automatic output member/drift configuration was not applied") }
        observed.insert(uid)
    }
    guard observed == expectedUIDs else { throw AudioFailure("Automatic output membership mismatch") }
}
final class OutputRoute {
    let journal: URL, previousUID: String, listener: Device
    let deviceUID = "org.nilaris.output.\(UUID().uuidString)"
    var id: AudioObjectID = 0
    var closed = false
    init(_ plan: CapturePlan, _ listener: Device, journal: URL) throws {
        self.journal = journal; self.listener = listener
        let previous: AudioObjectID = try value(systemObject, kAudioHardwarePropertyDefaultOutputDevice, AudioObjectID.self)
        previousUID = try stringValue(previous, kAudioDevicePropertyDeviceUID)
        let clockUID = listener.transport == kAudioDeviceTransportTypeBuiltIn ? listener.uid : plan.blackhole.uid
        // Match the working Multi-Output: stacked=true; public so apps follow the default.
        let composition: [String: Any] = [
            kAudioAggregateDeviceUIDKey: deviceUID, kAudioAggregateDeviceNameKey: "Nilaris Automatic Output",
            kAudioAggregateDeviceIsPrivateKey: 0, kAudioAggregateDeviceIsStackedKey: mirroredOutputMode,
            kAudioAggregateDeviceMainSubDeviceKey: clockUID,
            kAudioAggregateDeviceSubDeviceListKey: [
                [kAudioSubDeviceUIDKey: plan.blackhole.uid, kAudioSubDeviceDriftCompensationKey: plan.blackhole.uid == clockUID ? 0 : 1],
                [kAudioSubDeviceUIDKey: listener.uid, kAudioSubDeviceDriftCompensationKey: listener.uid == clockUID ? 0 : 1]]]
        try writeJSON(["schema_version": 1, "managed_output_uid": deviceUID, "previous_output_uid": previousUID,
                       "owner_pid": ProcessInfo.processInfo.processIdentifier], to: journal, exclusive: true)
        do {
            try checked(AudioHardwareCreateAggregateDevice(composition as CFDictionary, &id), "Create automatic output")
            guard id != 0 else { throw AudioFailure("Output has no device ID") }
            let deadline = Date().addingTimeInterval(3)
            var ready = false
            var observedChannels = -1
            var observedUIDs = Set<String>()
            while Date() < deadline {
                observedChannels = (try? channelCount(id, kAudioObjectPropertyScopeOutput)) ?? -1
                let active = (try? objectIDs(id, kAudioAggregateDevicePropertyActiveSubDeviceList)) ?? []
                observedUIDs = Set(active.compactMap { try? stringValue($0, kAudioDevicePropertyDeviceUID) })
                ready = observedChannels == 2 && observedUIDs == Set([plan.blackhole.uid, listener.uid])
                if ready { break }
                Thread.sleep(forTimeInterval: 0.05)
            }
            guard ready else { throw AudioFailure("Automatic output did not become ready: channels=\(observedChannels), subdevices=\(observedUIDs.sorted())") }
            let actualClock = try stringValue(id, kAudioAggregateDevicePropertyMainSubDevice)
            guard actualClock == clockUID,
                  let actualComposition = try cfValue(id, kAudioAggregateDevicePropertyComposition) as? [String: Any],
                  (actualComposition[kAudioAggregateDeviceIsStackedKey] as? NSNumber)?.intValue == mirroredOutputMode
                else { throw AudioFailure("Automatic output mirroring/clock was not applied") }
            // ActiveSubDeviceList returns physical device IDs on this Mac. Drift is
            // aggregate-member state, not a property of the underlying physical device.
            try validateOutputMembers(actualComposition, expectedUIDs: Set([plan.blackhole.uid, listener.uid]),
                                      clockUID: clockUID)
            try setDefaultOutput(id)
        } catch { try? restore(); throw error }
    }
    func restore() throws {
        if closed { return }
        let inventory = try devices()
        let current: AudioObjectID = try value(systemObject, kAudioHardwarePropertyDefaultOutputDevice, AudioObjectID.self)
        if id != 0 && current == id {
            let previous = inventory.first(where: { $0.uid == previousUID })
            let fallback = inventory.filter { $0.transport == kAudioDeviceTransportTypeBuiltIn && $0.outputChannels == 2 }
            guard let destination = previous ?? (fallback.count == 1 ? fallback[0] : nil)
                else { throw AudioFailure("Previous output disconnected; recovery journal retained") }
            if previous == nil { print("NILARIS_ROUTE_WARNING: Previous output disconnected; restored built-in speakers") }
            try setDefaultOutput(destination.id)
        }
        // Respect a different output chosen by the user during recording.
        if id != 0 { try checked(AudioHardwareDestroyAggregateDevice(id), "Remove automatic output") }
        try FileManager.default.removeItem(at: journal); closed = true
    }
}
func recoverRoute(_ journal: URL) throws {
    guard FileManager.default.fileExists(atPath: journal.path) else { return }
    guard let j = try JSONSerialization.jsonObject(with: Data(contentsOf: journal)) as? [String: Any],
          let uid = j["managed_output_uid"] as? String, uid.hasPrefix("org.nilaris.output."),
          let previousUID = j["previous_output_uid"] as? String else { throw AudioFailure("Invalid recovery journal") }
    if let owner = j["owner_pid"] as? Int32, owner != getpid(), kill(owner, 0) == 0 {
        throw AudioFailure("Another recorder owns the output route")
    }
    let inventory = try devices()
    let current: AudioObjectID = try value(systemObject, kAudioHardwarePropertyDefaultOutputDevice, AudioObjectID.self)
    if let managed = inventory.first(where: { $0.uid == uid }) {
        if current == managed.id {
            let previous = inventory.first(where: { $0.uid == previousUID })
            let fallback = inventory.filter { $0.transport == kAudioDeviceTransportTypeBuiltIn && $0.outputChannels == 2 }
            guard let destination = previous ?? (fallback.count == 1 ? fallback[0] : nil)
                else { throw AudioFailure("Previous output unavailable; recovery deferred") }
            try setDefaultOutput(destination.id)
        }
        try checked(AudioHardwareDestroyAggregateDevice(managed.id), "Recover automatic output")
    }
    try FileManager.default.removeItem(at: journal)
}
final class CaptureState {
    let lock = NSLock()
    var frames: Int64 = 0
    var lastBuffer = Date(), error: String?, stopped = false, ready = false
    var localActiveBuffers = 0, duplicatedBuffers = 0
    var configurationGeneration = 0
    var lastConfigurationChange = Date()
    func fail(_ message: String) { lock.lock(); if error == nil { error = message }; lock.unlock() }
    func configurationChanged() {
        lock.lock()
        if ready {
            if error == nil { error = "Audio engine configuration changed; partial audio preserved" }
        } else {
            configurationGeneration += 1
            lastConfigurationChange = Date()
        }
        lock.unlock()
    }
    func stop() { lock.lock(); stopped = true; lock.unlock() }
}
func captureClientDescription(sampleRate: Double, channels: UInt32) -> AudioStreamBasicDescription {
    // Noninterleaved Float32 has four bytes per frame in each channel buffer.
    // Avoid AVAudioFormat's implicit channel-layout constructor for >2 channels.
    AudioStreamBasicDescription(mSampleRate: sampleRate, mFormatID: kAudioFormatLinearPCM,
        mFormatFlags: kAudioFormatFlagsNativeFloatPacked | kAudioFormatFlagIsNonInterleaved,
        mBytesPerPacket: 4, mFramesPerPacket: 1, mBytesPerFrame: 4,
        mChannelsPerFrame: channels, mBitsPerChannel: 32, mReserved: 0)
}
func verifyInputUnit(_ unit: AudioUnit, _ selected: AudioDeviceID) throws {
    var bound: AudioDeviceID = 0, size = UInt32(MemoryLayout<AudioDeviceID>.size)
    try checked(AudioUnitGetProperty(unit, kAudioOutputUnitProperty_CurrentDevice,
        kAudioUnitScope_Global, 0, &bound, &size), "Verify active recorder input")
    guard bound == selected else { throw AudioFailure("Recorder input changed") }
    var map = [Int32](repeating: -1, count: 3), mapSize = UInt32(3 * MemoryLayout<Int32>.size)
    let status = map.withUnsafeMutableBytes {
        AudioUnitGetProperty(unit, kAudioOutputUnitProperty_ChannelMap, kAudioUnitScope_Output,
                             1, $0.baseAddress!, &mapSize)
    }
    try checked(status, "Verify active channel map")
    guard map == [0, 1, 2], mapSize == 12 else { throw AudioFailure("AudioUnit channel map changed") }
}
func record() throws {
    let fm = FileManager.default, root = URL(fileURLWithPath: FileManager.default.currentDirectoryPath, isDirectory: true)
    let journal = root.appendingPathComponent("audio_route_recovery.json")
    try recoverRoute(journal)
    let inventory = try devices(), plan = try capturePlan(inventory), listener = try listeningDevice(inventory)
    let route = try OutputRoute(plan, listener, journal: journal)
    defer {
        do { try route.restore(); print("NILARIS_ROUTE_RESTORED") }
        catch { print("NILARIS_ROUTE_WARNING: \(error)") }; fflush(stdout)
    }
    let engine = AVAudioEngine(), input = engine.inputNode
    guard let unit = input.audioUnit else { throw AudioFailure("Input AudioUnit unavailable") }
    var selected = plan.aggregate.id
    try checked(AudioUnitSetProperty(unit, kAudioOutputUnitProperty_CurrentDevice, kAudioUnitScope_Global, 0,
        &selected, UInt32(MemoryLayout<AudioDeviceID>.size)), "Bind recorder input")
    var bound: AudioDeviceID = 0, deviceSize = UInt32(MemoryLayout<AudioDeviceID>.size)
    try checked(AudioUnitGetProperty(unit, kAudioOutputUnitProperty_CurrentDevice, kAudioUnitScope_Global, 0,
        &bound, &deviceSize), "Verify recorder input")
    guard bound == selected else { throw AudioFailure("Unexpected recorder device") }
    // AVAudioEngine may retain the former default-device client format after a
    // Bluetooth route change. Configure AUHAL's capture-side client format from
    // the selected device, without changing the device's hardware sample rate.
    var hardware = AudioStreamBasicDescription()
    var hardwareSize = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
    try checked(AudioUnitGetProperty(unit, kAudioUnitProperty_StreamFormat,
        kAudioUnitScope_Input, 1, &hardware, &hardwareSize), "Read selected input hardware format")
    let before = input.outputFormat(forBus: 0)
    print("NILARIS_INPUT_FORMAT: hardware=\(hardware.mSampleRate)Hz/\(hardware.mChannelsPerFrame)ch, client=\(before.sampleRate)Hz/\(before.channelCount)ch")
    fflush(stdout)
    guard hardware.mChannelsPerFrame == 3, abs(hardware.mSampleRate - plan.sampleRate) < 1
        else { throw AudioFailure("Selected input hardware disagrees with aggregate metadata: hardware=\(hardware.mSampleRate)Hz/\(hardware.mChannelsPerFrame)ch, aggregate=\(plan.sampleRate)Hz/3ch") }
    var client = captureClientDescription(sampleRate: hardware.mSampleRate, channels: 3)
    try checked(AudioUnitSetProperty(unit, kAudioUnitProperty_StreamFormat,
        kAudioUnitScope_Output, 1, &client,
        UInt32(MemoryLayout<AudioStreamBasicDescription>.size)), "Configure selected input client format")
    let format = input.outputFormat(forBus: 0)
    guard format.channelCount == 3, format.commonFormat == .pcmFormatFloat32, !format.isInterleaved,
          abs(format.sampleRate - plan.sampleRate) < 1 else {
        throw AudioFailure("Input client format was not applied: observed=\(format.sampleRate)Hz/\(format.channelCount)ch, expected=\(plan.sampleRate)Hz/3ch")
    }
    let identity: [Int32] = [0, 1, 2], mapBytes = UInt32(3 * MemoryLayout<Int32>.size)
    let status = identity.withUnsafeBytes {
        AudioUnitSetProperty(unit, kAudioOutputUnitProperty_ChannelMap, kAudioUnitScope_Output, 1, $0.baseAddress!, mapBytes)
    }
    try checked(status, "Set explicit channel map")
    var actualMap = [Int32](repeating: -1, count: 3), mapSize = mapBytes
    let readStatus = actualMap.withUnsafeMutableBytes {
        AudioUnitGetProperty(unit, kAudioOutputUnitProperty_ChannelMap, kAudioUnitScope_Output, 1, $0.baseAddress!, &mapSize)
    }
    try checked(readStatus, "Verify channel map")
    guard actualMap == identity, mapSize == mapBytes else { throw AudioFailure("Channel map verification failed") }
    let stamp = ISO8601DateFormatter().string(from: Date()).replacingOccurrences(of: ":", with: "-")
    let output = root.appendingPathComponent("recordings/nilaris_\(stamp).caf")
    let metadataURL = output.deletingPathExtension().appendingPathExtension("capture.json")
    guard !fm.fileExists(atPath: output.path), !fm.fileExists(atPath: metadataURL.path)
        else { throw AudioFailure("Recording name collision; files preserved") }
    var metadata = plan.json
    metadata.merge(["schema_version": 1, "recording_file": output.lastPathComponent,
        "started_at": ISO8601DateFormatter().string(from: Date()), "status": "starting",
        "input_unit_channel_map": identity, "output": ["listener": listener.json,
        "managed_uid": route.deviceUID, "previous_uid": route.previousUID]]) { _, new in new }
    try writeJSON(metadata, to: metadataURL, exclusive: true)
    var file: AVAudioFile? = try AVAudioFile(forWriting: output, settings: format.settings)
    let state = CaptureState()
    var tapInstalled = true
    func finishAudio() {
        if tapInstalled { input.removeTap(onBus: 0); tapInstalled = false }
        engine.stop(); file = nil
    }
    input.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
        guard buffer.frameLength > 0 else { return }
        guard buffer.format.channelCount == 3, abs(buffer.format.sampleRate - format.sampleRate) < 1,
              let channels = buffer.floatChannelData else { state.fail("Capture format changed"); return }
        do {
            try file?.write(from: buffer)
            let count = Int(buffer.frameLength), local = channels[plan.local[0]]
            var energy: Float = 0, matches = [Bool](repeating: true, count: plan.remote.count)
            for frame in 0..<count {
                let v = local[frame]
                guard v.isFinite else { state.fail("Non-finite audio sample"); return }
                energy += v * v
                for (index, channel) in plan.remote.enumerated() {
                    let r = channels[channel][frame]
                    guard r.isFinite else { state.fail("Non-finite audio sample"); return }
                    if v.bitPattern != r.bitPattern { matches[index] = false }
                }
            }
            state.lock.lock(); state.frames += Int64(count); state.lastBuffer = Date()
            if energy / Float(count) > 0.000001 {
                state.localActiveBuffers += 1; if matches.contains(true) { state.duplicatedBuffers += 1 }
            }
            state.lock.unlock()
        } catch { state.fail("Audio write failure: \(error)") }
    }
    defer { finishAudio() }
    signal(SIGTERM, SIG_IGN); signal(SIGINT, SIG_IGN)
    let term = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .global())
    let interrupt = DispatchSource.makeSignalSource(signal: SIGINT, queue: .global())
    term.setEventHandler { state.stop() }; interrupt.setEventHandler { state.stop() }; term.resume(); interrupt.resume()
    defer { term.cancel(); interrupt.cancel() }
    DispatchQueue.global().async {
        while let command = readLine() {
            if command.trimmingCharacters(in: .whitespacesAndNewlines).uppercased() == "STOP" { state.stop(); return }
        }
        state.stop() // Parent exited: preserve audio and restore output.
    }
    let observer = NotificationCenter.default.addObserver(forName: .AVAudioEngineConfigurationChange,
        object: engine, queue: nil) { _ in state.configurationChanged() }
    defer { NotificationCenter.default.removeObserver(observer) }
    do {
        try engine.start()
        try verifyInputUnit(unit, selected)
        let began = Date()
        var stableSince = began
        var handledGeneration = 0
        var startupRestarts = 0
        while true {
            state.lock.lock()
            let frames = state.frames, error = state.error, stopped = state.stopped,
                last = state.lastBuffer, ready = state.ready,
                generation = state.configurationGeneration, changedAt = state.lastConfigurationChange
            state.lock.unlock()
            if let error { throw AudioFailure(error) }
            if stopped { break }
            if !ready && generation != handledGeneration {
                handledGeneration = generation
                stableSince = changedAt
                let current = try capturePlan(devices())
                guard current.aggregate.uid == plan.aggregate.uid, current.orderedUIDs == plan.orderedUIDs,
                      current.local == plan.local, current.remote == plan.remote,
                      abs(current.sampleRate - plan.sampleRate) < 1
                    else { throw AudioFailure("Input changed during startup") }
                let currentOutput: AudioDeviceID = try value(systemObject, kAudioHardwarePropertyDefaultOutputDevice, AudioDeviceID.self)
                guard currentOutput == route.id,
                      try value(listener.id, kAudioDevicePropertyDeviceIsAlive, UInt32.self) != 0
                    else { throw AudioFailure("Output changed during startup") }
                let observed = input.outputFormat(forBus: 0)
                if !engine.isRunning || observed.channelCount != format.channelCount ||
                    abs(observed.sampleRate - format.sampleRate) >= 1 {
                    guard startupRestarts < 2 else { throw AudioFailure("Audio engine could not stabilize during startup") }
                    startupRestarts += 1
                    engine.stop()
                    try checked(AudioUnitSetProperty(unit, kAudioOutputUnitProperty_CurrentDevice,
                        kAudioUnitScope_Global, 0, &selected, UInt32(MemoryLayout<AudioDeviceID>.size)),
                        "Rebind input during startup")
                    var client = captureClientDescription(sampleRate: plan.sampleRate, channels: 3)
                    try checked(AudioUnitSetProperty(unit, kAudioUnitProperty_StreamFormat,
                        kAudioUnitScope_Output, 1, &client, UInt32(MemoryLayout<AudioStreamBasicDescription>.size)),
                        "Restore input client format during startup")
                    let mappingStatus = identity.withUnsafeBytes {
                        AudioUnitSetProperty(unit, kAudioOutputUnitProperty_ChannelMap,
                            kAudioUnitScope_Output, 1, $0.baseAddress!, mapBytes)
                    }
                    try checked(mappingStatus, "Restore input channel map during startup")
                    try engine.start()
                    stableSince = Date()
                    print("NILARIS_STARTUP_RESTART: \(startupRestarts)"); fflush(stdout)
                }
                try verifyInputUnit(unit, selected)
            }
            // Wait for a stable startup interval; queued notifications from our own
            // initial format setup must not be mistaken for mid-recording hotplug.
            if !ready && frames > 0 && Date().timeIntervalSince(stableSince) >= 1 &&
                Date().timeIntervalSince(last) < 1 && engine.isRunning {
                try verifyInputUnit(unit, selected)
                state.lock.lock()
                let unchanged = state.configurationGeneration == generation && state.error == nil
                if unchanged { state.ready = true }
                state.lock.unlock()
                if unchanged {
                    metadata["status"] = "recording"
                    metadata["startup_preroll_frames"] = frames
                    metadata["startup_configuration_changes"] = generation
                    metadata["startup_restarts"] = startupRestarts
                    try writeJSON(metadata, to: metadataURL)
                    print("NILARIS_OUTPUT: \(listener.name)")
                    print("NILARIS_READY"); fflush(stdout)
                }
            }
            if !ready && Date().timeIntervalSince(began) > 10 { throw AudioFailure("No stable audio buffers received during startup") }
            if ready && Date().timeIntervalSince(last) > 5 { throw AudioFailure("Audio buffers stopped arriving") }
            if Date().timeIntervalSince(began) > 9 * 3600 { print("NILARIS_AUTO_STOP: Hard limit reached"); break }
            if ready {
                let current = try capturePlan(devices())
                guard current.aggregate.uid == plan.aggregate.uid, current.orderedUIDs == plan.orderedUIDs,
                      current.local == plan.local, current.remote == plan.remote,
                      abs(current.sampleRate - plan.sampleRate) < 1 else { throw AudioFailure("Input configuration changed") }
                guard try value(listener.id, kAudioDevicePropertyDeviceIsAlive, UInt32.self) != 0
                    else { throw AudioFailure("Listening device disconnected") }
                try verifyInputUnit(unit, selected)
                let currentOutput: AudioDeviceID = try value(systemObject, kAudioHardwarePropertyDefaultOutputDevice, AudioDeviceID.self)
                guard currentOutput == route.id else { throw AudioFailure("Output changed; partial audio preserved") }
            }
            Thread.sleep(forTimeInterval: 0.5)
        }
        finishAudio()
        state.lock.lock()
        let frames = state.frames, active = state.localActiveBuffers, duplicated = state.duplicatedBuffers,
            callbackError = state.error
        state.lock.unlock()
        if let callbackError { throw AudioFailure(callbackError) }
        guard frames > 0 else { throw AudioFailure("No audio frames recorded") }
        guard active < 10 || Double(duplicated) / Double(active) < 0.95
            else { throw AudioFailure("Identical LOCAL/REMOTE samples; check mapping") }
        metadata["captured_frames"] = frames; metadata["duration_seconds"] = Double(frames) / format.sampleRate
        metadata["status"] = "completed"; metadata["finished_at"] = ISO8601DateFormatter().string(from: Date())
        metadata["duplication_check"] = ["local_active_buffers": active, "identical_buffers": duplicated]
        try writeJSON(metadata, to: metadataURL)
        print("NILARIS_SAVED: \(output.path)"); fflush(stdout)
    } catch {
        finishAudio()
        state.lock.lock(); let frames = state.frames; state.lock.unlock()
        metadata["status"] = "failed"; metadata["captured_frames"] = frames; metadata["error"] = String(describing: error)
        try? writeJSON(metadata, to: metadataURL); throw error
    }
}
func outputSnapshot(_ device: Device) -> [String: Any] {
    var result = device.json
    result["output_channels"] = (try? channelCount(device.id, kAudioObjectPropertyScopeOutput)) ?? -1
    result["sample_rate"] = (try? value(device.id, kAudioDevicePropertyNominalSampleRate, Float64.self)) ?? -1
    result["full_subdevice_uids"] = (try? cfValue(device.id, kAudioAggregateDevicePropertyFullSubDeviceList)) as? [String] ?? []
    let active = (try? objectIDs(device.id, kAudioAggregateDevicePropertyActiveSubDeviceList)) ?? []
    result["active_subdevice_uids"] = active.map { id in
        (try? stringValue(id, kAudioDevicePropertyDeviceUID)) ?? "unreadable-\(id)"
    }
    if let composition = try? cfValue(device.id, kAudioAggregateDevicePropertyComposition),
       let dictionary = composition as? [String: Any] {
        if JSONSerialization.isValidJSONObject(dictionary) { result["composition"] = dictionary }
        else { result["composition_description"] = String(describing: dictionary) }
    }
    result["main_subdevice_uid"] = (try? stringValue(device.id, kAudioAggregateDevicePropertyMainSubDevice)) ?? "unreadable"
    return result
}
func configureDefaultInput() throws {
    let inventory = try devices(), plan = try capturePlan(inventory)
    let current: AudioDeviceID = try value(systemObject, kAudioHardwarePropertyDefaultInputDevice, AudioDeviceID.self)
    // Remove the old workaround only. Preserve any other microphone choice.
    if current != plan.aggregate.id {
        print("NILARIS_DEFAULT_INPUT_UNCHANGED"); return
    }
    let journal = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        .appendingPathComponent("audio_input_migration.json")
    try writeJSON(["schema_version": 1, "previous_uid": plan.aggregate.uid,
                   "selected_uid": plan.microphone.uid], to: journal, exclusive: true)
    var a = address(kAudioHardwarePropertyDefaultInputDevice), mic = plan.microphone.id
    do {
        try checked(AudioObjectSetPropertyData(systemObject, &a, 0, nil,
            UInt32(MemoryLayout<AudioDeviceID>.size), &mic), "Set real default microphone")
        let actual: AudioDeviceID = try value(systemObject, kAudioHardwarePropertyDefaultInputDevice, AudioDeviceID.self)
        guard actual == mic else { throw AudioFailure("Default microphone selection not applied") }
        print("NILARIS_DEFAULT_INPUT: \(plan.microphone.name)")
    } catch {
        // Keep the journal: a setter could have succeeded before a subsequent read failed.
        throw error
    }
}
func restoreDefaultInput() throws {
    let journal = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        .appendingPathComponent("audio_input_migration.json")
    guard FileManager.default.fileExists(atPath: journal.path) else { return }
    guard let j = try JSONSerialization.jsonObject(with: Data(contentsOf: journal)) as? [String: Any],
          let previous = j["previous_uid"] as? String, let selected = j["selected_uid"] as? String
        else { throw AudioFailure("Invalid microphone migration journal") }
    let inventory = try devices()
    let current: AudioDeviceID = try value(systemObject, kAudioHardwarePropertyDefaultInputDevice, AudioDeviceID.self)
    if try stringValue(current, kAudioDevicePropertyDeviceUID) == selected {
        guard let device = inventory.first(where: { $0.uid == previous })
            else { throw AudioFailure("Previous input is unavailable") }
        var a = address(kAudioHardwarePropertyDefaultInputDevice), input = device.id
        try checked(AudioObjectSetPropertyData(systemObject, &a, 0, nil,
            UInt32(MemoryLayout<AudioDeviceID>.size), &input), "Restore previous microphone")
    }
    try FileManager.default.removeItem(at: journal)
    print("NILARIS_DEFAULT_INPUT_RESTORED")
}
func selfTest() throws {
    func fixture(_ id: AudioObjectID, _ transport: UInt32, _ channels: Int = 2) -> Device {
        Device(id: id, uid: "fixture-\(id)", name: "Fixture \(id)", inputChannels: 0,
               outputChannels: channels, transport: transport)
    }
    let speaker = fixture(1, kAudioDeviceTransportTypeBuiltIn)
    let headset = fixture(2, kAudioDeviceTransportTypeBluetooth)
    let handsFree = fixture(3, kAudioDeviceTransportTypeBluetooth, 1)
    let usb = fixture(4, kAudioDeviceTransportTypeUSB)
    let otherHeadset = fixture(5, kAudioDeviceTransportTypeBluetooth)
    func expect(_ condition: Bool) throws {
        if !condition { throw AudioFailure("Output policy self-test failed") }
    }
    let startupState = CaptureState()
    startupState.configurationChanged()
    try expect(startupState.configurationGeneration == 1 && startupState.error == nil)
    startupState.ready = true
    startupState.configurationChanged()
    try expect(startupState.error != nil && startupState.configurationGeneration == 1)
    let clientFixture = captureClientDescription(sampleRate: 48000, channels: 3)
    try expect(clientFixture.mSampleRate == 48000 && clientFixture.mChannelsPerFrame == 3)
    try expect(clientFixture.mBytesPerFrame == 4 && clientFixture.mBitsPerChannel == 32 &&
               clientFixture.mFormatFlags & kAudioFormatFlagIsNonInterleaved != 0)
    // The user-provided CoreAudio composition of the working Multi-Output has stacked=true.
    let workingComposition: [String: Any] = [kAudioAggregateDeviceIsStackedKey: true]
    try expect((workingComposition[kAudioAggregateDeviceIsStackedKey] as? NSNumber)?.intValue == mirroredOutputMode)
    try expect(mirroredOutputMode != 0)
    // Fixture from the working output reported by this Mac, including member-level drift.
    let members: [String: Any] = [kAudioAggregateDeviceSubDeviceListKey: [
        [kAudioSubDeviceUIDKey: "BlackHole2ch_UID", kAudioSubDeviceDriftCompensationKey: 1],
        [kAudioSubDeviceUIDKey: "BuiltInSpeakerDevice", kAudioSubDeviceDriftCompensationKey: 0]]]
    let expected = Set(["BlackHole2ch_UID", "BuiltInSpeakerDevice"])
    try validateOutputMembers(members, expectedUIDs: expected, clockUID: "BuiltInSpeakerDevice")
    for invalid: [String: Any] in [[:], [kAudioAggregateDeviceSubDeviceListKey: [
        [kAudioSubDeviceUIDKey: "BlackHole2ch_UID", kAudioSubDeviceDriftCompensationKey: 0],
        [kAudioSubDeviceUIDKey: "BuiltInSpeakerDevice", kAudioSubDeviceDriftCompensationKey: 0]]]] {
        do {
            try validateOutputMembers(invalid, expectedUIDs: expected, clockUID: "BuiltInSpeakerDevice")
            throw AudioFailure("Invalid member fixture accepted")
        } catch let error as AudioFailure {
            guard error.description.contains("Automatic output") else { throw error }
        }
    }
    try expect(try chooseListeningDevice([speaker], current: 99).id == 1)
    try expect(try chooseListeningDevice([speaker, headset, handsFree], current: 1).id == 2)
    // Regression: the user's WH-1000XM4 reported one output channel and was
    // the system default. It must never be discarded in favour of speakers.
    try expect(try chooseListeningDevice([speaker, handsFree], current: 3).id == 3)
    try expect(try chooseListeningDevice([speaker, headset, handsFree], current: 3).id == 3)
    try expect(try chooseListeningDevice([speaker, handsFree], current: 99).id == 3)
    let bluetoothInput = fixture(6, kAudioDeviceTransportTypeBluetooth, 0)
    try expect(try chooseListeningDevice([speaker, bluetoothInput, handsFree], current: 3).id == 3)
    try expect(try chooseListeningDevice([speaker, bluetoothInput], current: 1).id == 1)
    try expect(try chooseListeningDevice([speaker, usb], current: 4).id == 4)
    try expect(try chooseListeningDevice([speaker, headset, otherHeadset], current: 5).id == 5)
    do {
        _ = try chooseListeningDevice([speaker, headset, otherHeadset], current: 99)
        throw AudioFailure("Ambiguous outputs were not rejected")
    } catch let error as AudioFailure {
        guard error.description.contains("Multiple Bluetooth") else { throw error }
    }
    print("NILARIS_SELF_TEST_PASS: startup/format/policy/composition checks including mono Bluetooth; no settings changed")
}
do {
    if CommandLine.arguments.contains("--self-test") {
        try selfTest()
    } else if CommandLine.arguments.contains("--inspect") {
        let inventory = try devices(), plan = try capturePlan(inventory), listener = try listeningDevice(inventory)
        let inputID: AudioDeviceID = try value(systemObject, kAudioHardwarePropertyDefaultInputDevice, AudioDeviceID.self)
        let outputs = inventory.filter { $0.name == "Nilaris Audio Output" }.map(outputSnapshot)
        let report: [String: Any] = ["capture": plan.json, "listening_device": listener.json,
            "default_input_id": inputID, "existing_outputs": outputs,
            "settings_changed": false, "api_request_sent": false]
        print(String(data: try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]), encoding: .utf8)!)
    } else if CommandLine.arguments.contains("--configure-default-input") {
        try configureDefaultInput()
    } else if CommandLine.arguments.contains("--restore-default-input") {
        try restoreDefaultInput()
    } else if CommandLine.arguments.contains("--recover-route") {
        try recoverRoute(URL(fileURLWithPath: FileManager.default.currentDirectoryPath).appendingPathComponent("audio_route_recovery.json"))
        print("NILARIS_ROUTE_RECOVERY_COMPLETE")
    } else { try record() }
} catch { print("NILARIS_AUDIO_ERROR: \(error)"); fflush(stdout); exit(EXIT_FAILURE) }
