# Plan: Kokoro TTS MCP Server

## Context

We have a working `kokoro` CLI at `~/bin/kokoro` (symlinked from
`/Users/scott/Documents/work/kokoro-tts/kokoro`). It wraps
`mlx_audio.tts.generate` with Kokoro-82M on Apple Silicon.

This project is an MCP server that lets Claude speak responses aloud — in
Claude Code, Claude Chat, and Claude Cowork. It shells out to the `kokoro`
CLI rather than reimplementing TTS logic. Kept as a separate project to
avoid adding dependencies to the kokoro-tts ball of yarn.

## Dependency on kokoro-tts

- Requires `~/bin/kokoro` to exist (symlink to the kokoro-tts project)
- The kokoro-tts project has its own venv with mlx-audio and all TTS deps
- This project only needs the `mcp` Python SDK — lightweight venv

## Project Structure

```
/Users/scott/Documents/work/kokoro-tts-mcp/
├── CLAUDE.md            # Project instructions for Claude
├── PLAN.md              # This file — implementation plan
├── mcp_server.py        # The MCP server (~120 lines)
├── .venv/               # Lightweight: just mcp + deps
└── .gitignore
```

## Tools to Expose

| Tool | Blocking? | Purpose |
|------|-----------|---------|
| `speak(text, voice?, speed?)` | No | Play text aloud. Returns immediately. |
| `stop()` | No | Kill any currently-playing audio. |
| `speak_and_save(text, output_path?, voice?, speed?, mp3?)` | Yes | Save audio file. Returns path. |
| `list_voices()` | No | Return voice list (embedded, instant). |

## Key Design Decisions

1. **Non-blocking `speak`**: Uses `subprocess.Popen` (fire-and-forget).
   Playing audio takes ~16s for 30 words — can't block Claude that long.

2. **Process group kill for `stop`**: The kokoro script spawns
   `python3.12 -m mlx_audio.tts.generate --stream`, which spawns an audio
   player. Use `os.killpg(SIGTERM)` with `preexec_fn=os.setsid` to kill
   the entire process tree.

3. **Kills previous playback before new `speak`**: Prevents audio overlap
   if Claude calls `speak` twice in succession.

4. **`KOKORO_BIN` configurable**: Defaults to `~/bin/kokoro`, overridable
   via `KOKORO_BIN` env var.

5. **FastMCP decorator pattern**: Auto-generates JSON tool schemas from
   Python type hints and docstrings. ~120 lines for the whole server.

6. **Embedded voice list**: `list_voices` returns a hardcoded dict —
   no subprocess needed, instant response.

## Implementation Steps

### Step 1: Create venv

```bash
cd /Users/scott/Documents/work/kokoro-tts-mcp
python3.12 -m venv .venv
source .venv/bin/activate
pip install "mcp>=1.2.0"
```

### Step 2: Create `mcp_server.py`

FastMCP server with 4 tools. Key implementation details:

- **speak**: `subprocess.Popen(cmd, preexec_fn=os.setsid, stdout=DEVNULL, stderr=PIPE)`
  - Calls `~/bin/kokoro "text" -v voice -s speed` (default: stream/play mode)
  - Returns immediately: "Speaking N words with voice X"
  - Stores Popen handle in module-level `_current_process`

- **stop**: `os.killpg(os.getpgid(_current_process.pid), signal.SIGTERM)`
  - Kills process group (kokoro + children)
  - Returns "Stopped audio playback" or "No audio is currently playing"

- **speak_and_save**: `subprocess.run(cmd, timeout=120)`
  - Calls `~/bin/kokoro -n -o path "text"` or `~/bin/kokoro -n --save "text"`
  - Parses "Saved: /path/to/file" from stderr
  - Returns file path

- **list_voices**: Returns embedded dict of all 28 English voices
  - American Female (11): af_heart*, af_alloy, af_aoede, af_bella, etc.
  - American Male (9): am_adam, am_echo, am_eric, etc.
  - British Female (4): bf_alice, bf_emma, bf_isabella, bf_lily
  - British Male (4): bm_daniel, bm_fable, bm_george, bm_lewis

### Step 3: Create `.gitignore`, `CLAUDE.md`

### Step 4: Git init and initial commit

### Step 5: Register with Claude Code

```bash
claude mcp add kokoro-tts -- \
    /Users/scott/Documents/work/kokoro-tts-mcp/.venv/bin/python3.12 \
    /Users/scott/Documents/work/kokoro-tts-mcp/mcp_server.py
```

### Step 6: Register with Claude Desktop (Chat/Cowork)

Add `mcpServers` to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
"mcpServers": {
  "kokoro-tts": {
    "command": "/Users/scott/Documents/work/kokoro-tts-mcp/.venv/bin/python3.12",
    "args": ["/Users/scott/Documents/work/kokoro-tts-mcp/mcp_server.py"]
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

1. Venv installs cleanly with just `mcp`
2. `/mcp` in Claude Code shows `kokoro-tts` connected with 4 tools
3. "Say hello using kokoro" — audio plays, Claude doesn't block
4. "Stop the audio" — playback stops
5. "Save a greeting as MP3" — returns file path
6. "What voices are available?" — returns voice list
