"""
List and bulk-delete files in the ChatGPT consumer Library.

This script uses the same credentials and undocumented backend endpoints as
the ChatGPT-bulk-delete-chats browser extension by johnvouros, reimplemented
here as a standalone script:

  - session: GET    /api/auth/session (ChatGPT login cookies in, access token out)
  - list:    GET    /backend-api/files/library/nodes (cursor paged)
  - delete:  DELETE /backend-api/files/library/files/{id}?soft_delete=true

Endpoint discovery credit:
https://github.com/johnvouros/ChatGPT-bulk-delete-chats

Setup (same login session the extension would use):
  1. Log in to chatgpt.com in your browser.
  2. Open devtools, Network tab, click any chatgpt.com request.
  3. Copy the Cookie request header value.
  4. export CHATGPT_COOKIES='<pasted cookies>'

  The script exchanges the cookies for a short-lived access token on every
  run, so raw cookies never leave your machine. Alternatively, export
  CHATGPT_ACCESS_TOKEN directly with a token from /api/auth/session.

Examples:
  python delete_chatgpt_library_files.py --list
  python delete_chatgpt_library_files.py --list --name invoice
  python delete_chatgpt_library_files.py --delete libfile_abc123 --yes
  python delete_chatgpt_library_files.py --all --dry-run
  python delete_chatgpt_library_files.py --all --yes
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

BASE_URL = "https://chatgpt.com"
SESSION_PATH = "/api/auth/session"
NODES_PATH = "/backend-api/files/library/nodes"
DELETE_TIMEOUT = 30
REQUEST_TIMEOUT = 45
PAGE_DELAY = 0.12
DELETE_SPACING = 1.2


class ChatGPTAPIError(Exception):
    """Base exception for ChatGPT backend failures."""


class AuthError(ChatGPTAPIError):
    """Raised when the ChatGPT session is missing or expired."""


class RateLimitError(ChatGPTAPIError):
    """Raised when ChatGPT rate-limits requests."""

    def __init__(self, message: str, retry_after: float = 0) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass
class LibraryFile:
    library_file_id: str
    file_id: str
    name: str
    created_at: Optional[str]
    parent_directory_id: Optional[str]
    mime_type: Optional[str]


def discover_cookies(env: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Return pasted ChatGPT cookies from the environment when present."""
    env = os.environ if env is None else env
    return env.get("CHATGPT_COOKIES")


