"""
Kokoro TTS MCP Server — speak text aloud from Claude Code / Chat / Cowork.

Lazy-loads Kokoro-82M on first use, keeps it resident for fast subsequent calls.
"""

import contextlib
import fcntl
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
from mcp.server.fastmcp import FastMCP

# ── Constants ────────────────────────────────────────────────────────────

MODEL_ID = "mlx-community/Kokoro-82M-bf16"
DEFAULT_VOICE = "af_heart"
DEFAULT_SPEED = 1.0
SAMPLE_RATE = 24000
SENTINEL = "/tmp/kokoro-tts-pause"
STOP_SENTINEL = "/tmp/kokoro-tts-stop"
PLAYBACK_LOCKFILE = "/tmp/kokoro-tts-playback.lock"
SHORT_TEXT_THRESHOLD = 25
SHORT_TEXT_PAD = " ... ..."

VOICES = {
    "American Female": [
        "af_heart (default)", "af_alloy", "af_aoede", "af_bella",
        "af_jessica", "af_kore", "af_nicole", "af_nova",
        "af_river", "af_sarah", "af_sky",
    ],
    "American Male": [
        "am_adam", "am_echo", "am_eric", "am_fenrir",
        "am_liam", "am_michael", "am_onyx", "am_puck", "am_santa",
    ],
    "British Female": ["bf_alice", "bf_emma", "bf_isabella", "bf_lily"],
    "British Male": ["bm_daniel", "bm_fable", "bm_george", "bm_lewis"],
}

# ── Lazy model state ─────────────────────────────────────────────────────

_model = None
_model_lock = threading.Lock()


def _get_model():
    """Load Kokoro-82M on first call, return cached model thereafter."""
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        from mlx_audio.tts.utils import load_model
        with contextlib.redirect_stdout(sys.stderr):
            _model = load_model(model_path=MODEL_ID)
        return _model


def _lang_code(voice: str) -> str:
    """Derive language code from voice prefix (a=American, b=British)."""
    if voice and voice[0] in ("a", "b", "j", "z"):
        return voice[0]
    return "a"


# ── Playback state ───────────────────────────────────────────────────────

_playback_lock = threading.Lock()
_playback_thread: threading.Thread | None = None
_playback_stream: sd.OutputStream | None = None
_playback_stop = threading.Event()  # signal thread to stop
_playback_state = "idle"  # idle | playing | paused
_playback_session = 0  # incremented to invalidate older playback workers


def _set_state(state: str):
    global _playback_state
    _playback_state = state


def _next_playback_session() -> int:
    """Return a new playback session id and invalidate older workers."""
    global _playback_session
    with _playback_lock:
        _playback_session += 1
        return _playback_session


def _is_current_session(session_id: int) -> bool:
    with _playback_lock:
        return session_id == _playback_session


def _request_global_stop():
    """Signal any external player process to stop and clear pause state."""
    Path(STOP_SENTINEL).touch()
    try:
        os.remove(SENTINEL)
    except FileNotFoundError:
        pass


