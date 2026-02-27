#!/usr/bin/env python3
"""
kokoro-clipboard — Speak macOS clipboard contents via kokoro.

Behavior:
- If clipboard has speakable text, optionally extracts [kokoro]...[/kokoro],
  strips markdown, and speaks the result.
- If clipboard is non-text, speaks a short message describing the clipboard
  type (image/PDF/file/etc.), unless --silent-nontext is set.
"""

import argparse
import html
import re
import shutil
import subprocess
import sys


def run_capture(cmd: list[str]) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return p.returncode, p.stdout or "", p.stderr or ""
    except Exception as exc:  # pragma: no cover
        return 1, "", str(exc)


def get_clipboard_info() -> str:
    _, out, _ = run_capture(["osascript", "-e", "clipboard info"])
    return out.strip()


def get_clipboard_text() -> str:
    # Prefer plain text when available.
    for cmd in (["pbpaste", "-Prefer", "txt"], ["pbpaste"]):
        code, out, _ = run_capture(cmd)
        if code == 0 and out:
            return out
    return ""


def classify_nontext(info: str) -> str:
    lower = info.lower()
    if any(t in lower for t in ("png", "tiff", "jpeg", "gif", "heic", "8bps")):
        return "The clipboard contains an image, not text."
    if "pdf" in lower:
        return "The clipboard contains a PDF, not plain text."
    if any(t in lower for t in ("file", "furl", "alis", "public.file-url")):
        return "The clipboard contains a file reference, not text."
    if any(t in lower for t in ("url", "public.url")):
        return "The clipboard contains a URL, not plain text."
    if info.strip():
        return "The clipboard contains non-text data."
    return "The clipboard is empty."


def extract_kokoro_block(text: str) -> str:
    match = re.search(r"\[kokoro\]\s*(.*?)\s*\[/kokoro\]", text, re.IGNORECASE | re.DOTALL)
    return match.group(1) if match else text


def strip_markdown_for_tts(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = html.unescape(text)

    # Keep code content but drop markdown fence syntax.
    text = re.sub(r"```[^\n]*\n(.*?)```", r"\nCode snippet:\n\1\n", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"__(.*?)__", r"\1", text)
    text = re.sub(r"\*(.*?)\*", r"\1", text)
    text = re.sub(r"_(.*?)_", r"\1", text)
    text = re.sub(r"~~(.*?)~~", r"\1", text)

    # Links/images.
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", lambda m: m.group(1) or "image", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"<(https?://[^>]+)>", r"\1", text)

    # Headings/quotes/hr.
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s{0,3}>\s?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*([-*_]\s*){3,}\s*$", "", text, flags=re.MULTILINE)

    # List markers.
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+[.)]\s+", "", text, flags=re.MULTILINE)

    # Convert simple markdown table rows into comma-separated text.
    converted = []
    for line in text.split("\n"):
        if "|" in line and line.count("|") >= 2:
            cells = [cell.strip() for cell in line.split("|") if cell.strip()]
            # Skip separator-only rows: --- | :---:
            if cells and all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
                continue
            if cells:
                converted.append(", ".join(cells))
                continue
        converted.append(line)
    text = "\n".join(converted)

    # Remove common markdown artifacts.
    text = re.sub(r"\[\^.+?\]:.*$", "", text, flags=re.MULTILINE)  # footnotes

    parts: list[str] = []
    for raw in text.split("\n"):
        line = re.sub(r"\s+", " ", raw).strip()
        if not line:
            continue
        if line[-1] not in ".!?;:":
            line = f"{line}."
        parts.append(line)

    out = " ".join(parts)
    out = re.sub(r"\s*([,.!?;:])\s*", r"\1 ", out)
    out = re.sub(r"\s{2,}", " ", out)
    return out.strip()


def build_kokoro_command(cmd: str, voice: str, speed: float) -> list[str]:
    if cmd.startswith("/"):
        kokoro_bin = cmd
    else:
        kokoro_bin = shutil.which(cmd) or cmd
    return [kokoro_bin, "-v", voice, "-s", str(speed)]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="kokoro-clipboard.py",
        description="Speak the current macOS clipboard with markdown cleanup.",
    )
    p.add_argument("-v", "--voice", default="af_heart", help="Kokoro voice")
    p.add_argument("-s", "--speed", type=float, default=1.0, help="Speech speed multiplier")
    p.add_argument("--kokoro-cmd", default="kokoro", help="Kokoro command (default: kokoro)")
    p.add_argument("--raw", action="store_true", help="Do not strip markdown")
    p.add_argument("--silent-nontext", action="store_true", help="Do not speak non-text clipboard")
    p.add_argument("--max-chars", type=int, default=20000, help="Character cap before truncation")
    p.add_argument("--dry-run", action="store_true", help="Print final text without speaking")
    p.add_argument("--text", help="Use this text instead of reading the clipboard")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    info = ""
    text = args.text if args.text is not None else ""
    if args.text is None:
        info = get_clipboard_info()
        text = get_clipboard_text()

    if text.strip():
        speak_text = extract_kokoro_block(text).strip()
        if not args.raw:
            speak_text = strip_markdown_for_tts(speak_text)
        if not speak_text:
            speak_text = "The clipboard text was empty after cleanup."
    else:
        speak_text = classify_nontext(info)
        if args.silent_nontext:
            print(speak_text, file=sys.stderr)
            return 1

    if len(speak_text) > args.max_chars:
        speak_text = speak_text[: args.max_chars].rstrip() + " ..."

    if args.dry_run:
        print(speak_text)
        return 0

    cmd = build_kokoro_command(args.kokoro_cmd, args.voice, args.speed)
    try:
        p = subprocess.run(cmd, input=speak_text, text=True, check=False)
        return p.returncode
    except FileNotFoundError:
        print(f"kokoro-clipboard: command not found: {cmd[0]}", file=sys.stderr)
        return 127


if __name__ == "__main__":
    raise SystemExit(main())
