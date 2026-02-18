# kokoro-tts-mcp

An MCP (Model Context Protocol) server that provides text-to-speech using the [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) model, accelerated with [MLX](https://github.com/ml-explore/mlx) on Apple Silicon. Enables Claude Code, Claude Chat, and Claude Cowork to speak text aloud on a Mac.

The server lazy-loads the model on first use and keeps it resident in memory (~600 MB), eliminating cold-start latency on subsequent requests. Audio generation takes roughly 1.5 seconds per request after the initial load.

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

### Claude Code

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

### Claude Desktop (Chat / Cowork)

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

### Standalone

```bash
.venv/bin/python3.12 mcp_server.py
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

## External Playback Control

Two shell scripts are included for controlling playback from outside Claude (e.g., via Stream Deck, Keyboard Maestro, or a hotkey):

- **`kokoro-pause`** — Toggle pause/resume. Also supports `kokoro-pause pause`, `kokoro-pause resume`, and `kokoro-pause status`.
- **`kokoro-stop`** — Stop playback immediately and discard audio.

These work by creating/removing sentinel files that the server monitors during playback.

## Known Issues

- **Python 3.13+ not supported** — spacy and pydantic have incompatibilities on 3.13+. Use Python 3.12.
- **Short text workaround** — Text under 25 characters is automatically padded to avoid an mlx-audio hang bug. This is handled transparently.
- **Do not install `phonemizer`** — The `phonemizer` package conflicts with `phonemizer-fork` (pulled in by mlx-audio). Installing it causes out-of-dictionary words to be silently skipped. See `requirements.txt` for details.
- **`misaki` must be <0.9** — Version 0.9+ breaks `EspeakWrapper.set_data_path`. This is pinned in `requirements.txt`.

## License

[MIT](LICENSE)