@contextlib.contextmanager
def _acquire_playback_ownership():
    """Serialize playback across processes so only one stream is active at once."""
    fd = os.open(PLAYBACK_LOCKFILE, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _generate_audio(text: str, voice: str, speed: float) -> np.ndarray:
    """Generate audio samples from text. Returns float32 numpy array."""
    model = _get_model()
    chunks = []
    # Redirect stdout → stderr so library print() calls (e.g. "Creating new
    # KokoroPipeline") don't corrupt the MCP JSON-RPC transport on stdout.
    with contextlib.redirect_stdout(sys.stderr):
        for result in model.generate(
            text=text,
            voice=voice,
            speed=speed,
            lang_code=_lang_code(voice),
        ):
            chunks.append(np.array(result.audio))
    if not chunks:
        return np.array([], dtype=np.float32)
    return np.concatenate(chunks)


def _play_audio(audio: np.ndarray, session_id: int | None = None):
    """Play audio with pause/resume sentinel support. Runs in background thread."""
    global _playback_stream
    if session_id is None:
        session_id = _next_playback_session()

    # Stop any external player process first, then acquire global ownership.
    _request_global_stop()
    with _acquire_playback_ownership():
        _playback_stop.clear()
        # Clean up stale sentinels from a previous session
        for f in (SENTINEL, STOP_SENTINEL):
            try:
                os.remove(f)
            except FileNotFoundError:
                pass
        _set_state("playing")

        stream = None
        try:
            # Write all audio into a sounddevice OutputStream, checking for
            # pause (sentinel file) and stop (event) during playback.
            block_size = 2048
            idx = 0
            stream = sd.OutputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                blocksize=block_size,
                dtype="float32",
            )
            with _playback_lock:
                _playback_stream = stream

            stream.start()

            while idx < len(audio):
                # Check stop — MCP event or sentinel file from kokoro-stop
                if (
                    _playback_stop.is_set()
                    or os.path.exists(STOP_SENTINEL)
                    or not _is_current_session(session_id)
                ):
                    _playback_stop.set()
                    break

                # Check pause sentinel
                if os.path.exists(SENTINEL):
                    _set_state("paused")
                    while (
                        os.path.exists(SENTINEL)
                        and not _playback_stop.is_set()
                        and _is_current_session(session_id)
                    ):
                        if os.path.exists(STOP_SENTINEL):
                            _playback_stop.set()
                            break
                        time.sleep(0.1)
                    if _playback_stop.is_set() or not _is_current_session(session_id):
                        break
                    # Re-check stop before resuming — avoids playing a blip
                    # when kokoro-stop removes pause then thread wakes
                    if os.path.exists(STOP_SENTINEL) or not _is_current_session(session_id):
                        _playback_stop.set()
                        break
                    _set_state("playing")

                end = min(idx + block_size, len(audio))
                chunk = audio[idx:end].reshape(-1, 1)
                stream.write(chunk)
                idx = end

            stream.stop()
            stream.close()
        except Exception:
            pass
        finally:
            with _playback_lock:
                if _playback_stream is stream:
                    _playback_stream = None
            if _is_current_session(session_id):
                _set_state("idle")
                # Clean up sentinel files
                for f in (SENTINEL, STOP_SENTINEL):
                    try:
                        os.remove(f)
                    except FileNotFoundError:
                        pass


def _generate_and_play(text: str, voice: str, speed: float, session_id: int | None = None):
    """Generate audio chunk-by-chunk and play each immediately. Runs in background thread."""
    global _playback_stream
    if session_id is None:
        session_id = _next_playback_session()

    # Stop any external player process first, then acquire global ownership.
    _request_global_stop()
    with _acquire_playback_ownership():
        _playback_stop.clear()
        # Clean up stale sentinels from a previous session
        for f in (SENTINEL, STOP_SENTINEL):
            try:
                os.remove(f)
            except FileNotFoundError:
                pass
        _set_state("playing")

        stream = None
        try:
            model = _get_model()
            block_size = 2048
            stream = sd.OutputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                blocksize=block_size,
                dtype="float32",
            )
            with _playback_lock:
                _playback_stream = stream
            stream.start()

            with contextlib.redirect_stdout(sys.stderr):
                for result in model.generate(
                    text=text,
                    voice=voice,
                    speed=speed,
                    lang_code=_lang_code(voice),
                ):
                    # Check stop before playing this chunk
                    if (
                        _playback_stop.is_set()
                        or os.path.exists(STOP_SENTINEL)
                        or not _is_current_session(session_id)
                    ):
                        _playback_stop.set()
                        break

                    audio = np.array(result.audio)
                    if len(audio) == 0:
                        continue

                    idx = 0
                    while idx < len(audio):
                        # Check stop
                        if (
                            _playback_stop.is_set()
                            or os.path.exists(STOP_SENTINEL)
                            or not _is_current_session(session_id)
                        ):
                            _playback_stop.set()
                            break

                        # Check pause sentinel
                        if os.path.exists(SENTINEL):
                            _set_state("paused")
                            while (
                                os.path.exists(SENTINEL)
                                and not _playback_stop.is_set()
                                and _is_current_session(session_id)
                            ):
                                if os.path.exists(STOP_SENTINEL):
                                    _playback_stop.set()
                                    break
                                time.sleep(0.1)
                            if _playback_stop.is_set() or not _is_current_session(session_id):
                                break
                            if os.path.exists(STOP_SENTINEL) or not _is_current_session(session_id):
                                _playback_stop.set()
                                break
                            _set_state("playing")

                        end = min(idx + block_size, len(audio))
                        chunk = audio[idx:end].reshape(-1, 1)
                        stream.write(chunk)
                        idx = end

                    # Break outer loop if stop was requested during playback
                    if _playback_stop.is_set() or not _is_current_session(session_id):
                        break

            stream.stop()
            stream.close()
        except Exception:
            pass
        finally:
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
            with _playback_lock:
                if _playback_stream is stream:
                    _playback_stream = None
            if _is_current_session(session_id):
                _set_state("idle")
                for f in (SENTINEL, STOP_SENTINEL):
                    try:
                        os.remove(f)
                    except FileNotFoundError:
                        pass


