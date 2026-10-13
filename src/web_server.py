from media_environment import configure_media_path

configure_media_path()

from pathlib import Path
import json
import threading
from functools import wraps
from flask import Flask, jsonify, request, render_template, send_file
from recording_controller import RecordingController
from processing_manager import ProcessingManager
from session_history import list_sessions
from app_paths import OUTPUTS_DIR
from session_storage import remove_session_audio, trash_entire_session
from session_titles import TitleService, AutomaticTitleWorker, save_title
from session_audio_export import AudioExportManager, cached_export
from meeting_notes import NotesManager, notes_paths

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
controller = RecordingController()
processing_manager = ProcessingManager()
audio_export_manager = AudioExportManager()



operation_lock = threading.Lock()
notes_manager = NotesManager(save_guard=operation_lock)
title_service = TitleService(save_guard=operation_lock)
title_worker = AutomaticTitleWorker(title_service, operation_lock,
    lambda: controller.get_status().get('state') in ('idle', 'auto_stopped')
        and processing_manager.get_status().get('state') not in ('preparing', 'awaiting_confirmation', 'transcribing'))


def coordinated_storage_operation(function):
    """Prevent starts and storage changes from overlapping."""

    @wraps(function)
    def guarded(*args, **kwargs):
        if not operation_lock.acquire(blocking=False):
            return jsonify({
                "error": "Another Nilaris operation is in progress. "
                         "Please try again shortly."
            }), 409

        try:
            return function(*args, **kwargs)
        finally:
            operation_lock.release()

    return guarded


@app.get("/")
def home():
    return render_template("index.html")


ALLOWED_ORIGIN = "http://127.0.0.1:8765"


@app.before_request
def protect_recording_api():
    if (
        request.path.startswith((
            "/api/recording/",
            "/api/processing/prepare",
            "/api/processing/defer",
            "/api/processing/confirm",
        ))
        or (
            request.path.startswith("/api/sessions/")
            and request.path.endswith((
                "/prepare",
                "/remove-audio",
                "/trash",
                "/title",
                "/title/quote",
                "/title/generate",
                "/title/retry",
                "/audio/prepare",
                "/notes/generate",
            ))
        )
    ):
        if request.method != "POST":
            return jsonify({"error": "Method not allowed"}), 405

        if request.headers.get("Origin") != ALLOWED_ORIGIN:
            return jsonify({
                "error": "Request origin not allowed"
            }), 403


@app.post("/api/recording/start")
@coordinated_storage_operation
def start_recording():
    try:
        result = controller.start_recording()
        return jsonify(result)
    except RuntimeError as error:
        return jsonify({"error": str(error)}), 409


@app.post("/api/recording/stop")
def stop_recording():
    try:
        result = controller.stop_recording()
        return jsonify(result)
    except RuntimeError as error:
        return jsonify({"error": str(error)}), 409



@app.post("/api/recording/recover")
def recover_recording():
    try:
        result = controller.acknowledge_recorder_error()
        return jsonify(result)
    except RuntimeError as error:
        return jsonify({"error": str(error)}), 409


@app.post("/api/processing/prepare")
@coordinated_storage_operation
def prepare_recording():
    recording = controller.last_completed_recording

    if not recording:
        return jsonify({
            "error": "No completed recording available."
        }), 409

    try:
        result = processing_manager.start_preparation(
            recording["recording_path"]
        )
        return jsonify(result), 202

    except RuntimeError as error:
        return jsonify({"error": str(error)}), 409



@app.post("/api/processing/defer")
def defer_processing():
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id")

    if not isinstance(session_id, str) or not session_id:
        return jsonify({"error": "Session ID is required."}), 400

    try:
        result = processing_manager.defer_session(session_id)
        return jsonify(result)
    except RuntimeError as error:
        return jsonify({"error": str(error)}), 409



@app.post("/api/processing/confirm")
@coordinated_storage_operation
def confirm_processing():
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id")

    if not isinstance(session_id, str) or not session_id:
        return jsonify({"error": "Session ID is required."}), 400

    try:
        result = processing_manager.confirm_and_start(session_id)
        return jsonify(result), 202
    except RuntimeError as error:
        return jsonify({"error": str(error)}), 409


@app.get("/api/processing/status")
def processing_status():
    return jsonify(processing_manager.get_status())