def discover_access_token(env: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Return a pre-fetched ChatGPT access token when present."""
    env = os.environ if env is None else env
    return env.get("CHATGPT_ACCESS_TOKEN")


def _headers(access_token: Optional[str], cookies: Optional[str]) -> Dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    if cookies:
        headers["Cookie"] = cookies
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


def _request(
    method: str,
    path: str,
    access_token: Optional[str] = None,
    cookies: Optional[str] = None,
    timeout: int = REQUEST_TIMEOUT,
) -> object:
    request = Request(
        f"{BASE_URL}{path}",
        method=method,
        headers=_headers(access_token, cookies),
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as error:
        if error.code == 429:
            raise RateLimitError(
                "ChatGPT rate-limited the request.",
                _retry_after_seconds(error),
            ) from error
        if error.code in (401, 403):
            raise AuthError(
                "ChatGPT rejected the session (401/403). "
                "Log in again and refresh CHATGPT_COOKIES. "
                "On a server or VPN IP this can also be ChatGPT bot "
                "protection; run the script from your own machine instead."
            ) from error
        raise ChatGPTAPIError(
            f"ChatGPT {method} {path} failed ({error.code})."
        ) from error
    except URLError as error:
        raise ChatGPTAPIError(f"Request to {path} failed: {error}") from error


def fetch_access_token(cookies: str) -> Tuple[str, Optional[str]]:
    """Exchange login cookies for a short-lived access token."""
    payload = _request("GET", SESSION_PATH, cookies=cookies)
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


def _to_library_file(payload: Dict[str, object]) -> Optional[LibraryFile]:
    library_file_id = payload.get("library_file_id")
    file_id = payload.get("file_id")
    name = payload.get("file_name")
    if not library_file_id or not file_id or not name:
        return None
    if not isinstance(library_file_id, str):
        return None
    if not isinstance(file_id, str):
        return None
    if not isinstance(name, str):
        return None
    parent = payload.get("parent_directory_id")
    created = payload.get("created_at")
    mime = payload.get("mime_type")
    return LibraryFile(
        library_file_id=library_file_id,
        file_id=file_id,
        name=name,
        created_at=created if isinstance(created, str) else None,
        parent_directory_id=parent if isinstance(parent, str) else None,
        mime_type=mime if isinstance(mime, str) else None,
    )


def _from_observed_item(item: Dict[str, object]) -> Optional[LibraryFile]:
    created = item.get("record_creation_time") or item.get("file_upload_time")
    return _to_library_file(
        {
            "library_file_id": item.get("id"),
            "file_id": item.get("file_id"),
            "file_name": item.get("name"),
            "created_at": created if isinstance(created, str) else None,
            "parent_directory_id": item.get("parent_directory_id"),
            "mime_type": item.get("mime_type"),
        }
    )


def parse_library_page(payload: object) -> Tuple[List[LibraryFile], Optional[str]]:
    """Parse one listing page into files plus the next cursor or offset."""
    if not isinstance(payload, dict):
        raise ChatGPTAPIError("Library sync failed: unexpected response shape.")
    items = payload.get("items")
    if isinstance(items, list):
        files: List[LibraryFile] = []
        for item in items:
            if not isinstance(item, dict):
                raise ChatGPTAPIError("Library sync failed: bad item shape.")
            kind = item.get("kind")
            if kind == "directory":
                continue
            if kind != "file":
                raise ChatGPTAPIError("Library sync failed: unknown item kind.")
            parsed = _from_observed_item(item)
            if parsed is None:
                raise ChatGPTAPIError("Library sync failed: file is missing an ID.")
            files.append(parsed)
        cursor = payload.get("cursor", "missing")
        if cursor == "missing":
            raise ChatGPTAPIError("Library sync failed: missing cursor.")
        if cursor is None:
            return files, None
        if not isinstance(cursor, str) or not cursor:
            raise ChatGPTAPIError("Library sync failed: invalid cursor.")
        return files, f"cursor={quote(cursor, safe='')}"
    nodes = payload.get("nodes")
    if not isinstance(nodes, list):
        raise ChatGPTAPIError("Library sync failed: unexpected response shape.")
    legacy: List[LibraryFile] = []
    for node in nodes:
        if not isinstance(node, dict):
            raise ChatGPTAPIError("Library sync failed: bad node shape.")
        parsed = _to_library_file(node)
        if parsed is None:
            raise ChatGPTAPIError("Library sync failed: node is missing an ID.")
        legacy.append(parsed)
    if not payload.get("has_more"):
        return legacy, None
    cursor_value = payload.get("next_cursor")
    offset_value = payload.get("next_offset")
    if cursor_value and offset_value is not None:
        raise ChatGPTAPIError("Library sync failed: ambiguous next page.")
    if cursor_value:
        if not isinstance(cursor_value, str):
            raise ChatGPTAPIError("Library sync failed: invalid cursor.")
        return legacy, f"cursor={quote(cursor_value, safe='')}"
    if isinstance(offset_value, int) and offset_value >= 0:
        return legacy, f"offset={offset_value}"
    raise ChatGPTAPIError("Library sync failed: invalid next page token.")


def list_library_files(
    access_token: str, cookies: Optional[str]
) -> List[LibraryFile]:
    """Return every Library file, following cursor pages."""
    files: List[LibraryFile] = []
    seen_ids: set = set()
    seen_pages: set = set()
    query: Optional[str] = None
    while True:
        path = NODES_PATH if query is None else f"{NODES_PATH}?{query}"
        payload = _request("GET", path, access_token, cookies)
        page_files, next_query = parse_library_page(payload)
        for item in page_files:
            if item.library_file_id in seen_ids:
                continue
            seen_ids.add(item.library_file_id)
            files.append(item)
        if next_query is None:
            return files
        if next_query in seen_pages:
            raise ChatGPTAPIError("Library sync failed: repeated a page token.")
        seen_pages.add(next_query)
        query = next_query
        time.sleep(PAGE_DELAY)


def build_delete_path(item: LibraryFile) -> str:
    """Build the backend delete URL for one Library file."""
    params = {"file_id": item.file_id, "file_name": item.name, "soft_delete": "true"}
    if item.parent_directory_id:
        params["parent_directory_id"] = item.parent_directory_id
    encoded = quote(item.library_file_id, safe="")
    return f"/backend-api/files/library/files/{encoded}?{urlencode(params)}"


def delete_library_file(
    access_token: str, cookies: Optional[str], item: LibraryFile
) -> bool:
    """Delete one Library file. Returns True only on explicit success."""
    payload = _request(
        "DELETE", build_delete_path(item), access_token, cookies, DELETE_TIMEOUT
    )
    return isinstance(payload, dict) and payload.get("success") is True


def format_files(files: List[LibraryFile]) -> str:
    lines = ["library_file_id\tfile_name\tmime_type"]
    for item in files:
        lines.append(
            f"{item.library_file_id}\t{item.name}\t{item.mime_type or ''}"
        )
    return "\n".join(lines)


def confirm(prompt: str) -> bool:
    answer = input(f"{prompt} [y/N] ").strip().lower()
    return answer in ("y", "yes")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="List and bulk-delete files in the ChatGPT Library."
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List Library files instead of deleting them.",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="Only include files whose name contains this text.",
    )
    parser.add_argument(
        "--delete",
        nargs="+",
        metavar="LIBRARY_FILE_ID",
        default=[],
        help="Delete the given Library file IDs.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Delete every listed file (combine with --name to narrow it).",
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
    return parser


def resolve_credentials(
    args: argparse.Namespace,
) -> Tuple[str, Optional[str]]:
    cookies = args.cookies or discover_cookies()
    token = args.access_token or discover_access_token()
    if token:
        return token, cookies
    if not cookies:
        raise AuthError(
            "No ChatGPT credentials. Set CHATGPT_COOKIES or "
            "CHATGPT_ACCESS_TOKEN (see the module docstring)."
        )
    token, _ = fetch_access_token(cookies)
    return token, cookies


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.list and not args.delete and not args.all:
        print("error: pass --list, --delete ID, or --all.", file=sys.stderr)
        return 2

    try:
        access_token, cookies = resolve_credentials(args)
        files = list_library_files(access_token, cookies)
        if args.name:
            needle = args.name.lower()
            files = [item for item in files if needle in item.name.lower()]

        if args.list and not args.delete and not args.all:
            print(f"{len(files)} file(s)")
            if files:
                print(format_files(files))
            return 0

        if args.all:
            targets = files
        else:
            known = {item.library_file_id: item for item in files}
            missing = [fid for fid in args.delete if fid not in known]
            if missing:
                print(
                    f"error: unknown file ID(s): {', '.join(missing)}",
                    file=sys.stderr,
                )
                return 1
            targets = [known[fid] for fid in args.delete]

        if not targets:
            print("No files match. Nothing to delete.")
            return 0
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
        for index, item in enumerate(targets):
            if index > 0:
                time.sleep(DELETE_SPACING)
            try:
                if delete_library_file(access_token, cookies, item):
                    deleted += 1
                    print(f"deleted {item.library_file_id} ({item.name})")
                else:
                    print(
                        f"paused: ChatGPT did not confirm {item.library_file_id}; "
                        "stopping the batch.",
                        file=sys.stderr,
                    )
                    failed.extend(
                        target.library_file_id for target in targets[index:]
                    )
                    break
            except RateLimitError as error:
                print(
                    f"paused: rate-limited at {item.library_file_id}; "
                    "stopping the batch.",
                    file=sys.stderr,
                )
                if error.retry_after > 0:
                    print(
                        f"ChatGPT asked to wait {error.retry_after:.0f}s.",
                        file=sys.stderr,
                    )
                failed.extend(target.library_file_id for target in targets[index:])
                break
            except ChatGPTAPIError as error:
                print(f"failed {item.library_file_id}: {error}", file=sys.stderr)
                failed.append(item.library_file_id)

        print(f"Done. Deleted {deleted}, failed {len(failed)}.")
        return 1 if failed else 0
    except ChatGPTAPIError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
