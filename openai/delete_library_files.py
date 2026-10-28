"""
Delete files stored in your OpenAI account through the Files API.

This script talks to the official OpenAI Files API, which covers files
uploaded through the API, Codex, Assistants, and other platform tools:

  - list:   GET    /v1/files (paginated with the after cursor)
  - delete: DELETE /v1/files/{file_id}

Deleting a file here also removes it from all vector stores.

It does not cover the ChatGPT consumer Library (the Library sidebar on
chatgpt.com and in the Android app), which has no public API. Delete those
files by hand: open Library, select the file, then Delete (trash icon).
Deleting a chat does not delete its Library files, so remove each one
separately. Older generated images may only disappear after deleting the
chat that created them.

Setup:
  export OPENAI_API_KEY="sk-..."

Examples:
  python delete_library_files.py --list
  python delete_library_files.py --list --purpose assistants
  python delete_library_files.py --delete file-abc123 file-def456
  python delete_library_files.py --all --dry-run
  python delete_library_files.py --all --yes
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_BASE = "https://api.openai.com/v1"
PAGE_LIMIT = 100


class OpenAIAPIError(Exception):
    """Base exception for OpenAI API failures."""


class AuthError(OpenAIAPIError):
    """Raised when the API key is missing or rejected."""


@dataclass
class StoredFile:
    file_id: str
    filename: str
    purpose: str
    size_bytes: int
    created_at: int


def discover_api_key(env: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Return the OpenAI API key from the environment when present."""
    env = os.environ if env is None else env
    return env.get("OPENAI_API_KEY")


def _request(
    method: str, path: str, api_key: str, retries: int = 3
) -> Dict[str, object]:
    url = f"{API_BASE}{path}"
    request = Request(
        url,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    attempt = 0
    while True:
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
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


def _to_stored_file(payload: Dict[str, object]) -> StoredFile:
    return StoredFile(
        file_id=str(payload.get("id", "")),
        filename=str(payload.get("filename", "")),
        purpose=str(payload.get("purpose", "")),
        size_bytes=int(payload.get("bytes", 0) or 0),
        created_at=int(payload.get("created_at", 0) or 0),
    )


def list_files(api_key: str, purpose: Optional[str] = None) -> List[StoredFile]:
    """Return every file on the account, optionally filtered by purpose."""
    files: List[StoredFile] = []
    after: Optional[str] = None
    while True:
        params = {"limit": PAGE_LIMIT, "order": "desc"}
        if purpose:
            params["purpose"] = purpose
        if after:
            params["after"] = after
        payload = _request("GET", f"/files?{urlencode(params)}", api_key)
        data = payload.get("data")
        if not isinstance(data, list):
            raise OpenAIAPIError("Unexpected list response from OpenAI.")
        for item in data:
            if isinstance(item, dict):
                files.append(_to_stored_file(item))
        if not payload.get("has_more") or not data:
            return files
        last = data[-1]
        after = str(last.get("id")) if isinstance(last, dict) else None
        if not after:
            return files


def delete_file(api_key: str, file_id: str) -> bool:
    """Delete one file. Returns True when the API confirms deletion."""
    payload = _request("DELETE", f"/files/{file_id}", api_key)
    return payload.get("deleted") is True and payload.get("id") == file_id


def format_files(files: Iterable[StoredFile]) -> str:
    lines = ["id\tpurpose\tbytes\tfilename"]
    for item in files:
        lines.append(
            f"{item.file_id}\t{item.purpose}\t{item.size_bytes}\t{item.filename}"
        )
    return "\n".join(lines)


def confirm(prompt: str) -> bool:
    answer = input(f"{prompt} [y/N] ").strip().lower()
    return answer in ("y", "yes")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="List and delete files stored in your OpenAI account."
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List files instead of deleting them.",
    )
    parser.add_argument(
        "--purpose",
        default=None,
        help="Only include files with this purpose (for example: assistants).",
    )
    parser.add_argument(
        "--delete",
        nargs="+",
        metavar="FILE_ID",
        default=[],
        help="Delete the given file IDs.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Delete every listed file (combine with --purpose to narrow it).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without deleting anything.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    api_key = discover_api_key()
    if not api_key:
        print("error: OPENAI_API_KEY is not set.", file=sys.stderr)
        return 2

    if not args.list and not args.delete and not args.all:
        print("error: pass --list, --delete FILE_ID, or --all.", file=sys.stderr)
        return 2

    try:
        if args.list and not args.delete and not args.all:
            files = list_files(api_key, purpose=args.purpose)
            print(f"{len(files)} file(s)")
            if files:
                print(format_files(files))
            return 0

        targets: List[StoredFile] = []
        if args.all:
            targets = list_files(api_key, purpose=args.purpose)
            if not targets:
                print("No files match. Nothing to delete.")
                return 0
        else:
            known = {item.file_id: item for item in list_files(api_key)}
            missing = [fid for fid in args.delete if fid not in known]
            if missing:
                print(
                    f"error: unknown file ID(s): {', '.join(missing)}",
                    file=sys.stderr,
                )
                return 1
            targets = [known[fid] for fid in args.delete]

        print(f"{len(targets)} file(s) selected:")
        print(format_files(targets))
        if args.dry_run:
            print("Dry run. Nothing was deleted.")
            return 0
        if not args.yes and not confirm("Delete these files?"):
            print("Aborted. Nothing was deleted.")
            return 0

        deleted = 0
        failed: List[str] = []
        for item in targets:
            try:
                if delete_file(api_key, item.file_id):
                    deleted += 1
                    print(f"deleted {item.file_id}")
                else:
                    failed.append(item.file_id)
            except OpenAIAPIError as error:
                print(f"failed {item.file_id}: {error}", file=sys.stderr)
                failed.append(item.file_id)

        print(f"Done. Deleted {deleted}, failed {len(failed)}.")
        return 1 if failed else 0
    except OpenAIAPIError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
