"""Generate an image with no API key, using the local Codex login session.

This script talks to the ChatGPT backend through the same Codex
credentials the sibling scripts use, so there is nothing extra to set up
on a machine where Codex CLI is already logged in:

  - credential file: ``~/.codex/auth.json``, ``tokens.access_token`` plus
    ``tokens.account_id`` (same file as
    transcribe_chatgpt_audio.py:66 ``read_codex_session`` and
    delete_chatgpt_library_files.py:101 ``read_codex_access_token``)
  - headers: ``Authorization: Bearer <token>`` plus
    ``ChatGPT-Account-ID: <account id>`` (same headers as
    transcribe_chatgpt_audio.py:180-189 and
    delete_chatgpt_library_files.py:118-128)
  - errors: 401/403 becomes AuthError, 429 becomes RateLimitError with the
    Retry-After delay (same mapping as
    delete_chatgpt_library_files.py:131-186)

Backend image call, verified live 2026-11-24 (no browser, stdlib only):

  - POST ``https://chatgpt.com/backend-api/codex/responses`` with
    ``stream: true`` (server-sent events); ``input`` must be a list and
    ``stream`` must be true or the backend answers 400.
  - Driving model ``gpt-6.2-sol`` with
    ``tools: [{"type": "image_generation", "size": ..., "quality": ...}]``.
    The backend ran the generation with ``gpt-image-2-codex`` and
    streamed back an ``image_generation_call`` item whose ``result``
    field holds the PNG as base64. This script decodes it and either
    writes the file or reports its byte size and pixel dimensions.
  - The plain ``POST /backend-api/conversation`` path was tried and does
    NOT work headless: it answers 403 bot protection and its sentinel
    handshake demands Turnstile plus device attestation. The plain
    platform API (``POST /v1/images/generations``) rejects the Codex
    OAuth token with missing-scope 401s. The codex/responses path above
    is the one that works.

Examples:
  python generate_image.py "a red lighthouse at dusk" --out light.png --codex-auth
  python generate_image.py "a tabby cat astronaut" --size 1024x1536 --codex-auth
  python generate_image.py "a calm lake" --quality high --out lake.png --codex-auth

Fallback without Codex credentials: set OPENAI_API_KEY and drop
--codex-auth to use the official Images endpoint
(POST /v1/images/generations), or pass --dry-run to preview either
request without calling anything.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import struct
import sys
import time
import uuid
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_URL = "https://chatgpt.com"
CODEX_RESPONSES_PATH = "/backend-api/codex/responses"
CODEX_ORIGINATOR = "codex_cli_rs"
CODEX_USER_AGENT = "codex_cli_rs/0.1.0"
DEFAULT_CODEX_MODEL = "gpt-6.2-sol"
DEFAULT_SIZE = "1024x1024"
SIZES = ("1024x1024", "1536x1024", "1024x1536", "auto")
QUALITIES = ("low", "medium", "high", "auto")
REQUEST_TIMEOUT = 120
IMAGE_TIMEOUT = 600

API_BASE = "https://api.openai.com/v1"
GENERATIONS_PATH = "/images/generations"
DEFAULT_PLATFORM_MODEL = "gpt-image-1.5"


class ImageError(Exception):
    """Base exception for image generation failures."""


class AuthError(ImageError):
    """Raised when the credentials are missing or rejected."""


class RateLimitError(ImageError):
    """Raised when the backend rate-limits requests."""

    def __init__(self, message: str, retry_after: float = 0) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass
class GeneratedImage:
    image_bytes: bytes
    model: str
    width: int
    height: int


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


def discover_access_token(env: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Return a pre-fetched ChatGPT access token when present."""
    env = os.environ if env is None else env
    return env.get("CHATGPT_ACCESS_TOKEN")


