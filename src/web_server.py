from pathlib import Path
import threading
from functools import wraps
from flask import Flask, jsonify, request, render_template
from recording_controller import RecordingController
from processing_manager import ProcessingManager
from session_history import list_sessions
from session_storage import remove_session_audio, trash_entire_session

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
controller = RecordingController()
processing_manager = ProcessingManager()



operation_lock = threading.Lock()


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

    project_root = Path(__file__).resolve().parent.parent
    transcript_path = (
        project_root
        / "outputs"
        / f"{session_id}_separated"
        / "transcript.txt"
    )

    if not transcript_path.is_file():
        return jsonify({"error": "Transcript not found."}), 404

    return jsonify({
        "session_id": session_id,
        "transcript": transcript_path.read_text(encoding="utf-8"),
    })


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


@app.get("/api/sessions")
def get_sessions():
    """Return saved local recording sessions."""
    return jsonify({"sessions": list_sessions()})


@app.get("/api/health")
def health():
    return jsonify({
        "application": "Nilaris",
        "status": "running",
        "mode": "local"
    })


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=8765,
        debug=False,
        use_reloader=False
    )
