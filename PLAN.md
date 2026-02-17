# Plan: Kokoro TTS MCP Server

## Context

We have a working `kokoro` CLI at `~/bin/kokoro` (symlinked from
`/Users/scott/Projects/voice/kokoro-tts/kokoro`). It wraps
`mlx_audio.tts.generate` with Kokoro-82M on Apple Silicon.

This project is an MCP server that lets Claude speak responses aloud — in
Claude Code, Claude Chat, and Claude Cowork. Unlike the CLI wrapper, the
MCP server lazy-loads the model on first use and keeps it resident in
memory, eliminating the ~1.7s cold start on every subsequent request.

## Architecture: in-process model, not CLI subprocess

The CLI wrapper (`kokoro`) is great for one-off use, but shelling out on
every `speak()` call means a full Python startup + model load each time
(~1.7s overhead). For conversational back-and-forth with Claude, that lag
adds up fast.

Instead, this MCP server installs the full TTS dependency stack in its own
venv and lazy-loads the Kokoro-82M model on first use. The server starts
lightweight (~30-40 MB). Once loaded, the model stays resident (~600-800 MB)
for the lifetime of the server process. Each subsequent speech request
costs only ~1.5s for audio generation.

This means a separate venv from kokoro-tts — the MCP server needs both
`mcp` and the full `mlx-audio` stack. Keeping it separate avoids polluting
either project's dependencies.

## Setup

Requires Python 3.12 (not 3.14 — spacy/pydantic incompatibility).

```bash
cd /Users/scott/Projects/voice/kokoro-tts-mcp
python3.12 -m venv .venv
source .venv/bin/activate
pip install "mcp>=1.2.0" mlx-audio 'misaki[en]<0.9' num2words spacy espeakng_loader
```

Also requires espeak system library:
```bash
brew install espeak
```

### Dependency notes (same pitfalls as kokoro-tts)

- Pin `misaki[en]<0.9` — 0.9+ breaks `EspeakWrapper.set_data_path`
- Do NOT explicitly install `phonemizer` — it shadows `phonemizer-fork`
  and breaks espeak fallback (OOD words get silently skipped)
- See `/Users/scott/Projects/voice/kokoro-tts/CLAUDE.md` for full details

## Project Structure

```
/Users/scott/Projects/voice/kokoro-tts-mcp/
├── CLAUDE.md            # Project instructions for Claude
├── PLAN.md              # This file — implementation plan
├── mcp_server.py        # The MCP server
├── kokoro-pause         # Toggle script for external pause/resume
├── .venv/               # Full TTS stack + mcp SDK
└── .gitignore
```

## Tools to Expose

| Tool | Blocking? | Purpose |
|------|-----------|---------|
| `speak(text, voice?, speed?)` | No | Play text aloud. Returns immediately with status. |
| `pause()` | No | Pause current playback. |
| `resume()` | No | Resume paused playback. |
| `stop()` | No | Kill any currently-playing audio. |
| `status()` | No | Return current state: idle, playing, or paused. |
| `speak_and_save(text, output_path?, voice?, speed?, mp3?)` | Yes | Save audio file. Returns path. |
| `list_voices()` | No | Return voice list (embedded, instant). |

## Key Design Decisions

1. **Lazy model loading**: The model is NOT loaded at server startup.
   The server starts lightweight (~30-40 MB). On the first `speak()` or
   `speak_and_save()` call, the Kokoro-82M model and spaCy NLP model
   load on demand (~1.7s, ~600 MB). They then stay resident in memory
   for the lifetime of the process. First call: ~3.2s (load + generate).
   Every subsequent call: ~1.5s (generate only).

2. **Memory budget**: ~30-40 MB idle (no TTS used yet), ~600 MB after
   first use (model loaded), ~800 MB peak during generation. Negligible
   on a 32-64 GB machine. If TTS is never used in a session, the memory
   cost is near zero.

3. **Non-blocking `speak`**: Audio generation runs in a background thread.
   The MCP tool returns immediately ("Speaking N words with voice X")
   while audio plays. This prevents blocking Claude during playback.

4. **Kills previous playback before new `speak`**: Prevents audio overlap
   if Claude calls `speak` twice in succession.

5. **Sentinel file for external pause/resume**: The server monitors
   `/tmp/kokoro-tts-pause` during playback (every ~0.5s). If the file
   exists, playback pauses. If removed, playback resumes. This allows
   external control via Stream Deck, Keyboard Maestro, or any script
   without going through Claude. A toggle script (`kokoro-pause`) is
   included in the project. The MCP `pause()`/`resume()` tools also
   work by creating/removing this file, so both paths are consistent.

