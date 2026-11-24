"""Generate images through the official OpenAI Images API.

This script talks to the official Images endpoint, the same API family as
the Files scripts in this folder:

  - generate: POST /v1/images/generations

The request sends a JSON body with a model and a prompt. GPT image models
always return base64-encoded image bytes (the legacy URL response format
is not supported for them), so this script decodes the first image and
either writes it to a file or reports its size.

Endpoint and parameters verified against the official API reference
("Create image", POST /images/generations): model must be given
explicitly, and gpt-image-1 was shut down on October 23, 2026, so the
default here is gpt-image-1.5. Quality defaults to auto and size defaults
to 1024x1024 to keep output predictable.

Setup:
  export OPENAI_API_KEY="sk-..."

Examples:
  python generate_image.py "a red lighthouse at dusk" --out lighthouse.png
  python generate_image.py "a tabby cat astronaut" --size 1024x1536 --out cat.png
  python generate_image.py "a calm lake" --model gpt-image-1-mini --dry-run
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_BASE = "https://api.openai.com/v1"
GENERATIONS_PATH = "/images/generations"
DEFAULT_MODEL = "gpt-image-1.5"
DEFAULT_SIZE = "1024x1024"
REQUEST_TIMEOUT = 120


class OpenAIAPIError(Exception):
    """Base exception for OpenAI API failures."""


class AuthError(OpenAIAPIError):
    """Raised when the API key is missing or rejected."""


@dataclass
class GeneratedImage:
    image_bytes: bytes
    model: str


def discover_api_key(env: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Return the OpenAI API key from the environment when present."""
    env = os.environ if env is None else env
    return env.get("OPENAI_API_KEY")


def _request(
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
                raise OpenAIAPIError(f"Unexpected response from {url}.")
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
            detail = _error_detail(error)
            raise OpenAIAPIError(
                f"OpenAI API {method} {path} failed ({error.code}): {detail}"
            ) from error
        except URLError as error:
            raise OpenAIAPIError(f"Request to {url} failed: {error}") from error


def _error_detail(error: HTTPError) -> str:
    try:
        payload = json.load(error)
    except (ValueError, OSError):
        return error.reason or "unknown error"
    if isinstance(payload, dict):
        inner = payload.get("error")
        if isinstance(inner, dict) and inner.get("message"):
            return str(inner["message"])
    return error.reason or "unknown error"


def _decode_image(item: Dict[str, object]) -> bytes:
    encoded = item.get("b64_json")
    if not isinstance(encoded, str) or not encoded:
        raise OpenAIAPIError(
            "Image response did not include base64 image data. "
            "GPT image models return b64_json; URL output is not supported."
        )
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise OpenAIAPIError("Image response contained invalid image data.") from error


def generate_image(
    api_key: str,
    prompt: str,
    model: str = DEFAULT_MODEL,
    size: str = DEFAULT_SIZE,
    quality: Optional[str] = None,
) -> GeneratedImage:
    """Generate one image and return its decoded bytes."""
    if not prompt.strip():
        raise OpenAIAPIError("Prompt must not be empty.")
    body: Dict[str, object] = {"model": model, "prompt": prompt, "size": size}
    if quality:
        body["quality"] = quality
    payload = _request("POST", GENERATIONS_PATH, api_key, body)
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        raise OpenAIAPIError("Image generation returned no images.")
    first = data[0]
    if not isinstance(first, dict):
        raise OpenAIAPIError("Image generation returned an invalid image.")
    return GeneratedImage(image_bytes=_decode_image(first), model=model)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate an image through the OpenAI Images API."
    )
    parser.add_argument("prompt", help="Text description of the image to generate.")
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Image model to use (default: {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--size",
        default=DEFAULT_SIZE,
        help=f"Image size, for example 1024x1024 (default: {DEFAULT_SIZE}).",
    )
    parser.add_argument(
        "--quality",
        default=None,
        help="Image quality: auto, low, medium, or high (default: API default).",
    )
    parser.add_argument(
        "--out",
        default=None,
        metavar="PATH",
        help="Write the image bytes to this file.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the request that would be sent without calling the API.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.dry_run:
        preview = {
            "model": args.model,
            "prompt": args.prompt,
            "size": args.size,
        }
        if args.quality:
            preview["quality"] = args.quality
        print(f"POST {API_BASE}{GENERATIONS_PATH}")
        print(json.dumps(preview, indent=2))
        print("Dry run. Nothing was generated.")
        return 0

    api_key = discover_api_key()
    if not api_key:
        print("error: OPENAI_API_KEY is not set.", file=sys.stderr)
        return 2

    try:
        image = generate_image(
            api_key,
            args.prompt,
            model=args.model,
            size=args.size,
            quality=args.quality,
        )
    except OpenAIAPIError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if args.out:
        try:
            with open(args.out, "wb") as handle:
                handle.write(image.image_bytes)
        except OSError as error:
            print(f"error: cannot write {args.out}: {error}", file=sys.stderr)
            return 1
        print(f"Saved {len(image.image_bytes)} bytes to {args.out} ({image.model}).")
    else:
        print(f"Generated {len(image.image_bytes)} bytes ({image.model}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
