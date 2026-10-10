import threading
from pathlib import Path
from session_service import prepare_session
from transcription_orchestrator import (
    inspect_session,
    transcribe_chunks,
    finalize_session,
    PRICE_PER_MINUTE,
)


class ProcessingManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.job = None

    def start_preparation(self, recording_path):
        with self.lock:
            if self.job and self.job["state"] in (
                "preparing",
                "awaiting_confirmation",
                "transcribing",
            ):
                raise RuntimeError(
                    "Finish or defer the current session before "
                    "preparing another."
                )

            recording_path = Path(recording_path).resolve()
            session_id = recording_path.stem

            self.job = {
                "session_id": session_id,
                "state": "preparing",
                "recording_path": str(recording_path),
                "result": None,
                "error": None,
            }

            threading.Thread(
                target=self._run_preparation,
                args=(str(recording_path),),
                daemon=True,
            ).start()

            return dict(self.job)

    def _run_preparation(self, recording_path):
        try:
            result = prepare_session(recording_path)

            with self.lock:
                if self.job["recording_path"] == recording_path:
                    self.job["state"] = "awaiting_confirmation"
                    self.job["result"] = result

        except Exception as error:
            with self.lock:
                if self.job["recording_path"] == recording_path:
                    self.job["state"] = "failed"
                    self.job["error"] = str(error)

    def get_transcription_quote(self, session_id):
        """Calculate the current Whisper cost without sending audio."""
        with self.lock:
            if self.job is None:
                raise RuntimeError("No active processing job.")

            if self.job["session_id"] != session_id:
                raise RuntimeError("Session ID does not match.")

            if self.job["state"] != "awaiting_confirmation":
                raise RuntimeError(
                    "Session is not awaiting transcription approval."
                )

            session_dir = self.job["result"]["session_dir"]

            ready_chunks = inspect_session(session_dir)

            minutes = sum(
                chunk["duration_seconds"] / 60
                for chunk in ready_chunks
            )

            cost = minutes * PRICE_PER_MINUTE

            return {
                "session_id": session_id,
                "ready_chunks": len(ready_chunks),
                "estimated_minutes": round(minutes, 2),
                "estimated_cost_usd": round(cost, 5),
                "api_request_sent": False,
            }

    @staticmethod
    def is_cost_authorized(approved_cost, current_cost):
        """Allow increases strictly below $0.50."""
        if approved_cost < 0 or current_cost < 0:
            return False

        if approved_cost == 0 and current_cost > 0:
            return False

        return current_cost < approved_cost + 0.50

    def reserve_transcription(self, session_id):
        """Reserve a session after explicit approval, without API calls."""
        with self.lock:
            if self.job is None:
                raise RuntimeError("No active processing job.")

            if self.job["session_id"] != session_id:
                raise RuntimeError("Session ID does not match.")

            if self.job["state"] != "awaiting_confirmation":
                raise RuntimeError(
                    "Session is not awaiting transcription approval."
                )

            preparation = self.job["result"]

            if not preparation:
                raise RuntimeError("Session preparation is incomplete.")

            session_dir = preparation["session_dir"]

            # The previously displayed preparation quote is the
            # amount the user approves by clicking Confirm.
            approved_cost = preparation["estimated_cost_usd"]

            # Recalculate from the actual pending audio chunks.
            ready_chunks = inspect_session(session_dir)

            current_minutes = sum(
                chunk["duration_seconds"] / 60
                for chunk in ready_chunks
            )

            current_cost = current_minutes * PRICE_PER_MINUTE

            if not self.is_cost_authorized(
                approved_cost, current_cost
            ):
                raise RuntimeError(
                    "Transcription cost changed beyond the approved "
                    "tolerance. New approval required. "
                    f"Approved: ${approved_cost:.5f}; "
                    f"Current: ${current_cost:.5f}"
                )

            # Freeze the approved chunk list for the worker.
            self.job["approved_chunks"] = list(ready_chunks)

            # Reserve before any future worker can be started.
            self.job["state"] = "transcribing"
            self.job["approved_cost_usd"] = approved_cost
            self.job["execution_estimated_cost_usd"] = round(
                current_cost, 5
            )

            return {
                "session_id": session_id,
                "state": "transcribing",
                "session_dir": session_dir,
                "approved_cost_usd": approved_cost,
                "execution_estimated_cost_usd": round(
                    current_cost, 5
                ),
                "api_request_sent": False,
            }

    def confirm_and_start(self, session_id):
        """Authorize and launch a background transcription job.

        Calling this function can incur Whisper API charges.
        """
        reservation = self.reserve_transcription(session_id)

        session_dir = reservation["session_dir"]

        worker = threading.Thread(
            target=self._run_transcription,
            args=(session_id, session_dir),
            daemon=True,
        )

        try:
            worker.start()
        except Exception:
            with self.lock:
                if (
                    self.job
                    and self.job["session_id"] == session_id
                    and self.job["state"] == "transcribing"
                ):
                    self.job["state"] = "failed"
                    self.job["error"] = "Could not start transcription worker."
            raise

        return {
            "session_id": session_id,
            "state": "transcribing",
            "approved_cost_usd": reservation["approved_cost_usd"],
            "execution_estimated_cost_usd":
                reservation["execution_estimated_cost_usd"],
        }

    def _run_transcription(self, session_id, session_dir):
        """Run Whisper in the background for one approved session."""
        try:
            with self.lock:
                if (
                    self.job is None
                    or self.job["session_id"] != session_id
                    or self.job["state"] != "transcribing"
                ):
                    raise RuntimeError("Invalid transcription job.")

                ready_chunks = list(self.job["approved_chunks"])

            if ready_chunks:
                transcribe_chunks(ready_chunks)

            result = finalize_session(session_dir)

            with self.lock:
                if self.job and self.job["session_id"] == session_id:
                    self.job["state"] = "completed"
                    self.job["result"] = result
                    self.job["error"] = None

        except Exception as error:
            with self.lock:
                if self.job and self.job["session_id"] == session_id:
                    self.job["state"] = "failed"
                    self.job["error"] = str(error)

    def defer_session(self, session_id):
        """Release the active job while preserving session files."""
        with self.lock:
            if self.job is None:
                raise RuntimeError("No active processing job.")

            if self.job["session_id"] != session_id:
                raise RuntimeError("Session ID does not match.")

            if self.job["state"] != "awaiting_confirmation":
                raise RuntimeError(
                    "Only sessions awaiting confirmation can be deferred."
                )

            self.job["state"] = "deferred"

            return dict(self.job)

    def get_status(self):
        """Return JSON-safe job status without internal chunk data."""
        with self.lock:
            if self.job is None:
                return {"state": "idle"}

            return {
                key: value
                for key, value in self.job.items()
                if key != "approved_chunks"
            }
