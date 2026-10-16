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
    var error: String?, stopped = false, ready = false
    var localActiveBuffers = 0, duplicatedBuffers = 0
    var lastBufferUptime = ProcessInfo.processInfo.systemUptime
    func fail(_ message: String) { lock.lock(); if error == nil { error = message }; lock.unlock() }
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
    guard bound == selected else { throw AudioFailure("Recorder input changed: expected device \(selected), observed \(bound)") }
    var map = [Int32](repeating: -1, count: 3), mapSize = UInt32(3 * MemoryLayout<Int32>.size)
    let status = map.withUnsafeMutableBytes {
        AudioUnitGetProperty(unit, kAudioOutputUnitProperty_ChannelMap, kAudioUnitScope_Output,
                             1, $0.baseAddress!, &mapSize)
    }
    try checked(status, "Verify active channel map")
    guard map == [0, 1, 2], mapSize == 12 else { throw AudioFailure("AudioUnit channel map changed") }
}
func verifySameCapture(_ expected: CapturePlan, _ observed: CapturePlan) throws {
    guard observed.aggregate.uid == expected.aggregate.uid,
          observed.orderedUIDs == expected.orderedUIDs,
          observed.local == expected.local, observed.remote == expected.remote,
          abs(observed.sampleRate - expected.sampleRate) < 1 else {
        throw AudioFailure("Input layout changed; partial recording preserved")
    }
}
func disconnectedListenerFallback(_ inventory: [Device]) throws -> Device {
    let speakers = inventory.filter { $0.transport == kAudioDeviceTransportTypeBuiltIn && $0.outputChannels == 2 }
    guard speakers.count == 1 else { throw AudioFailure("Cannot identify built-in speakers for output recovery") }
    return speakers[0]
}
typealias CapturePropertySetter = (AudioUnit, AudioUnitPropertyID, AudioUnitScope,
    AudioUnitElement, UnsafeRawPointer, UInt32) -> OSStatus
