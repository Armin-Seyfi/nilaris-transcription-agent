# Nilaris audio automation candidate

Status: installed and manually checked with built-in speakers and stereo Bluetooth
headphones on one development Mac. Automated checks cover format/mapping policies;
these results do not establish compatibility with every macOS/audio device combination.
The existing Swift AVAudioEngine, BlackHole, Aggregate Input, Flask, FFmpeg and
paid-approval transcription workflow remain in place.

## Behavior

- Bind the recorder's input AudioUnit directly to the unique `Nilaris Audio Input`.
- Resolve the ordered full subdevice UID list, active subdevices and input channel
  counts. Accept only the tested BlackHole 2ch + one built-in microphone layout.
- Set and read back an identity AudioUnit channel map, including after engine start
  and during recording. Save capture metadata alongside the original CAF.
- At Start, prefer a connected stereo Bluetooth output (the user's Sony headset),
  then the current supported physical output, then an unambiguous USB output or
  built-in speakers. Multiple candidates fail safely rather than guessing.
- Create a temporary **public** Multi-Output with BlackHole plus the listening
  device. Use `stacked=true`, matching the working Multi-Output composition observed on this Mac. Validate its clock,
  subdevices and drift correction before changing the system default output.
- Restore the previous output at Stop/error/EOF/SIGTERM, unless the user selected
  another output meanwhile. If the old output disconnected, fall back to built-in
  speakers. Destroy only the temporary output created by this session.
- Keep a recovery journal for a hard crash/SIGKILL. Recover on the next start or
  error acknowledgement, respecting a different output selected by the user.
- Ready means nonempty buffers are arriving after a one-second stable startup
  interval. Queued initial configuration events permit at most two validated
  startup restarts; changes after Ready still fail safely. Preroll frames and
  startup events are recorded in metadata. No stable buffers
  within 10s, a 5s buffer stall, a device/map/format change, or suspicious exact
  LOCAL/REMOTE duplication stops safely and preserves partial files.
- Process new recordings only using their completed, verified capture metadata.
  Preserve and reuse already-prepared historical sessions. Raw historical CAFs
  without a sidecar require separately verified mapping; no loudness-based guess.
- The installation removes the old system-input workaround **only if** the
  default input is currently `Nilaris Audio Input`, selecting the built-in mic.
  This one-time migration is journaled for rollback. Other microphone choices
  are preserved. Normal recording does not change the system default microphone.

## Limits

- Hardware binding, temporary output creation, Bluetooth reconnects, audio quality
  and drift compensation still require live tests on this Mac.
- Some meeting apps use an explicitly selected output/microphone and ignore the
  system default. Use their real mic and system-default playback settings once;
  Nilaris cannot promise to override every third-party app's internal preferences.
- Device selection occurs at Start. Connecting/disconnecting during capture may
  stop the recording safely; seamless mid-session switching is not implemented.
- Bluetooth transport does not semantically identify every device as a headphone.
  A connected Bluetooth stereo speaker can also be selected by the same policy.
- Acoustic speaker leakage can persist. The observed 30s speaker test produced no
  duplicate transcript, but long Persian meetings and simultaneous speech remain
  untested. No echo cancellation or LLM transcript cleanup is added.
- A digital-zero buffer is valid silence; Ready detects audio frames, not that
  somebody is speaking or that meeting playback is routed correctly.
- Existing AVAudioEngine APIs compile with macOS 27 deprecation warnings; no
  migration to a different capture API is included.

## Operation

Use the macOS setup and launch instructions in README.md. Deployment-specific patch
installers and hash manifests are kept outside the public source release; they were
used to install reversible incremental updates on the development machine.

## Live acceptance tests (no transcription required initially)

1. With no headset, Start selects built-in speakers + BlackHole. Speak alone,
   then play system speech while silent. Stop restores the previous output.
2. With Sony connected, Start selects Sony + BlackHole. Verify you hear playback
   only through the headphones and can adjust volume on the right earcup.
3. Confirm CAF frame count matches `.capture.json` and actual channel roles;
   inspect both extracted paths before any paid transcription.
4. Disconnect the headset during a disposable test: expect an explicit error,
   preserved partial CAF, and sensible output recovery; acknowledge the error.
5. Keep completed baseline sessions unchanged. Validate the new prepare flow,
   TXT/DOCX export and session history separately, without re-transcribing baselines.


### Mono Bluetooth playback endpoints

Some WH-1000XM4 configurations expose a one-channel Bluetooth output. The
recorder accepts one- or two-channel physical playback devices and preserves the
currently selected Bluetooth endpoint, including mono. Input-only Bluetooth
devices remain excluded. When no Bluetooth endpoint is selected, one unambiguous
stereo endpoint is preferred. Existing aggregate membership, drift, channel
mapping and route restoration checks remain in force. The change does not force
a Bluetooth profile or activate the headset microphone.

The regression reproduces a selected mono headset alongside built-in speakers
and checks that the headset is retained. Actual playback and BlackHole signal
need a short on-device recording test; policy fixtures alone do not establish
hardware compatibility. Headphone disconnection is handled by the bounded recovery process below.


### Input capture independent of playback

The capture backend uses an input-only CoreAudio AUHAL instance with input enabled
on element 1, playback disabled on element 0, and CurrentDevice pinned to the
verified Nilaris Audio Input aggregate. Its callback calls AudioUnitRender into
a preallocated, three-channel Float32 buffer and writes the same CAF format as
before. The local microphone remains the built-in microphone. No global input
setting is changed.

This replaces AVAudioEngine capture. On-device logs showed that both rebinding
and recreating AVAudioEngine returned the correct capture device before start,
then reported a different device after engine.start during Bluetooth removal.
Direct input-only AUHAL avoids that shared input/output engine lifecycle.

If the listening device disappears, only the temporary playback route is replaced
with BlackHole plus built-in speakers. The input unit stays running and must
retain its device and channel mapping; capture must advance across the playback
transition before success is reported. Source layout changes, rendering errors
and stalled buffers fail closed and preserve partial audio. Playback replacement
is limited to twelve transitions per recording.

A brief remote-audio interruption is possible while apps follow the new default
output. No missing speech is reconstructed and no artificial silence is inserted:
the input clock keeps recording the microphone and whatever reaches BlackHole.
Capture metadata records the transition interval, listener change, continuity
check, and possible missing remote audio. UI warnings display the transition.
The sidecar identifies this backend as independent_input_only_auhal_v1.

While playback uses built-in speakers, an unambiguous Bluetooth endpoint that
remains visible for at least 0.8 seconds triggers automatic switching to headphones.
Both mono and stereo endpoints are supported. Input-only devices and transient
connections are ignored. An already active Bluetooth or USB listener is not
replaced merely because another headset appears. Ambiguous connections leave
speaker playback alone unless the system default identifies a specific headset.

The transition also tolerates the OS changing its default output while the new
Bluetooth endpoint is becoming available. Other manual output changes are still
unsupported. On stopping after an automatic handoff, the last selected physical
listener is retained rather than restoring the speaker setting from before the
headphones connected. Apps pinned to their own output device may not follow the
new system route and need separate validation.

On-device testing confirmed both directions in one recording: built-in speakers
to WH-1000XM4, then back to built-in speakers. Capture continued across both
transitions, the recording completed, and transcription finalized with LOCAL and
REMOTE segments. This validates the tested Mac/headset pairing, not every device
or application output configuration.

Offline tests exercise the actual input-only configuration helper with an injected
CoreAudio property setter: input is enabled, playback is disabled, the explicit
capture device is selected, and property failures are rejected. Mapping and
playback policy tests remain. Real microphone/system capture and headphone
disconnection need on-device validation; compilation and fixtures do not establish
hardware compatibility.
