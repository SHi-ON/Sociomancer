"""
Transcribe an audio file with the ChatGPT web client's batch endpoint.

The ChatGPT web app ships two transcription paths (found in its JS
bundles, module EJ6P / composer):

  - streaming: WS /dictation/stream (mic dictation, PCM16 frames)
  - batch:     POST /backend-api/transcribe (one audio file in, text out)

This script uses the batch path: multipart audio upload, JSON transcript
back. It uses only the standard library and the same credentials as
delete_chatgpt_library_files.py.

Setup (same login session the web app would use):
  1. Log in to chatgpt.com in your browser.
  2. Open devtools, Network tab, click any chatgpt.com request.
  3. Copy the Cookie request header value.
  4. export CHATGPT_COOKIES='<pasted cookies>'

  The script exchanges the cookies for a short-lived access token on every
  run, so raw cookies never leave your machine. Alternatively, export
  CHATGPT_ACCESS_TOKEN directly with a token from /api/auth/session.
  On a machine where Codex CLI is logged in, pass --codex-auth instead
  to reuse that session with no extra setup.

Examples:
  python transcribe_chatgpt_audio.py voice-memo.m4a --codex-auth
  python transcribe_chatgpt_audio.py interview.mp3 --language en
  python transcribe_chatgpt_audio.py clip.wav --json --codex-auth
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import uuid
from typing import Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_URL = "https://chatgpt.com"
SESSION_PATH = "/api/auth/session"
TRANSCRIBE_PATH = "/backend-api/transcribe"
REQUEST_TIMEOUT = 120

AUDIO_MIME_OVERRIDES = {
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".opus": "audio/opus",
    ".oga": "audio/ogg",
    ".weba": "audio/webm",
}


class TranscribeError(Exception):
    """Base exception for transcription failures."""


class AuthError(TranscribeError):
    """Raised when the ChatGPT session is missing or expired."""


def read_codex_session() -> Tuple[str, Optional[str]]:
    """Read access token and account ID from a local Codex login session."""
    candidate = os.path.expanduser("~/.codex/auth.json")
    try:
        with open(candidate) as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as error:
        raise AuthError(
            f"Could not read the Codex session at {candidate}: {error}"
        ) from error
    tokens = payload.get("tokens") if isinstance(payload, dict) else None
    token = tokens.get("access_token") if isinstance(tokens, dict) else None
    account = tokens.get("account_id") if isinstance(tokens, dict) else None
    if not token or not isinstance(token, str):
        raise AuthError(f"No access token in the Codex session at {candidate}.")
    return token, str(account) if account else None


def fetch_session(
    cookies: str, timeout: int = 45
) -> Tuple[str, Optional[str]]:
    """Exchange login cookies for a short-lived access token."""
    request = Request(
        f"{BASE_URL}{SESSION_PATH}",
        method="GET",
        headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0",
            "Cookie": cookies,
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except HTTPError as error:
        if error.code in (401, 403):
            raise AuthError(
                "ChatGPT rejected the session (401/403). "
                "Log in again and refresh CHATGPT_COOKIES."
            ) from error
        raise TranscribeError(
            f"ChatGPT session exchange failed ({error.code})."
        ) from error
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        raise TranscribeError(f"ChatGPT session exchange failed: {error}") from error
    if not isinstance(payload, dict):
        raise AuthError("ChatGPT session response was not JSON.")
    token = payload.get("accessToken")
    if not token or not isinstance(token, str):
        raise AuthError(
            "No access token in the ChatGPT session. "
            "Log in again and refresh CHATGPT_COOKIES."
        )
    user = payload.get("user")
    account = user.get("id") if isinstance(user, dict) else None
    return token, str(account) if account else None


def guess_content_type(path: str) -> str:
    """Guess the audio MIME type the way the web client would."""
    ext = os.path.splitext(path)[1].lower()
    if ext in AUDIO_MIME_OVERRIDES:
        return AUDIO_MIME_OVERRIDES[ext]
    guessed, _ = mimetypes.guess_type(path)
    if guessed and guessed.startswith("audio/"):
        return guessed
    return "audio/webm"


def build_multipart(
    audio: bytes, filename: str, content_type: str, language: Optional[str]
) -> Tuple[bytes, str]:
    """Build the multipart body the web client sends to /transcribe."""
    boundary = f"----chatgpt-transcribe-{uuid.uuid4().hex}"
    clean_name = filename.replace('"', "")
    parts: List[bytes] = [
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; '
        f'filename="{clean_name}"\r\n'.encode(),
        f"Content-Type: {content_type}\r\n\r\n".encode(),
        audio,
        b"\r\n",
    ]
    if language:
        parts.extend(
            [
                f"--{boundary}\r\n".encode(),
                b'Content-Disposition: form-data; name="language"\r\n\r\n',
                f"{language}\r\n".encode(),
            ]
        )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), boundary


def transcribe(
    audio_path: str,
    access_token: str,
    account_id: Optional[str],
    language: str = "en",
    timeout: int = REQUEST_TIMEOUT,
) -> Dict[str, object]:
    """POST one audio file to /backend-api/transcribe and return the payload."""
    try:
        with open(audio_path, "rb") as handle:
            audio = handle.read()
    except OSError as error:
        raise TranscribeError(f"Could not read {audio_path}: {error}") from error
    if not audio:
        raise TranscribeError(f"{audio_path} is empty; nothing to transcribe.")
    filename = os.path.basename(audio_path) or "audio.webm"
    body, boundary = build_multipart(
        audio, filename, guess_content_type(audio_path), language or None
    )
    headers = {
        "Accept": "application/json",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "User-Agent": "Mozilla/5.0",
        "Authorization": f"Bearer {access_token}",
    }
    if account_id:
        headers["ChatGPT-Account-ID"] = account_id
    request = Request(
        f"{BASE_URL}{TRANSCRIBE_PATH}", data=body, method="POST", headers=headers
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except HTTPError as error:
        detail = ""
        try:
            detail = error.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        if error.code in (401, 403):
            raise AuthError(
                "ChatGPT rejected the session (401/403). "
                "Log in again and refresh the credentials. "
                "On a server or VPN IP this can also be ChatGPT bot "
                "protection; run the script from your own machine instead."
            ) from error
        if error.code == 429:
            raise TranscribeError(
                "ChatGPT rate-limited the request. Wait and try again."
            ) from error
        if error.code == 413 or "file_above_max_size" in detail:
            raise TranscribeError(
                "ChatGPT rejected the audio as too large. "
                "Send a shorter clip."
            ) from error
        if "audio_too_short" in detail:
            raise TranscribeError(
                "ChatGPT rejected the audio as too short."
            ) from error
        raise TranscribeError(
            f"ChatGPT POST {TRANSCRIBE_PATH} failed ({error.code}). {detail}"
        ) from error
    except URLError as error:
        raise TranscribeError(f"Request to {TRANSCRIBE_PATH} failed: {error}") from error
    except TimeoutError as error:
        raise TranscribeError(
            f"ChatGPT POST {TRANSCRIBE_PATH} timed out."
        ) from error
    except json.JSONDecodeError as error:
        raise TranscribeError(
            f"ChatGPT POST {TRANSCRIBE_PATH} returned an invalid response."
        ) from error
    if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
        raise TranscribeError(
            f"ChatGPT POST {TRANSCRIBE_PATH} returned an unexpected shape."
        )
    return payload


def resolve_credentials(args: argparse.Namespace) -> Tuple[str, Optional[str]]:
    token = args.access_token or os.environ.get("CHATGPT_ACCESS_TOKEN")
    cookies = args.cookies or os.environ.get("CHATGPT_COOKIES")
    if token:
        return token, None
    if args.codex_auth:
        return read_codex_session()
    if not cookies:
        raise AuthError(
            "No ChatGPT credentials. Pass --codex-auth, or set "
            "CHATGPT_COOKIES or CHATGPT_ACCESS_TOKEN "
            "(see the module docstring)."
        )
    return fetch_session(cookies)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Transcribe an audio file with ChatGPT's web endpoint."
    )
    parser.add_argument("audio", help="Path to the audio file to transcribe.")
    parser.add_argument(
        "--language",
        default="en",
        help='Spoken language hint sent with the upload (default: "en").',
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full JSON response instead of just the text.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=REQUEST_TIMEOUT,
        help=f"Request timeout in seconds (default: {REQUEST_TIMEOUT}).",
    )
    parser.add_argument(
        "--cookies",
        default=None,
        help="ChatGPT login cookies (overrides CHATGPT_COOKIES).",
    )
    parser.add_argument(
        "--access-token",
        default=None,
        help="ChatGPT access token (overrides CHATGPT_ACCESS_TOKEN).",
    )
    parser.add_argument(
        "--codex-auth",
        action="store_true",
        help="Use the Codex login session on this machine.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        access_token, account_id = resolve_credentials(args)
        payload = transcribe(
            args.audio, access_token, account_id, args.language, args.timeout
        )
    except TranscribeError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(str(payload.get("text", "")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