@app.get("/api/status")
def recording_status():
    return jsonify(controller.get_status())


@app.post("/api/sessions/<session_id>/notes/generate")
@coordinated_storage_operation
def generate_session_notes(session_id):
    try:
        return jsonify(notes_manager.start(session_id)), 202
    except FileNotFoundError as error:
        return jsonify({'error': str(error)}), 404
    except ValueError as error:
        return jsonify({'error': str(error)}), 400
    except RuntimeError as error:
        return jsonify({'error': str(error)}), 409


@app.get("/api/sessions/<session_id>/notes/status")
def session_notes_status(session_id):
    try:
        return jsonify(notes_manager.status(session_id))
    except FileNotFoundError as error:
        return jsonify({'error': str(error)}), 404
    except ValueError as error:
        return jsonify({'error': str(error)}), 400


@app.get("/api/sessions/<session_id>/download/notes")
def download_session_notes(session_id):
    try:
        state = notes_manager.status(session_id)
        if state['state'] != 'ready':
            return jsonify({'error': 'Generate AI notes before downloading.'}), 409
        document_path = notes_manager.download_document(session_id)
        return send_file(document_path, as_attachment=True,
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            download_name=f"{session_id}_ai_notes_{state['language']}.docx")
    except FileNotFoundError as error:
        return jsonify({'error': str(error)}), 404
    except ValueError as error:
        return jsonify({'error': str(error)}), 400


@app.get("/api/sessions/<session_id>/transcript")
def get_session_transcript(session_id):
    """Read an existing transcript without modifying files."""

    if (
        not session_id.startswith("nilaris_")
        or len(session_id) != 28
        or not all(
            char.isalnum() or char in "_-"
            for char in session_id
        )
    ):
        return jsonify({"error": "Invalid session ID."}), 400

    transcript_path = (
        OUTPUTS_DIR
        / f"{session_id}_separated"
        / "transcript.txt"
    )

    if not transcript_path.is_file():
        return jsonify({"error": "Transcript not found."}), 404

    return jsonify({
        "session_id": session_id,
        "transcript": transcript_path.read_text(encoding="utf-8"),
    })


@app.get("/api/sessions/<session_id>/download/txt")
def download_session_txt(session_id):
    """Download the original transcript without modifying it."""

    import re

    if not re.fullmatch(
        r"nilaris_\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z",
        session_id,
    ):
        return jsonify({"error": "Invalid session ID."}), 400

    session_dir = OUTPUTS_DIR / f"{session_id}_separated"
    transcript_path = session_dir / "transcript.txt"

    if session_dir.is_symlink() or transcript_path.is_symlink():
        return jsonify({"error": "Unsafe transcript path."}), 400

    if not transcript_path.is_file():
        return jsonify({"error": "Transcript not found."}), 404

    return send_file(
        transcript_path,
        mimetype="text/plain; charset=utf-8",
        as_attachment=True,
        download_name=f"{session_id}_transcript.txt",
    )



@app.post("/api/sessions/<session_id>/prepare")
@coordinated_storage_operation
def prepare_saved_session(session_id):
    """Prepare an existing local recording without calling Whisper."""

    sessions = list_sessions()

    session = next(
        (item for item in sessions
         if item["session_id"] == session_id),
        None
    )

    if session is None:
        return jsonify({"error": "Saved session not found."}), 404

    if session["has_transcript"]:
        return jsonify({
            "error": "Session already transcribed. View its transcript."
        }), 409

    recorder_status = controller.get_status()

    if recorder_status["state"] in ("recording", "starting"):
        return jsonify({
            "error": "Stop the active recording before preparing a saved session."
        }), 409

    try:
        result = processing_manager.start_preparation(
            session["recording_path"]
        )
        return jsonify(result), 202

    except RuntimeError as error:
        return jsonify({"error": str(error)}), 409



def ensure_session_storage_available():
    """Block storage changes while recording or processing is active."""

    if notes_manager.busy():
        raise RuntimeError('Wait for AI notes generation to finish before managing session files.')
    if audio_export_manager.busy():
        raise RuntimeError('Wait for audio download preparation to finish before managing session files.')
    recording_state = controller.get_status().get("state")

    if recording_state not in ("idle", "auto_stopped"):
        raise RuntimeError(
            "Stop or recover the recording before managing session files."
        )

    processing_state = processing_manager.get_status().get("state")

    if processing_state in (
        "preparing",
        "awaiting_confirmation",
        "transcribing",
    ):
        raise RuntimeError(
            "Finish or defer the current processing job before deleting files."
        )