def _stop_playback():
    """Stop current playback immediately."""
    global _playback_thread
    # Signal the background thread to stop — it owns the stream lifecycle
    _playback_stop.set()
    # Invalidate any currently-running playback worker session immediately.
    _next_playback_session()
    # Try to interrupt a blocking stream write from this thread.
    with _playback_lock:
        stream = _playback_stream
    if stream is not None:
        try:
            stream.abort()
        except Exception:
            pass
    # Remove sentinel files so the thread isn't stuck in pause loop
    for f in (SENTINEL, STOP_SENTINEL):
        try:
            os.remove(f)
        except FileNotFoundError:
            pass
    # Wait for the background thread to clean up the stream and exit
    if _playback_thread is not None and _playback_thread.is_alive():
        _playback_thread.join(timeout=3.0)
    _playback_thread = None
    _set_state("idle")


# ── MCP Server ───────────────────────────────────────────────────────────

mcp = FastMCP("kokoro-tts")


@mcp.tool()
def speak(text: str, voice: str = DEFAULT_VOICE, speed: float = DEFAULT_SPEED) -> str:
    """Speak text aloud. Returns immediately while audio plays in background.

    Args:
        text: Text to speak.
        voice: Voice name (e.g. af_heart, bm_fable). Default: af_heart.
        speed: Speed multiplier. Default: 1.0.
    """
    global _playback_thread

    # Kill any current playback first.
    # Check thread liveness too, since state may already be idle if a prior
    # stop timed out while worker teardown was still in progress.
    if _playback_state != "idle" or (_playback_thread is not None and _playback_thread.is_alive()):
        _stop_playback()

    # Short text padding to avoid AudioPlayer hang
    if len(text) < SHORT_TEXT_THRESHOLD:
        text = text + SHORT_TEXT_PAD

    word_count = len(text.split())

    # Ensure model is loaded before returning (first call ~3.2s, subsequent ~0ms)
    _get_model()

    # Generate and play in background thread — streams chunks as they're produced
    session_id = _next_playback_session()
    _playback_thread = threading.Thread(
        target=_generate_and_play, args=(text, voice, speed, session_id), daemon=True
    )
    _playback_thread.start()

    return f"Speaking {word_count} words with voice {voice} at {speed}x speed."


@mcp.tool()
def pause() -> str:
    """Pause current audio playback."""
    if _playback_state == "idle":
        return "No audio is currently playing."
    if _playback_state == "paused":
        return "Already paused."
    Path(SENTINEL).touch()
    return "Paused."


@mcp.tool()
def resume() -> str:
    """Resume paused audio playback."""
    if _playback_state != "paused":
        return "Audio is not paused."
    try:
        os.remove(SENTINEL)
    except FileNotFoundError:
        pass
    return "Resumed."


@mcp.tool()
def stop() -> str:
    """Stop any currently-playing audio immediately."""
    if _playback_state == "idle":
        return "No audio is currently playing."
    _stop_playback()
    return "Stopped audio playback."


@mcp.tool()
def status() -> str:
    """Return current playback state: idle, playing, or paused."""
    return _playback_state


@mcp.tool()
def speak_and_save(
    text: str,
    output_path: str = "/tmp/kokoro_output.wav",
    voice: str = DEFAULT_VOICE,
    speed: float = DEFAULT_SPEED,
    mp3: bool = False,
) -> str:
    """Generate speech and save to a file. Blocks until file is written.

    Args:
        text: Text to speak.
        output_path: Where to save the file. Default: /tmp/kokoro_output.wav.
        voice: Voice name. Default: af_heart.
        speed: Speed multiplier. Default: 1.0.
        mp3: If True, save as MP3 (requires ffmpeg). Default: False.
    """
    if len(text) < SHORT_TEXT_THRESHOLD:
        text = text + SHORT_TEXT_PAD

    audio = _generate_audio(text, voice, speed)
    if len(audio) == 0:
        return "No audio generated."

    # Ensure output directory exists
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Always write WAV first
    wav_path = out.with_suffix(".wav") if mp3 else out
    from mlx_audio.audio_io import write as audio_write
    audio_write(str(wav_path), audio, SAMPLE_RATE)

    if mp3:
        mp3_path = out.with_suffix(".mp3")
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(wav_path),
                "-codec:a", "libmp3lame", "-b:a", "128k", "-ac", "1",
                str(mp3_path),
            ],
            capture_output=True,
        )
        wav_path.unlink(missing_ok=True)
        return f"Saved: {mp3_path}"

    return f"Saved: {wav_path}"


@mcp.tool()
def list_voices() -> dict:
    """List all available Kokoro voices, grouped by accent and gender."""
    return VOICES


if __name__ == "__main__":
    mcp.run()