6. **Status reporting**: `speak()` returns current playback state
   (idle/playing/paused) so Claude knows not to stack audio on a paused
   session. A dedicated `status()` tool also reports the current state.

7. **Short text padding**: Text under 25 characters gets padded with
   ` ... ...` to avoid the mlx-audio AudioPlayer hang bug (same
   workaround as the CLI wrapper).

8. **FastMCP decorator pattern**: Auto-generates JSON tool schemas from
   Python type hints and docstrings.

9. **Embedded voice list**: `list_voices` returns a hardcoded dict —
   no computation needed, instant response.

10. **Separate venv from kokoro-tts**: Both projects install the same
   TTS dependencies independently. This avoids cross-project dependency
   conflicts and lets each project manage its own versions.

## Implementation Steps

### Step 1: Create venv

```bash
cd /Users/scott/Projects/voice/kokoro-tts-mcp
python3.12 -m venv .venv
source .venv/bin/activate
pip install "mcp>=1.2.0" mlx-audio 'misaki[en]<0.9' num2words spacy espeakng_loader
```

### Step 2: Create `mcp_server.py`

FastMCP server with in-process TTS. Key implementation details:

- **Lazy init**: Module-level `_model = None`. A `_get_model()` function
  checks if `_model` is None, loads Kokoro-82M + spaCy pipeline if so,
  caches in the global, and returns it. Thread-safe with a `threading.Lock`.

- **speak**: Calls `_get_model()` (loads on first use). Generate audio
  in-process using loaded model. Play via `sounddevice` in a background
  thread, checking `/tmp/kokoro-tts-pause` every ~0.5s during playback.
  Return immediately with status (idle/playing/paused).
  Store thread/stream handle in module-level `_current_playback`.

- **pause**: Creates `/tmp/kokoro-tts-pause`. Returns "Paused" or
  "No audio is currently playing".

- **resume**: Removes `/tmp/kokoro-tts-pause`. Returns "Resumed" or
  "Audio is not paused".

- **stop**: Stop current audio playback, remove sentinel file.
  Return "Stopped audio playback" or "No audio is currently playing".

- **status**: Returns current state: "idle", "playing", or "paused".

- **speak_and_save**: Generate audio in-process, write WAV file.
  Optionally convert to MP3 via ffmpeg. Returns file path.

- **list_voices**: Returns embedded dict of all 28 English voices:
  - American Female (11): af_heart*, af_alloy, af_aoede, af_bella, etc.
  - American Male (9): am_adam, am_echo, am_eric, etc.
  - British Female (4): bf_alice, bf_emma, bf_isabella, bf_lily
  - British Male (4): bm_daniel, bm_fable, bm_george, bm_lewis

### Step 3: Create `.gitignore`, `CLAUDE.md`

### Step 4: Git commit

### Step 5: Register with Claude Code

```bash
claude mcp add kokoro-tts -- \
    /Users/scott/Projects/voice/kokoro-tts-mcp/.venv/bin/python3.12 \
    /Users/scott/Projects/voice/kokoro-tts-mcp/mcp_server.py
```

### Step 6: Register with Claude Desktop (Chat/Cowork)

Add `mcpServers` to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
"mcpServers": {
  "kokoro-tts": {
    "command": "/Users/scott/Projects/voice/kokoro-tts-mcp/.venv/bin/python3.12",
    "args": ["/Users/scott/Projects/voice/kokoro-tts-mcp/mcp_server.py"]
  }
}
```

Restart Claude app (Cmd+Q, reopen) after editing.

## Available Voices (for reference)

Naming: first letter = language (a=American, b=British), second = gender (f/m).

- **American Female**: af_heart*, af_alloy, af_aoede, af_bella, af_jessica,
  af_kore, af_nicole, af_nova, af_river, af_sarah, af_sky
- **American Male**: am_adam, am_echo, am_eric, am_fenrir, am_liam,
  am_michael, am_onyx, am_puck, am_santa
- **British Female**: bf_alice, bf_emma, bf_isabella, bf_lily
- **British Male**: bm_daniel, bm_fable, bm_george, bm_lewis

\* = default voice

## Verification

1. Venv installs cleanly with `mcp` + full TTS stack
2. `/mcp` in Claude Code shows `kokoro-tts` connected with 7 tools
3. "Say hello using kokoro" — audio plays, Claude doesn't block
4. "Pause" / "Resume" — playback pauses and resumes
5. `kokoro-pause` script toggles pause from Stream Deck / Keyboard Maestro
6. "Stop the audio" — playback stops
7. "Save a greeting as MP3" — returns file path
8. "What voices are available?" — returns voice list
9. Second `speak()` call returns in ~1.5s (no cold start penalty)
