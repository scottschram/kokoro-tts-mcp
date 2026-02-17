# Project: kokoro-tts-mcp

MCP server for Kokoro-82M TTS on Apple Silicon. Lets Claude speak text aloud
in Claude Code, Chat, and Cowork.

## Setup

Requires Python 3.12 (not 3.14 — spacy/pydantic incompatibility).

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install "mcp>=1.2.0" mlx-audio 'misaki[en]<0.9' num2words spacy espeakng_loader
```

Also requires: `brew install espeak`

### Dependency pitfalls

- Pin `misaki[en]<0.9` — 0.9+ breaks `EspeakWrapper.set_data_path`
- Do NOT install `phonemizer` — it shadows `phonemizer-fork` and breaks
  espeak fallback (OOD words get silently skipped)

## Running

```bash
.venv/bin/python3.12 mcp_server.py
```

## Registration

Claude Code:
```bash
claude mcp add kokoro-tts -- \
    /Users/scott/Projects/voice/kokoro-tts-mcp/.venv/bin/python3.12 \
    /Users/scott/Projects/voice/kokoro-tts-mcp/mcp_server.py
```

## Architecture

- Lazy-loads model on first `speak()` / `speak_and_save()` call
- Model stays resident in memory (~600 MB) for fast subsequent calls
- `speak()` is non-blocking — generates audio then plays in background thread
- Pause/resume via sentinel file `/tmp/kokoro-tts-pause` (works with
  `kokoro-pause` toggle script, Stream Deck, Keyboard Maestro, etc.)
- Short text (<25 chars) padded with ` ... ...` to avoid mlx-audio hang bug

## Tools

| Tool | Purpose |
|------|---------|
| `speak(text, voice?, speed?)` | Play text aloud (non-blocking) |
| `pause()` | Pause playback |
| `resume()` | Resume playback |
| `stop()` | Stop playback |
| `status()` | Return idle/playing/paused |
| `speak_and_save(text, output_path?, voice?, speed?, mp3?)` | Save audio file |
| `list_voices()` | List available voices |

## Voices

Default: `af_heart`. Prefix: a=American, b=British; f=female, m=male.

American Female: af_heart, af_alloy, af_aoede, af_bella, af_jessica,
af_kore, af_nicole, af_nova, af_river, af_sarah, af_sky

American Male: am_adam, am_echo, am_eric, am_fenrir, am_liam,
am_michael, am_onyx, am_puck, am_santa

British Female: bf_alice, bf_emma, bf_isabella, bf_lily

British Male: bm_daniel, bm_fable, bm_george, bm_lewis