@app.post("/api/sessions/<session_id>/remove-audio")
@coordinated_storage_operation
def remove_saved_session_audio(session_id):
    """Move session audio to macOS Trash, preserving the transcript."""

    try:
        ensure_session_storage_available()
        result = remove_session_audio(session_id)
        return jsonify(result)

    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    except FileNotFoundError as error:
        return jsonify({"error": str(error)}), 404

    except RuntimeError as error:
        return jsonify({"error": str(error)}), 409


@app.post("/api/sessions/<session_id>/trash")
@coordinated_storage_operation
def trash_saved_session(session_id):
    """Move a complete saved session to macOS Trash."""

    try:
        ensure_session_storage_available()
        result = trash_entire_session(session_id)
        return jsonify(result)

    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    except FileNotFoundError as error:
        return jsonify({"error": str(error)}), 404

    except RuntimeError as error:
        return jsonify({"error": str(error)}), 409


@app.post("/api/sessions/<session_id>/audio/prepare")
@coordinated_storage_operation
def prepare_session_audio_download(session_id):
    try:
        if controller.get_status().get('state') not in ('idle', 'auto_stopped'):
            raise RuntimeError('Stop or recover the active recording before preparing an audio download.')
        return jsonify(audio_export_manager.prepare(session_id)), 202
    except ValueError as error:
        return jsonify({'error': str(error)}), 400
    except FileNotFoundError as error:
        return jsonify({'error': str(error)}), 404
    except RuntimeError as error:
        return jsonify({'error': str(error)}), 409


@app.get("/api/sessions/<session_id>/audio/status")
def session_audio_download_status(session_id):
    try:
        return jsonify(audio_export_manager.status(session_id))
    except ValueError as error:
        return jsonify({'error': str(error)}), 400


@app.get("/api/sessions/<session_id>/download/audio")
@coordinated_storage_operation
def download_session_audio(session_id):
    try:
        audio = cached_export(session_id)
        if audio is None:
            return jsonify({'error': 'Prepare the MP3 download first.'}), 409
        # Open before releasing the lock so a concurrent recoverable cleanup does
        # not invalidate the streamed response on macOS.
        file = audio.open('rb')
        try:
            response = send_file(file, mimetype='audio/mpeg', as_attachment=True,
                download_name=f'{session_id}_audio.mp3', max_age=0)
            response.call_on_close(file.close)
            return response
        except Exception:
            file.close()
            raise
    except ValueError as error:
        return jsonify({'error': str(error)}), 400
    except FileNotFoundError as error:
        return jsonify({'error': str(error)}), 404


@app.post("/api/sessions/<session_id>/title")
@app.post("/api/sessions/<session_id>/title/retry")
@coordinated_storage_operation
def session_title_operation(session_id):
    try:
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            raise ValueError('Invalid title request.')
        if request.path.endswith('/retry'):
            result = title_worker.retry(session_id)
        else:
            result = save_title(session_id, data.get('title'))
        return jsonify(result)
    except ValueError as error:
        return jsonify({'error': str(error)}), 400
    except FileNotFoundError as error:
        return jsonify({'error': str(error)}), 404
    except RuntimeError as error:
        return jsonify({'error': str(error)}), 409
    except Exception:
        app.logger.exception('Session title operation failed')
        return jsonify({'error': 'Title operation failed; existing title preserved. '
                                  'If an AI request was sent it may have been billed. No automatic retry.'}), 502


@app.get("/api/sessions")
def get_sessions():
    """Return saved local recording sessions."""
    return jsonify({"sessions": list_sessions()})


@app.get("/api/health")
def health():
    return jsonify({
        "application": "Nilaris",
        "audio_export_active": audio_export_manager.busy(),
        "notes_generation_active": notes_manager.busy(),
        "status": "running",
        "mode": "local"
    })


if __name__ == "__main__":
    title_worker.start()
    app.run(
        host="127.0.0.1",
        port=8765,
        debug=False,
        use_reloader=False
    )
