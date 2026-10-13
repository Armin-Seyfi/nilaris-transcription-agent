# Automatic titles and inline editing

The user explicitly authorized automatic AI title generation without cost confirmation.
For each completed transcript with no existing display title, the app sends one
request using the **gpt-4o-mini alias**, not the inaccessible dated snapshot.
Existing completed untitled transcripts are included. Manual titles, including
titles assigned before transcription, are never automatically replaced.

The pattern remains Content type — Specific main topic | Supported optional context.
Date/time remain separate. OpenAI receives transcript text (distributed excerpts
for long transcripts) with the existing length and output-token limits. Audio is
never submitted for naming. A background worker operates while the server is open,
starting work when recording and transcription are idle. Titles cannot interrupt
successful transcription; each failed/interrupted request requires an explicit retry.

The pencil beside every recording/transcript opens an inline field. Enter saves;
Escape cancels. Editing is free, works without transcription, and wins over an
in-flight AI request. Search and history updates preserve an open title editor.
Errors are shown in the history with a small retry control; no cost confirmation.

New title and durable job-state sidecars live beside recordings as
SESSION.title.json and SESSION.title-job.json. This avoids creating a separated
output folder for an unprocessed recording, which would confuse preparation reuse.
Titles from the prior version remain readable in session_title.json inside outputs;
editing retains that original and writes the new sidecar with previous-title history.
Audio-only deletion retains titles; full-session trash includes the sidecars.

The model alias gpt-4o-mini is used; the configured OpenAI project must permit
access to it. Authentication, permission, quota and network errors leave the
transcript intact and expose a retry/edit option in history.

Code validation uses mocked AI responses. Semantic title quality and model access
remain environment-dependent. The deployed development app was manually checked
for inline edits to both transcribed and untranscribed sessions.
