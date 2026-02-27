# kokoro-tts-mcp

Text-to-speech using the [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) model, accelerated with [MLX](https://github.com/ml-explore/mlx) on Apple Silicon. Works two ways:

- **MCP server** — givegives local Claude and Codex clients (Claude Chat/Code/Cowork, Codex App, Codex CLI) the ability to speak text aloud and convert text to audio.
- Not supported yet: ChatGPT Mac App
- **Command-line tool** — `kokoro` command for use in scripts, the terminal, or piped workflows

Both share the same generation engine and playback code, so pause/stop controls (via Stream Deck, hotkeys, etc.) work identically regardless of how audio was started.

The MCP server lazy-loads the model on first use and keeps it resident in memory (~600 MB), so subsequent requests start instantly. The CLI loads the model fresh each invocation (~3s startup), which is negligible for longer text.

## Requirements

- macOS on Apple Silicon (M1/M2/M3/M4)
- Python 3.12 (not 3.13+ due to spacy/pydantic incompatibility)
- espeak (`brew install espeak`)
- ffmpeg (optional, only needed for MP3 export)

## Setup

```bash
git clone https://github.com/scottschram/kokoro-tts-mcp.git
cd kokoro-tts-mcp

python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

After installing, download the spaCy English model:

```bash
python -m spacy download en_core_web_sm
```

## Usage

### Command Line

```bash
kokoro "Hello, world."                         # play immediately
cat article.txt | kokoro                       # pipe input
kokoro -v bm_fable "Good morning, London."     # British male voice
kokoro -f article.txt -o article.wav           # save to WAV
kokoro -f article.txt --mp3                    # save as MP3 to /tmp
kokoro -o talk.wav -p "Hello"                  # save AND play
kokoro -s 1.3 "A bit faster."                 # speed adjustment
kokoro -v list                                 # show all voices
kokoro -h                                      # full help
```

Playback streams chunk-by-chunk, so even very long text (tested with 1500+ words) starts playing within a few seconds. Pause and stop work at any point during playback.

To make `kokoro` available globally, symlink it:

```bash
ln -sf /path/to/kokoro-tts-mcp/kokoro ~/bin/kokoro
```

### MCP Server (Claude Code)

Register the MCP server:

```bash
claude mcp add kokoro-tts -- \
    /path/to/kokoro-tts-mcp/.venv/bin/python3.12 \
    /path/to/kokoro-tts-mcp/mcp_server.py
```

Then in Claude Code, you can ask Claude to speak:

> "Say hello"
> "Read that summary aloud using the British male voice bm_george"
> "Save that explanation as an MP3"

### MCP Server (Claude Desktop — Chat / Cowork)

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "kokoro-tts": {
      "command": "/path/to/kokoro-tts-mcp/.venv/bin/python3.12",
      "args": ["/path/to/kokoro-tts-mcp/mcp_server.py"]
    }
  }
}
```

Restart the Claude app after editing.

### MCP Server (Codex CLI)

Register the MCP server:

```bash
codex mcp add kokoro-tts -- \
    /path/to/kokoro-tts-mcp/.venv/bin/python3.12 \
    /path/to/kokoro-tts-mcp/mcp_server.py
```

Then in Codex CLI, you can ask Codex to speak:

> "Say hello"
> "Read that summary aloud using the British male voice bm_george"
> "Save that explanation as an MP3"

### MCP Server (Codex Mac App)

Codex Mac App and Codex CLI share the same global Codex config (`~/.codex/config.toml`).
After registering `kokoro-tts` with `codex mcp add ...` in a terminal, restart the Codex app.

### Smoke Test

A quick test script to verify the TTS pipeline without MCP or the full CLI:

```bash
./test-tts                          # default test phrase
./test-tts "Custom text"            # speak custom text
./test-tts "Cheerio" bm_fable       # specify voice
```

## Tools

| Tool | Description |
|------|-------------|
| `speak(text, voice?, speed?)` | Play text aloud (non-blocking, returns immediately) |
| `pause()` | Pause current playback |
| `resume()` | Resume paused playback |
| `stop()` | Stop playback immediately |
| `status()` | Return current state: `idle`, `playing`, or `paused` |
| `speak_and_save(text, output_path?, voice?, speed?, mp3?)` | Generate and save audio to a file |
| `list_voices()` | List all available voices |

## Voices

28 English voices are available. The naming convention is: first letter = accent (`a` = American, `b` = British), second letter = gender (`f` = female, `m` = male).

**American Female:** af_heart (default), af_alloy, af_aoede, af_bella, af_jessica, af_kore, af_nicole, af_nova, af_river, af_sarah, af_sky

**American Male:** am_adam, am_echo, am_eric, am_fenrir, am_liam, am_michael, am_onyx, am_puck, am_santa

**British Female:** bf_alice, bf_emma, bf_isabella, bf_lily

**British Male:** bm_daniel, bm_fable, bm_george, bm_lewis

## Playback Control

Two shell scripts control playback from outside Claude (e.g., via Stream Deck, Keyboard Maestro, or a hotkey). They work with both the MCP server and the CLI — whichever is currently playing:

- **`kokoro-pause`** — Toggle pause/resume. Also supports `kokoro-pause pause`, `kokoro-pause resume`, and `kokoro-pause status`.
- **`kokoro-stop`** — Stop playback immediately and discard audio.

These work by creating/removing sentinel files (`/tmp/kokoro-tts-pause`, `/tmp/kokoro-tts-stop`) that the playback loop monitors.

## Known Issues

- **Python 3.13+ not supported** — spacy and pydantic have incompatibilities on 3.13+. Use Python 3.12.
- **Short text workaround** — Text under 25 characters is automatically padded to avoid an mlx-audio hang bug. This is handled transparently.
- **Do not install `phonemizer`** — The `phonemizer` package conflicts with `phonemizer-fork` (pulled in by mlx-audio). Installing it causes out-of-dictionary words to be silently skipped. See `requirements.txt` for details.
- **`misaki` must be <0.9** — Version 0.9+ breaks `EspeakWrapper.set_data_path`. This is pinned in `requirements.txt`.

## License

[MIT](LICENSE)
