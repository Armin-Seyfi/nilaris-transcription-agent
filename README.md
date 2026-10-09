# Nilaris Transcription Agent

A Python-based AI meeting transcription tool for macOS.

## Project Goal

Build an AI-powered meeting assistant that captures business conversations in Persian and English, transcribes them, and produces structured outputs for downstream LLM processing.

## Architecture

Google Meet Audio
    -> macOS Audio Capture
    -> 5-Minute Audio Chunks
    -> MP3 Compression
    -> OpenAI Whisper API
    -> Timestamped Transcription
    -> JSON / TXT Export

## Development Status

**Status: Early Development**

### MVP v0.1 - Planned
- Capture meeting audio on macOS
- Support speakers and headphones
- Process audio in 5-minute batches
- Compress audio before API submission
- Transcribe Persian and English conversations
- Export timestamped JSON and readable TXT

### Future Roadmap
- AI-generated meeting summaries
- Meeting minutes and action items
- Speaker diarization
- Overlapping audio chunks
- Google Meet bot integration

## Privacy

Meeting recordings, transcripts, and API credentials are excluded from Git version control.

Record meetings only with appropriate participant consent and in accordance with applicable privacy requirements.

## Technology

Python, OpenAI Whisper API, FFmpeg, BlackHole, Git.

## Author

Armin Seyfi

AI Consulting | Business Strategy & AI Integration
