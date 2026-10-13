# Conservative timestamp repair

The existing speaker test's first REMOTE segment starts at 0 in Whisper's raw
JSON, while FFmpeg detects near-digital silence until 14.3175 seconds. The
unified transcript builder previously copied that raw start correctly; chunk
offset assembly was not the cause of this observed error.

The new builder scans each cached audio chunk with FFmpeg silencedetect at
-70 dB, minimum one second. If a segment's start lies in a long near-silence
interval and the interval ends before the segment's end, only that start is
moved to 50 ms before the end of silence. Later segments are not shifted.
Ends are clipped to actual chunk/session audio bounds. Text is never rewritten.
A segment entirely in silence is preserved and flagged for review; an alignment
is not invented. Invalid/nonfinite/out-of-audio times fail clearly.

This is NOT a learned VAD, forced word alignment, or proof of the exact spoken
word boundary. Low-volume speech, background noise and model-internal timing
errors still need separate evaluation. All timestamps remain approximate.
Raw chunk JSON remains intact; the final JSON records timing provenance.

Existing completed transcripts are not overwritten or automatically rebuilt.
The existing chunk offsets and whole-chunk silence filtering are unchanged.
No dependency beyond the existing FFmpeg/ffprobe tools is added.
