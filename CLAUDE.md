# kokoro-tts-mcp

See [README.md](README.md) for setup, usage, and voice reference.

## Architecture

- Single file: `mcp_server.py` — FastMCP server with 7 tools
- Lazy-loads Kokoro-82M on first `speak()` / `speak_and_save()` call
- Model stays resident in memory (~600 MB) for fast subsequent calls
- `speak()` is non-blocking — generates audio then plays in background thread
- Kills previous playback before starting new `speak()` — prevents audio overlap
- Pause/resume via sentinel file `/tmp/kokoro-tts-pause`
- Stop via sentinel file `/tmp/kokoro-tts-stop` — clears pause state too
- Short text (<25 chars) padded with ` ... ...` to avoid mlx-audio hang bug
- `stdout` redirected to `stderr` during model load and generation to avoid
  corrupting the MCP JSON-RPC transport

## Key Constraints

- Requires Python 3.12 (not 3.13+ — spacy/pydantic incompatibility)
- Pin `misaki[en]<0.9` — 0.9+ breaks `EspeakWrapper.set_data_path`
- Do NOT install `phonemizer` — it shadows `phonemizer-fork` and breaks
  espeak fallback (OOD words get silently skipped)
- Requires `brew install espeak` on macOS

## Design Decisions

- **In-process model, not CLI subprocess**: Shelling out on every `speak()` would
  mean full Python startup + model load each time (~1.7s). Lazy in-process loading
  amortizes this — first call ~3.2s, every subsequent call ~1.5s.
- **Separate venv**: Keeps the full TTS dependency stack isolated.
- **Sentinel files for external control**: Allows Stream Deck, Keyboard Maestro,
  or any script to pause/stop without going through Claude. The MCP tools use
  the same mechanism, keeping both paths consistent.
- **Embedded voice list**: `list_voices()` returns a hardcoded dict — no
  computation needed, instant response.
- **FastMCP decorator pattern**: Auto-generates JSON tool schemas from Python
  type hints and docstrings.

## Memory Budget

- ~30-40 MB idle (model not yet loaded)
- ~600 MB after first TTS use (model resident)
- ~800 MB peak during generation
