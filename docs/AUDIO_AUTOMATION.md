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
hardware compatibility. A disconnect during recording still ends capture rather
than silently changing the input layout.
