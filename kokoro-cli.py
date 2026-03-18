#!/usr/bin/env python3
"""
kokoro-cli — Text-to-speech using Kokoro-82M via mlx-audio.

Command-line interface that reuses the MCP server's generation and playback
functions, giving you sentinel-based pause/stop support for free (same
/tmp/kokoro-tts-pause and /tmp/kokoro-tts-stop files used by kokoro-pause
and kokoro-stop scripts).

Usage:
    kokoro "Hello, world."
    echo "text" | kokoro
    kokoro -f article.txt
    kokoro -v bm_fable "Good morning, London."
    kokoro -f article.txt -o article.wav
    kokoro -f article.txt --mp3
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

# Import directly from the MCP server module (same directory)
from mcp_server import (
    DEFAULT_SPEED,
    DEFAULT_VOICE,
    SAMPLE_RATE,
    SHORT_TEXT_PAD,
    SHORT_TEXT_THRESHOLD,
    VOICES,
    _generate_and_play,
    _generate_audio,
    _get_model,
    _play_audio,
    _stop_playback,
)


def format_voices() -> str:
    """Format voice list for display."""
    lines = []
    for group, names in VOICES.items():
        label = f"{group}:"
        voice_str = " ".join(n.replace(" (default)", "*") for n in names)
        lines.append(f"  {label:<20s} {voice_str}")
    lines.append("")
    lines.append("  * = default voice. Language auto-detected from voice prefix.")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="kokoro",
        description="Text-to-speech using Kokoro-82M. Plays audio by default.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  kokoro "Hello, world."                         # play immediately
  cat article.txt | kokoro                       # pipe input, play
  kokoro -v bm_fable "Good morning, London."     # British male voice
  kokoro -f article.txt -o article.wav           # save to file
  kokoro -f article.txt --mp3                    # save as MP3 to /tmp
  kokoro -o talk.wav -p "Hello"                  # save AND play
  kokoro -s 1.3 "A bit faster."                 # speed adjustment
  kokoro -v list                                 # show all voices
""",
    )

    parser.add_argument("text", nargs="*", help="Text to speak")
    parser.add_argument(
        "-v", "--voice", default=DEFAULT_VOICE,
        help=f"Voice name (default: {DEFAULT_VOICE}). Use -v list to show all.",
    )
    parser.add_argument(
        "-s", "--speed", type=float, default=DEFAULT_SPEED,
        help=f"Speed multiplier (default: {DEFAULT_SPEED})",
    )
    parser.add_argument(
        "-o", "--output", metavar="FILE",
        help="Save to file (suppresses play unless -p also given)",
    )
    parser.add_argument(
        "--mp3", action="store_true",
        help="Save as MP3 via ffmpeg (implies --save if no -o)",
    )
    parser.add_argument(
        "--save", action="store_true",
        help="Save to auto-named /tmp file",
    )
    parser.add_argument(
        "-f", "--file", metavar="FILE",
        help="Read text from file",
    )
    parser.add_argument(
        "-p", "--play", action="store_true", default=None,
        help="Force play (even when saving)",
    )
    parser.add_argument(
        "-n", "--no-play", action="store_true",
        help="Suppress play",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Show generation statistics",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    # Handle -v list
    if args.voice == "list":
        print(format_voices())
        return

    # ── Resolve input text ───────────────────────────────────────────
    text = " ".join(args.text) if args.text else ""

    if not text:
        if args.file:
            p = Path(args.file)
            if not p.is_file():
                print(f"kokoro: file not found: {args.file}", file=sys.stderr)
                sys.exit(1)
            text = p.read_text()
        elif not sys.stdin.isatty():
            text = sys.stdin.read()
        else:
            print(
                "kokoro: no text provided. Usage: kokoro \"text\" or echo text | kokoro (-h for help)",
                file=sys.stderr,
            )
            sys.exit(1)

    text = text.strip()
    if not text:
        print("kokoro: empty text", file=sys.stderr)
        sys.exit(1)

    # ── Short-text workaround ────────────────────────────────────────
    if len(text) < SHORT_TEXT_THRESHOLD:
        text = text + SHORT_TEXT_PAD

    # ── Resolve output / play mode ───────────────────────────────────
    output = args.output
    mp3 = args.mp3

    # Auto-detect .mp3 extension on output file
    if output and output.endswith(".mp3"):
        mp3 = True

    # --mp3 without -o implies --save
    if mp3 and not output:
        args.save = True

    # --save generates auto filename
    if args.save and not output:
        ext = "mp3" if mp3 else "wav"
        from datetime import datetime
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output = f"/tmp/kokoro_{stamp}.{ext}"

    saving = bool(output)

    # Resolve play mode: default play unless saving
    if args.play is None:
        play = not saving
    else:
        play = args.play
    if args.no_play:
        play = False

    # ── Validation ───────────────────────────────────────────────────
    if mp3:
        import shutil
        if not shutil.which("ffmpeg"):
            print("kokoro: ffmpeg required for --mp3. Install: brew install ffmpeg", file=sys.stderr)
            sys.exit(1)

    voice = args.voice
    speed = args.speed

    # ── Execute ──────────────────────────────────────────────────────

    if saving:
        # Generate all audio first (blocks until complete)
        if args.verbose:
            word_count = len(text.split())
            print(f"Generating audio for {word_count} words...", file=sys.stderr)
            t0 = time.time()

        audio = _generate_audio(text, voice, speed)
        if len(audio) == 0:
            print("kokoro: no audio generated", file=sys.stderr)
            sys.exit(1)

        if args.verbose:
            elapsed = time.time() - t0
            duration = len(audio) / SAMPLE_RATE
            print(
                f"Generated {duration:.1f}s of audio in {elapsed:.1f}s "
                f"(RTF: {elapsed/duration:.2f}x)",
                file=sys.stderr,
            )

        # Write WAV
        wav_path = Path(output).with_suffix(".wav") if mp3 else Path(output)
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        from mlx_audio.audio_io import write as audio_write
        audio_write(str(wav_path), audio, SAMPLE_RATE)

        # Convert to MP3 if requested
        if mp3:
            mp3_path = Path(output).with_suffix(".mp3")
            subprocess.run(
                [
                    "ffmpeg", "-y", "-i", str(wav_path),
                    "-codec:a", "libmp3lame", "-b:a", "128k", "-ac", "1",
                    str(mp3_path),
                ],
                capture_output=True,
            )
            wav_path.unlink(missing_ok=True)
            print(f"Saved: {mp3_path}", file=sys.stderr)
        else:
            print(f"Saved: {wav_path}", file=sys.stderr)

        # Play after saving if -p given
        if play:
            _play_audio(audio)

    else:
        # Stream and play (with pause/stop sentinel support)
        if args.verbose:
            word_count = len(text.split())
            print(f"Streaming {word_count} words...", file=sys.stderr)

        # _generate_and_play runs in the calling thread here (not background)
        # but we need it to block until done. Use the same function the MCP
        # server uses, but call it directly (not in a thread).
        _generate_and_play(text, voice, speed)


if __name__ == "__main__":
    main()