def discover_api_key(env: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Return the OpenAI API key from the environment when present."""
    env = os.environ if env is None else env
    return env.get("OPENAI_API_KEY")


def _codex_headers(access_token: str, account_id: Optional[str]) -> Dict[str, str]:
    headers = {
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
        "User-Agent": CODEX_USER_AGENT,
        "Authorization": f"Bearer {access_token}",
        "originator": CODEX_ORIGINATOR,
    }
    if account_id:
        headers["ChatGPT-Account-ID"] = account_id
    return headers


def _retry_after_seconds(error: HTTPError) -> float:
    value = error.headers.get("Retry-After") if error.headers else None
    if not value:
        return 0
    try:
        return max(0.0, min(float(value), 300.0))
    except ValueError:
        pass
    try:
        moment = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return 0
    return max(0.0, min(moment.timestamp() - time.time(), 300.0))


def _codex_detail(error: HTTPError) -> str:
    try:
        raw = error.read().decode("utf-8", "replace")
    except Exception:
        return error.reason or "unknown error"
    try:
        payload = json.loads(raw)
    except ValueError:
        return raw[:300] or error.reason or "unknown error"
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str) and detail:
            return detail[:300]
        inner = payload.get("error")
        if isinstance(inner, dict) and inner.get("message"):
            return str(inner["message"])[:300]
    return error.reason or "unknown error"


def _extract_image_b64(events: List[dict]) -> Tuple[str, str]:
    """Pull the base64 image and serving model out of SSE event payloads."""
    failure: Optional[str] = None
    serving = "codex-image"
    for event in events:
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "response.created":
            response = event.get("response")
            if isinstance(response, dict):
                tools = response.get("tools")
                if isinstance(tools, list):
                    for tool in tools:
                        if (
                            isinstance(tool, dict)
                            and tool.get("type") == "image_generation"
                            and isinstance(tool.get("model"), str)
                            and tool["model"]
                        ):
                            serving = tool["model"]
        if kind == "response.failed":
            response = event.get("response")
            if isinstance(response, dict) and response.get("error"):
                failure = json.dumps(response["error"])[:300]
            else:
                failure = "The backend failed the image request."
        item = event.get("item")
        if isinstance(item, dict):
            event = item
            kind = event.get("type")
        if kind != "image_generation_call":
            continue
        if event.get("status") == "failed":
            failure = "The backend failed the image generation call."
            continue
        result = event.get("result")
        if isinstance(result, str) and result:
            model = event.get("model")
            if not isinstance(model, str) or not model:
                model = serving
            return result, model
    if failure:
        raise ImageError(f"Image generation failed: {failure}")
    raise ImageError("Image generation returned no image data.")


def _decode_image(encoded: object) -> bytes:
    if not isinstance(encoded, str) or not encoded:
        raise ImageError("Image response did not include base64 image data.")
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ImageError("Image response contained invalid image data.") from error


def image_dimensions(image: bytes) -> Tuple[int, int]:
    """Return (width, height) parsed from a PNG or JPEG header, no deps."""
    if image[:8] == b"\x89PNG\r\n\x1a\n" and len(image) >= 24:
        return struct.unpack(">II", image[16:24])
    if image[:2] == b"\xff\xd8":
        pos = 2
        while pos + 9 < len(image):
            if image[pos] != 0xFF:
                break
            marker = image[pos + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3):
                height, width = struct.unpack(">HH", image[pos + 5 : pos + 9])
                return width, height
            if marker in (0xD8, 0xD9, 0x01) or 0xD0 <= marker <= 0xD7:
                pos += 2
                continue
            length = struct.unpack(">H", image[pos + 2 : pos + 4])[0]
            if length < 2:
                break
            pos += 2 + length
    raise ImageError("Could not read image dimensions (not a PNG or JPEG).")


def _read_sse(response) -> List[dict]:
    """Read a streamed SSE body into a list of JSON event payloads."""
    events: List[dict] = []
    buffer = b""
    deadline = time.time() + IMAGE_TIMEOUT
    data_lines: List[str] = []

    def flush() -> None:
        if not data_lines:
            return
        raw = "\n".join(data_lines)
        data_lines.clear()
        if raw.strip() in ("", "[DONE]"):
            return
        try:
            payload = json.loads(raw)
        except ValueError:
            return
        if isinstance(payload, dict):
            events.append(payload)

    while True:
        if time.time() > deadline:
            raise ImageError("Image generation timed out.")
        chunk = response.read(65536)
        if not chunk:
            break
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            text = line.decode("utf-8", "replace").strip()
            if text == "":
                flush()
            elif text.startswith("data:"):
                data_lines.append(text[5:].strip())
    flush()
    return events


def generate_image_codex(
    access_token: str,
    account_id: Optional[str],
    prompt: str,
    model: str = DEFAULT_CODEX_MODEL,
    size: str = DEFAULT_SIZE,
    quality: Optional[str] = None,
) -> GeneratedImage:
    """Generate one image through the ChatGPT backend and decode it."""
    if not prompt.strip():
        raise ImageError("Prompt must not be empty.")
    if size not in SIZES:
        raise ImageError(f"Size must be one of {', '.join(SIZES)}.")
    if quality is not None and quality not in QUALITIES:
        raise ImageError(f"Quality must be one of {', '.join(QUALITIES)}.")
    tool: Dict[str, object] = {"type": "image_generation", "size": size}
    if quality:
        tool["quality"] = quality
    body = {
        "model": model,
        "instructions": "You are an image generator. "
        "When asked for an image, call the image_generation tool.",
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": prompt}],
            }
        ],
        "tools": [tool],
        "stream": True,
        "store": False,
    }
    request = Request(
        f"{BASE_URL}{CODEX_RESPONSES_PATH}",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers=_codex_headers(access_token, account_id),
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            events = _read_sse(response)
    except HTTPError as error:
        if error.code in (401, 403):
            raise AuthError(
                "ChatGPT rejected the session (401/403). "
                "Log in again so the Codex session refreshes. "
                "On a server or VPN IP this can also be ChatGPT bot "
                "protection; run the script from your own machine instead."
            ) from error
        if error.code == 429:
            raise RateLimitError(
                "ChatGPT rate-limited the request. Wait and try again.",
                _retry_after_seconds(error),
            ) from error
        raise ImageError(
            f"ChatGPT POST {CODEX_RESPONSES_PATH} failed "
            f"({error.code}): {_codex_detail(error)}"
        ) from error
    except URLError as error:
        raise ImageError(f"Request to {CODEX_RESPONSES_PATH} failed: {error}") from error
    except TimeoutError as error:
        raise ImageError(
            f"ChatGPT POST {CODEX_RESPONSES_PATH} timed out."
        ) from error
    encoded, serving_model = _extract_image_b64(events)
    image_bytes = _decode_image(encoded)
    width, height = image_dimensions(image_bytes)
    return GeneratedImage(
        image_bytes=image_bytes, model=serving_model, width=width, height=height
    )


def _platform_request(
    method: str,
    path: str,
    api_key: str,
    body: Optional[Dict[str, object]] = None,
    retries: int = 3,
) -> Dict[str, object]:
    url = f"{API_BASE}{path}"
    data = json.dumps(body or {}).encode("utf-8") if body is not None else None
    request = Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    attempt = 0
    while True:
        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                payload = json.load(response)
            if not isinstance(payload, dict):
                raise ImageError(f"Unexpected response from {url}.")
            return payload
        except HTTPError as error:
            if error.code == 401:
                raise AuthError(
                    "OpenAI rejected the API key (401). "
                    "Check OPENAI_API_KEY and try again."
                ) from error
            if error.code == 429 and attempt < retries:
                wait = 2**attempt
                time.sleep(wait)
                attempt += 1
                continue
            detail = _platform_error_detail(error)
            raise ImageError(
                f"OpenAI API {method} {path} failed ({error.code}): {detail}"
            ) from error
        except URLError as error:
            raise ImageError(f"Request to {url} failed: {error}") from error


def _platform_error_detail(error: HTTPError) -> str:
    try:
        payload = json.load(error)
    except (ValueError, OSError):
        return error.reason or "unknown error"
    if isinstance(payload, dict):
        inner = payload.get("error")
        if isinstance(inner, dict) and inner.get("message"):
            return str(inner["message"])
    return error.reason or "unknown error"


def generate_image_platform(
    api_key: str,
    prompt: str,
    model: str = DEFAULT_PLATFORM_MODEL,
    size: str = DEFAULT_SIZE,
    quality: Optional[str] = None,
) -> GeneratedImage:
    """Generate one image through the official Images endpoint."""
    if not prompt.strip():
        raise ImageError("Prompt must not be empty.")
    body: Dict[str, object] = {"model": model, "prompt": prompt, "size": size}
    if quality:
        body["quality"] = quality
    payload = _platform_request("POST", GENERATIONS_PATH, api_key, body)
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        raise ImageError("Image generation returned no images.")
    first = data[0]
    if not isinstance(first, dict):
        raise ImageError("Image generation returned an invalid image.")
    image_bytes = _decode_image(first.get("b64_json"))
    width, height = image_dimensions(image_bytes)
    return GeneratedImage(
        image_bytes=image_bytes, model=model, width=width, height=height
    )


def resolve_codex_credentials(
    args: argparse.Namespace,
) -> Tuple[str, Optional[str]]:
    token = args.access_token or discover_access_token()
    account = args.account_id or os.environ.get("CHATGPT_ACCOUNT_ID")
    if token:
        return token, account
    if args.codex_auth:
        return read_codex_session()
    raise AuthError(
        "No ChatGPT credentials. Pass --codex-auth, or set "
        "CHATGPT_ACCESS_TOKEN (see the module docstring)."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate an image with Codex/ChatGPT credentials."
    )
    parser.add_argument("prompt", help="Text description of the image to generate.")
    parser.add_argument(
        "--model",
        default=None,
        help="Codex chat model driving generation "
        f"(default: {DEFAULT_CODEX_MODEL}); with --platform-key, the image "
        f"model (default: {DEFAULT_PLATFORM_MODEL}).",
    )
    parser.add_argument(
        "--size",
        default=DEFAULT_SIZE,
        help=f"Image size, one of {', '.join(SIZES)} (default: {DEFAULT_SIZE}).",
    )
    parser.add_argument(
        "--quality",
        default=None,
        help=f"Image quality, one of {', '.join(QUALITIES)} (default: API default).",
    )
    parser.add_argument(
        "--out",
        default=None,
        metavar="PATH",
        help="Write the image bytes to this file.",
    )
    parser.add_argument(
        "--codex-auth",
        action="store_true",
        help="Reuse the local Codex login session (same as the sibling scripts).",
    )
    parser.add_argument(
        "--access-token",
        default=None,
        help="ChatGPT access token (or set CHATGPT_ACCESS_TOKEN).",
    )
    parser.add_argument(
        "--account-id",
        default=None,
        help="ChatGPT account ID (or set CHATGPT_ACCOUNT_ID).",
    )
    parser.add_argument(
        "--platform-key",
        action="store_true",
        help="Use OPENAI_API_KEY with the official Images endpoint instead.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the request that would be sent without calling the API.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    use_platform = args.platform_key or (
        not args.codex_auth and not args.access_token and discover_access_token() is None
    )
    model = args.model or (
        DEFAULT_PLATFORM_MODEL if use_platform else DEFAULT_CODEX_MODEL
    )

    if args.dry_run:
        if use_platform:
            preview: Dict[str, object] = {
                "model": model,
                "prompt": args.prompt,
                "size": args.size,
            }
            if args.quality:
                preview["quality"] = args.quality
            print(f"POST {API_BASE}{GENERATIONS_PATH}")
            print(json.dumps(preview, indent=2))
        else:
            print(f"POST {BASE_URL}{CODEX_RESPONSES_PATH} (streaming SSE)")
            print(
                json.dumps(
                    {
                        "model": model,
                        "input": [{"role": "user", "content": args.prompt}],
                        "tools": [
                            {
                                "type": "image_generation",
                                "size": args.size,
                                **(
                                    {"quality": args.quality} if args.quality else {}
                                ),
                            }
                        ],
                    },
                    indent=2,
                )
            )
        print("Dry run. Nothing was generated.")
        return 0

    try:
        if use_platform:
            api_key = discover_api_key()
            if not api_key:
                print("error: OPENAI_API_KEY is not set.", file=sys.stderr)
                return 2
            image = generate_image_platform(
                api_key,
                args.prompt,
                model=model,
                size=args.size,
                quality=args.quality,
            )
        else:
            access_token, account_id = resolve_codex_credentials(args)
            image = generate_image_codex(
                access_token,
                account_id,
                args.prompt,
                model=model,
                size=args.size,
                quality=args.quality,
            )
    except ImageError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if args.out:
        try:
            with open(args.out, "wb") as handle:
                handle.write(image.image_bytes)
        except OSError as error:
            print(f"error: cannot write {args.out}: {error}", file=sys.stderr)
            return 1
        print(
            f"Saved {len(image.image_bytes)} bytes "
            f"({image.width}x{image.height}) to {args.out} ({image.model})."
        )
    else:
        print(
            f"Generated {len(image.image_bytes)} bytes "
            f"({image.width}x{image.height}, {image.model})."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
