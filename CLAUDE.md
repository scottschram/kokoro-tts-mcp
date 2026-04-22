# kokoro-tts-mcp

See [README.md](README.md) for setup, usage, and voice reference.

## Architecture

- Single file: `mcp_server.py` — FastMCP server with 8 tools
- Lazy-loads Kokoro-82M on first `speak()` / `speak_and_save()` call
- Model stays resident in memory (~600 MB) for fast subsequent calls
- `speak()` is non-blocking — streams audio chunk-by-chunk in background thread
- Chunks generated lazily via pipeline (510 phoneme limit per chunk), played as produced
- Single `sd.OutputStream` kept open across all chunks for seamless audio
- Kills previous playback before starting new `speak()` — prevents audio overlap
- Pause/resume via sentinel file `/tmp/kokoro-tts-pause`
- Stop via sentinel file `/tmp/kokoro-tts-stop` — clears pause state too
- `user_stop_requested()` checks and clears stop sentinel — lets Claude detect
  external stops (e.g. Stream Deck) when playing multiple segments sequentially
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
- **Streaming generate-and-play**: `speak()` returns immediately; a background
  thread iterates `model.generate()` lazily, playing each chunk as it's produced.
  This avoids blocking the MCP client for long text (previous approach collected
  all audio before playback, causing timeouts on 500+ word text).

## Memory Budget

- ~30-40 MB idle (model not yet loaded)
- ~600 MB after first TTS use (model resident)
- ~800 MB peak during generation

## MCP Client Size Limit (tested 2026-04-22)

The `speak()` MCP tool has a practical ceiling of ~2500 words per call.
Not a pipeline issue — a scaling issue in the MCP client path above us.

Bisection (input was 5000-word Emma excerpt unless noted):

- CLI direct (`kokoro -f file.txt`): 10,000 words, RTF ~0.03×, works fine.
- Direct Python import of `speak()`: returns in 1.81s, audio starts at 1.92s.
- Manual JSON-RPC over stdio to `mcp_server.py`: tools/call roundtrip
  1.54s, audio plays.
- Via Claude Code MCP tool call:
  - 2500 words: audio in seconds ✅
  - 3000 words: ~3 min before audio (technically works, unusable)
  - 5000 words: ~4 min before audio, returned and played (retested
    with mcp 1.27.0). Previously reported as a hang at 3500–5000
    words on mcp 1.26.0, but we may simply not have waited long
    enough — the scaling looks roughly linear with text size.

So everything up to and including FastMCP stdio handles 5000 words in
under 2 seconds. The hang and latency appear only when a Claude Code
`tool_use` block with a large text argument is in flight. Not in our
code to fix.

To retest whether the limit has been lifted: ask Claude (Code or Chat)
to speak the contents of `input-test/emma_5000.txt` via the `speak()`
MCP tool. If first audio arrives within seconds, the "2500-word
recommendation" block in `speak()`'s docstring can be removed and
the README note revised. `status()`'s docstring should stay a
one-liner regardless — polling it in a loop burns tool-use budget in
Claude Chat, so the old "poll until idle" guidance is wrong even if
the size limit is gone.