func configureIndependentIO(_ unit: AudioUnit, selected: AudioDeviceID,
    setter: CapturePropertySetter = { AudioUnitSetProperty($0, $1, $2, $3, $4, $5) }) throws {
    var on: UInt32 = 1, off: UInt32 = 0, device = selected
    try checked(setter(unit, kAudioOutputUnitProperty_EnableIO, kAudioUnitScope_Input, 1, &on, 4),
                "Enable capture input")
    try checked(setter(unit, kAudioOutputUnitProperty_EnableIO, kAudioUnitScope_Output, 0, &off, 4),
                "Disable capture playback")
    try checked(setter(unit, kAudioOutputUnitProperty_CurrentDevice, kAudioUnitScope_Global, 0, &device, 4),
                "Pin independent capture device")
}
// Capture is deliberately input-only: playback/default-output changes must not
// redirect a shared AVAudioEngine input/output AudioUnit.
let fixedInputCallback: AURenderCallback = { context, flags, timestamp, _, frames, _ in
    return Unmanaged<FixedInputCapture>.fromOpaque(context).takeUnretainedValue()
        .receive(flags: flags, timestamp: timestamp, frames: frames)
}
final class FixedInputCapture {
    let plan: CapturePlan, state: CaptureState, format: AVAudioFormat
    let buffer: AVAudioPCMBuffer
    var unit: AudioUnit?
    var writer: AVAudioFile?
    var started = false
    var initialized = false
    init(plan: CapturePlan, state: CaptureState, output: URL) throws {
        self.plan = plan; self.state = state
        var description = captureClientDescription(sampleRate: plan.sampleRate, channels: 3)
        guard let layout = AVAudioChannelLayout(layoutTag: kAudioChannelLayoutTag_DiscreteInOrder | 3),
              let format = AVAudioFormat(streamDescription: &description, channelLayout: layout),
              let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 16384)
            else { throw AudioFailure("Cannot allocate fixed-input capture buffers") }
        self.format = format; self.buffer = buffer
        var componentDescription = AudioComponentDescription(componentType: kAudioUnitType_Output,
            componentSubType: kAudioUnitSubType_HALOutput, componentManufacturer: kAudioUnitManufacturer_Apple,
            componentFlags: 0, componentFlagsMask: 0)
        guard let component = AudioComponentFindNext(nil, &componentDescription)
            else { throw AudioFailure("CoreAudio input component unavailable") }
        var instance: AudioComponentInstance?
        try checked(AudioComponentInstanceNew(component, &instance), "Create independent capture AudioUnit")
        guard let instance else { throw AudioFailure("Independent capture AudioUnit missing") }
        unit = instance
        do {
            try configureIndependentIO(instance, selected: plan.aggregate.id)
            var hardware = AudioStreamBasicDescription(), size = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
            try checked(AudioUnitGetProperty(instance, kAudioUnitProperty_StreamFormat,
                kAudioUnitScope_Input, 1, &hardware, &size), "Read independent capture hardware")
            guard hardware.mChannelsPerFrame == 3, abs(hardware.mSampleRate - plan.sampleRate) < 1
                else { throw AudioFailure("Independent capture hardware disagrees with mapping") }
            try checked(AudioUnitSetProperty(instance, kAudioUnitProperty_StreamFormat,
                kAudioUnitScope_Output, 1, &description, size), "Set independent capture format")
            let mapping: [Int32] = [0, 1, 2]
            let mapStatus = mapping.withUnsafeBytes {
                AudioUnitSetProperty(instance, kAudioOutputUnitProperty_ChannelMap,
                    kAudioUnitScope_Output, 1, $0.baseAddress!, UInt32($0.count))
            }
            try checked(mapStatus, "Map independent capture channels")
            var callback = AURenderCallbackStruct(inputProc: fixedInputCallback,
                inputProcRefCon: Unmanaged.passUnretained(self).toOpaque())
            try checked(AudioUnitSetProperty(instance, kAudioOutputUnitProperty_SetInputCallback,
                kAudioUnitScope_Global, 0, &callback, UInt32(MemoryLayout<AURenderCallbackStruct>.size)),
                "Install independent capture callback")
            try checked(AudioUnitInitialize(instance), "Initialize independent capture")
            initialized = true
            try verifyInputUnit(instance, plan.aggregate.id)
            writer = try AVAudioFile(forWriting: output, settings: format.settings)
        } catch { close(); throw error }
    }
    func start() throws {
        guard let unit else { throw AudioFailure("Capture unit is closed") }
        try checked(AudioOutputUnitStart(unit), "Start independent capture")
        started = true
        try verifyInputUnit(unit, plan.aggregate.id)
    }
    func receive(flags: UnsafeMutablePointer<AudioUnitRenderActionFlags>,
                 timestamp: UnsafePointer<AudioTimeStamp>, frames: UInt32) -> OSStatus {
        guard frames > 0 else { return noErr }
        state.lock.lock(); let failed = state.error != nil; state.lock.unlock()
        if failed { return noErr }
        guard let unit, frames <= buffer.frameCapacity else {
            state.fail("Independent input callback exceeded buffer capacity"); return noErr
        }
        buffer.frameLength = frames
        let result = AudioUnitRender(unit, flags, timestamp, 1, frames, buffer.mutableAudioBufferList)
        guard result == noErr else {
            state.fail("Independent input render failed: CoreAudio error \(result)"); return noErr
        }
        guard let samples = buffer.floatChannelData else {
            state.fail("Independent input PCM channels unavailable"); return noErr
        }
        var energy: Float = 0, matches = [Bool](repeating: true, count: plan.remote.count)
        for frame in 0..<Int(frames) {
            let local = samples[plan.local[0]][frame]
            guard local.isFinite else { state.fail("Non-finite local capture sample"); return noErr }
            energy += local * local
            for (index, channel) in plan.remote.enumerated() {
                let remote = samples[channel][frame]
                guard remote.isFinite else { state.fail("Non-finite remote capture sample"); return noErr }
                if local.bitPattern != remote.bitPattern { matches[index] = false }
            }
        }
        do {
            guard let writer else { throw AudioFailure("Capture file closed unexpectedly") }
            try writer.write(from: buffer)
            state.lock.lock()
            state.frames += Int64(frames)
            state.lastBufferUptime = ProcessInfo.processInfo.systemUptime
            if energy / Float(frames) > 0.000001 {
                state.localActiveBuffers += 1
                if matches.contains(true) { state.duplicatedBuffers += 1 }
            }
            state.lock.unlock()
        } catch { state.fail("Audio write failure: \(error)") }
        return noErr
    }
    func verify() throws {
        guard let unit else { throw AudioFailure("Capture unit closed unexpectedly") }
        try verifyInputUnit(unit, plan.aggregate.id)
    }
    func close() {
        if let unit {
            if started { AudioOutputUnitStop(unit); started = false }
            if initialized { AudioUnitUninitialize(unit); initialized = false }
            AudioComponentInstanceDispose(unit); self.unit = nil
        }
        writer = nil
    }
    deinit { close() }
}
func record() throws {
    let fm = FileManager.default, root = URL(fileURLWithPath: fm.currentDirectoryPath, isDirectory: true)
    let journal = root.appendingPathComponent("audio_route_recovery.json")
    try recoverRoute(journal)
    let inventory = try devices(), plan = try capturePlan(inventory)
    var listener = try listeningDevice(inventory)
    var route = try OutputRoute(plan, listener, journal: journal)
    defer {
        do { try route.restore(); print("NILARIS_ROUTE_RESTORED") }
        catch { print("NILARIS_ROUTE_WARNING: \(error)") }; fflush(stdout)
    }
    let stamp = ISO8601DateFormatter().string(from: Date()).replacingOccurrences(of: ":", with: "-")
    let output = root.appendingPathComponent("recordings/nilaris_\(stamp).caf")
    let metadataURL = output.deletingPathExtension().appendingPathExtension("capture.json")
    guard !fm.fileExists(atPath: output.path), !fm.fileExists(atPath: metadataURL.path)
        else { throw AudioFailure("Recording name collision; files preserved") }
    var metadata = plan.json
    metadata.merge(["schema_version": 1, "recording_file": output.lastPathComponent,
        "started_at": ISO8601DateFormatter().string(from: Date()), "status": "starting",
        "input_unit_channel_map": [0, 1, 2], "capture_backend": "independent_input_only_auhal_v1",
        "output": ["listener": listener.json, "managed_uid": route.deviceUID,
                   "previous_uid": route.previousUID]]) { _, new in new }
    try writeJSON(metadata, to: metadataURL, exclusive: true)
    let state = CaptureState()
    var capture: FixedInputCapture?
    var events: [[String: Any]] = []
    defer { capture?.close() }
    signal(SIGTERM, SIG_IGN); signal(SIGINT, SIG_IGN)
    let term = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .global())
    let interrupt = DispatchSource.makeSignalSource(signal: SIGINT, queue: .global())
    term.setEventHandler { state.stop() }; interrupt.setEventHandler { state.stop() }
    term.resume(); interrupt.resume()
    defer { term.cancel(); interrupt.cancel() }
    DispatchQueue.global().async {
        while let command = readLine() {
            if command.trimmingCharacters(in: .whitespacesAndNewlines).uppercased() == "STOP" { state.stop(); return }
        }
        state.stop()
    }
    do {
        capture = try FixedInputCapture(plan: plan, state: state, output: output)
        try capture!.start()
        let began = ProcessInfo.processInfo.systemUptime
        while true {
            state.lock.lock()
            let frames = state.frames, error = state.error, stopped = state.stopped,
                last = state.lastBufferUptime, ready = state.ready
            state.lock.unlock()
            if let error { throw AudioFailure(error) }
            if stopped { break }
            let available = try devices()
            try verifySameCapture(plan, capturePlan(available))
            try capture!.verify()
            if !ready && frames > 0 && ProcessInfo.processInfo.systemUptime - began >= 1 &&
                ProcessInfo.processInfo.systemUptime - last < 1 {
                state.lock.lock(); state.ready = true; state.lock.unlock()
                metadata["status"] = "recording"; metadata["startup_preroll_frames"] = frames
                try writeJSON(metadata, to: metadataURL)
                print("NILARIS_OUTPUT: \(listener.name)"); print("NILARIS_READY"); fflush(stdout)
            }
            if !ready && ProcessInfo.processInfo.systemUptime - began > 10 {
                throw AudioFailure("Independent capture did not receive startup audio")
            }
            if ready && ProcessInfo.processInfo.systemUptime - last > 5 {
                throw AudioFailure("Independent capture buffers stopped arriving; partial audio preserved")
            }
            if ProcessInfo.processInfo.systemUptime - began > 9 * 3600 {
                print("NILARIS_AUTO_STOP: Hard limit reached"); break
            }
            if !available.contains(where: { $0.uid == listener.uid && $0.outputChannels > 0 }) {
                guard events.count < 6 else { throw AudioFailure("Repeated playback changes; partial recording preserved") }
                state.lock.lock(); let fromFrame = state.frames; state.lock.unlock()
                var event: [String: Any] = ["status": "recovering", "stage": "replace_playback_route",
                    "reason": "listening_device_disconnected", "from_listener": listener.json,
                    "audio_offset_seconds": Double(fromFrame) / plan.sampleRate,
                    "started_at": ISO8601DateFormatter().string(from: Date()), "silence_inserted": false]
                events.append(event); metadata["output_recoveries"] = events
                try writeJSON(metadata, to: metadataURL)
                print("NILARIS_ROUTE_WARNING: Headphones disconnected; switching playback to speakers")
                fflush(stdout)
                // The input-only unit stays on the capture aggregate while playback
                // changes. Any silence at BlackHole is captured on the original clock.
                let destination = try disconnectedListenerFallback(devices())
                try route.restore()
                route = try OutputRoute(plan, destination, journal: journal)
                listener = destination
                try verifySameCapture(plan, capturePlan(devices()))
                try capture!.verify()
                let continuityDeadline = ProcessInfo.processInfo.systemUptime + 1
                var throughFrame: Int64 = fromFrame
                repeat {
                    state.lock.lock()
                    throughFrame = state.frames
                    let captureError = state.error
                    state.lock.unlock()
                    if let captureError { throw AudioFailure(captureError) }
                    if throughFrame > fromFrame { break }
                    Thread.sleep(forTimeInterval: 0.02)
                } while ProcessInfo.processInfo.systemUptime < continuityDeadline
                try capture!.verify()
                guard throughFrame > fromFrame else {
                    throw AudioFailure("Capture did not advance during playback recovery")
                }
                event["status"] = "resumed"; event["stage"] = "complete"
                event["to_listener"] = listener.json
                event["capture_continued"] = true
                event["transition_end_seconds"] = Double(throughFrame) / plan.sampleRate
                event["transition_seconds"] = Double(throughFrame - fromFrame) / plan.sampleRate
                event["remote_audio_may_be_missing_during_transition"] = true
                events[events.count - 1] = event; metadata["output_recoveries"] = events
                metadata["output"] = ["listener": listener.json, "managed_uid": route.deviceUID,
                    "previous_uid": route.previousUID]
                try writeJSON(metadata, to: metadataURL)
                print("NILARIS_OUTPUT: \(listener.name)")
                print("NILARIS_ROUTE_WARNING: Capture continued; now listening through \(listener.name). Brief remote-audio interruption is possible.")
                fflush(stdout)
            } else {
                let current: AudioObjectID = try value(systemObject, kAudioHardwarePropertyDefaultOutputDevice, AudioObjectID.self)
                if current != route.id {
                    // Default-output and device-alive notifications need not arrive
                    // together. Keep capturing while waiting briefly for removal.
                    let settleUntil = ProcessInfo.processInfo.systemUptime + 0.8
                    var stillPresent = true
                    repeat {
                        stillPresent = (try devices()).contains { $0.uid == listener.uid && $0.outputChannels > 0 }
                        if !stillPresent { break }
                        Thread.sleep(forTimeInterval: 0.05)
                    } while ProcessInfo.processInfo.systemUptime < settleUntil
                    if !stillPresent { continue }
                    throw AudioFailure("Output manually changed; partial audio preserved")
                }
            }
            Thread.sleep(forTimeInterval: 0.25)
        }
        capture?.close()
        state.lock.lock()
        let frames = state.frames, active = state.localActiveBuffers, duplicated = state.duplicatedBuffers,
            callbackError = state.error
        state.lock.unlock()
        if let callbackError { throw AudioFailure(callbackError) }
        guard frames > 0 else { throw AudioFailure("No audio frames recorded") }
        guard active < 10 || Double(duplicated) / Double(active) < 0.95
            else { throw AudioFailure("Identical LOCAL/REMOTE samples; check mapping") }
        metadata["captured_frames"] = frames; metadata["duration_seconds"] = Double(frames) / plan.sampleRate
        metadata["status"] = "completed"; metadata["finished_at"] = ISO8601DateFormatter().string(from: Date())
        metadata["duplication_check"] = ["local_active_buffers": active, "identical_buffers": duplicated]
        try writeJSON(metadata, to: metadataURL)
        print("NILARIS_SAVED: \(output.path)"); fflush(stdout)
    } catch {
        capture?.close()
        state.lock.lock(); let frames = state.frames; state.lock.unlock()
        if !events.isEmpty && (events.last?["status"] as? String) == "recovering" {
            events[events.count - 1]["status"] = "failed"; metadata["output_recoveries"] = events
        }
        let stage = events.last?["stage"] as? String
        let detail = String(describing: error) + (stage.map { " [recovery stage: \($0)]" } ?? "")
        metadata["status"] = "failed"; metadata["captured_frames"] = frames; metadata["error"] = detail
        try? writeJSON(metadata, to: metadataURL)
        throw AudioFailure(detail)
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
    // Exercise the real I/O configuration helper using an injected property
    // writer: output must be disabled and the explicit aggregate must be pinned.
    var ioSettings: [(AudioUnitPropertyID, AudioUnitScope, AudioUnitElement, UInt32)] = []
    let fakeUnit = AudioUnit(bitPattern: 1)!
    try configureIndependentIO(fakeUnit, selected: 55, setter: { _, property, scope, element, bytes, _ in
        ioSettings.append((property, scope, element, bytes.load(as: UInt32.self)))
        return noErr
    })
    try expect(ioSettings.count == 3)
    try expect(ioSettings[0].0 == kAudioOutputUnitProperty_EnableIO &&
        ioSettings[0].1 == kAudioUnitScope_Input && ioSettings[0].2 == 1 && ioSettings[0].3 == 1)
    try expect(ioSettings[1].0 == kAudioOutputUnitProperty_EnableIO &&
        ioSettings[1].1 == kAudioUnitScope_Output && ioSettings[1].2 == 0 && ioSettings[1].3 == 0)
    try expect(ioSettings[2].0 == kAudioOutputUnitProperty_CurrentDevice && ioSettings[2].3 == 55)
    do {
        try configureIndependentIO(fakeUnit, selected: 55, setter: { _, _, _, _, _, _ in -50 })
        throw AudioFailure("Failed input configuration was accepted")
    } catch let error as AudioFailure {
        guard error.description.contains("Enable capture input") else { throw error }
    }
    // Input-layout changes remain fatal; only the unchanged mapping can resume.
    let captureDevice = fixture(10, kAudioDeviceTransportTypeAggregate)
    let blackholeDevice = fixture(11, kAudioDeviceTransportTypeVirtual)
    let microphoneDevice = fixture(12, kAudioDeviceTransportTypeBuiltIn, 0)
    let stablePlan = CapturePlan(aggregate: captureDevice, microphone: microphoneDevice,
        blackhole: blackholeDevice, local: [2], remote: [0, 1], orderedUIDs: ["blackhole", "mic"], sampleRate: 48000)
    try verifySameCapture(stablePlan, stablePlan)
    let changedPlan = CapturePlan(aggregate: captureDevice, microphone: microphoneDevice,
        blackhole: blackholeDevice, local: [0], remote: [1, 2], orderedUIDs: ["mic", "blackhole"], sampleRate: 48000)
    do {
        try verifySameCapture(stablePlan, changedPlan)
        throw AudioFailure("Changed capture layout was accepted")
    } catch let error as AudioFailure {
        guard error.description.contains("Input layout changed") else { throw error }
    }
    try expect(try disconnectedListenerFallback([speaker, headset]).id == speaker.id)
    do {
        _ = try disconnectedListenerFallback([headset])
        throw AudioFailure("Missing fallback was accepted")
    } catch let error as AudioFailure {
        guard error.description.contains("Cannot identify built-in speakers") else { throw error }
    }
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
    print("NILARIS_SELF_TEST_PASS: input-only AUHAL configuration, channel mapping and playback policy checks; no settings changed")
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
