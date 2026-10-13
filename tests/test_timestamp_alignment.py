import json
import math
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from timestamp_alignment import repair_segment, audio_timing
from transcript_builder import load_session_segments


class TimestampAlignmentTests(unittest.TestCase):
    def test_only_start_inside_long_near_silence_is_repaired(self):
        start,end,p=repair_segment(0,15.4,duration=29.496,silences=[(0,14.3175)])
        self.assertAlmostEqual(start,14.2675)
        self.assertEqual(end,15.4)
        self.assertEqual(p['raw_start'],0)
        self.assertFalse(p['word_alignment_verified'])

    def test_valid_later_segments_are_not_shifted(self):
        start,end,p=repair_segment(15.4,17.84,duration=29.496,silences=[(0,14.3175)])
        self.assertEqual((start,end),(15.4,17.84));self.assertEqual(p['adjustments'],[])

    def test_short_pause_is_preserved(self):
        self.assertEqual(repair_segment(1,3,duration=5,silences=[(1,1.6)])[:2],(1,3))

    def test_segment_wholly_in_silence_is_flagged_not_invented_or_deleted(self):
        start,end,p=repair_segment(0,2,duration=20,silences=[(0,10)])
        self.assertEqual((start,end),(0,2));self.assertTrue(p['review_flags'])

    def test_end_is_clipped_without_moving_other_segments(self):
        self.assertEqual(repair_segment(28.88,29.88,duration=29.496,silences=[])[:2],(28.88,29.496))

    def test_invalid_or_out_of_audio_timestamps_are_rejected(self):
        for start,end in [(float('nan'),1),(0,float('inf')),(-1,2),(3,2),(True,2),(30,31)]:
            with self.assertRaises(ValueError):repair_segment(start,end,duration=29,silences=[])

    def test_chunk_offset_and_text_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for source in ['local','remote']:
                d=root/source;d.mkdir();(d/'chunks.csv').write_text('chunk_001.mp3,300,330\n')
                (d/'chunk_001.mp3').write_bytes(b'fixture')
                (d/'chunk_001.json').write_text(json.dumps({'segments':[{'start':0,'end':15.4,'text':' Exact wording. '}]}))
            with patch('transcript_builder.audio_timing',return_value=(30,[(0,14.3175)])):
                segments=load_session_segments(root)
            self.assertEqual(len(segments),2)
            self.assertTrue(all(s['start']==314.267 and s['end']==315.4 for s in segments))
            self.assertTrue(all(s['text']=='Exact wording.' for s in segments))
            self.assertTrue(all(s['timestamp_provenance']['chunk_offset_seconds']==300 for s in segments))

    def test_ffmpeg_detects_leading_silence_but_not_quiet_speech_above_threshold(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'test.wav';rate=16000
            with wave.open(str(path),'wb') as f:
                f.setnchannels(1);f.setsampwidth(2);f.setframerate(rate)
                f.writeframes(b'\0'*(2*rate*2))
                f.writeframes(b''.join(struct.pack('<h',int(50*math.sin(2*math.pi*440*i/rate))) for i in range(rate)))
            duration,silences=audio_timing(path)
            self.assertEqual(duration,3)
            self.assertEqual(len(silences),1)
            self.assertAlmostEqual(silences[0][0],0)
            self.assertAlmostEqual(silences[0][1],2,places=3)


if __name__=='__main__':unittest.main()
