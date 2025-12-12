"""
Lightweight GitHub helpers to fetch repository star counts.

- Works without a token (low rate-limit, 60 req/hr).
- Uses a PAT automatically when one is available in the environment.
- Provides clear feedback when rate limits are hit.
- Supports fetching a single repo or many in one call.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class GitHubAPIError(Exception):
    """Base exception for GitHub API failures."""


class RateLimitError(GitHubAPIError):
    """Raised when the GitHub API rate limit is exceeded."""

    def __init__(self, message: str, reset_at: Optional[int] = None) -> None:
        super().__init__(message)
        self.reset_at = reset_at


@dataclass
class RepoStars:
    slug: str
    stars: int
    html_url: str
    description: Optional[str] = None


def discover_token(env: Optional[Dict[str, str]] = None) -> Optional[str]:
    """
    Try to find a GitHub token in the environment.

    Looks for common keys first, then anything containing "PAT".
    """
    tokens = discover_tokens(env=env)
    return tokens[0] if tokens else None


def discover_tokens(env: Optional[Dict[str, str]] = None) -> List[str]:
    """
    Return all candidate tokens found in the environment, preserving priority order.
    """
    env = env or os.environ
    preferred_keys = [
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "GITHUB_PAT",
        "GH_PAT",
    ]
    tokens: List[str] = []

    for key in preferred_keys:
        val = env.get(key)
        if val and val not in tokens:
            tokens.append(val)

    for key, value in env.items():
        if "PAT" in key.upper() and value and value not in tokens:
            tokens.append(value)
    return tokens


def normalize_slug(repo: str) -> str:
    """
    Convert a GitHub URL or slug into an owner/repo slug.
    """
    if "://" in repo:
        parsed = urlparse(repo)
        if parsed.netloc.lower() != "github.com":
            raise ValueError(f"Unsupported host in repo URL: {repo}")
        parts = parsed.path.strip("/").split("/")
        if len(parts) < 2:
            raise ValueError(f"Invalid GitHub URL: {repo}")
        return "/".join(parts[:2])
    if repo.count("/") != 1:
        raise ValueError(f"Invalid repo slug: {repo}")
    return repo


def _build_headers(token: Optional[str]) -> Dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "sociomancer-star-fetcher",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _handle_error(error: HTTPError) -> None:
    try:
        data = json.loads(error.read().decode("utf-8"))
    except Exception:
        data = {}
    message = data.get("message") or str(error)
    reset_at = None
    if error.headers:
        reset_header = error.headers.get("X-RateLimit-Reset")
        if reset_header and reset_header.isdigit():
            reset_at = int(reset_header)
    if error.code == 403 and "rate limit" in message.lower():
        raise RateLimitError(message, reset_at=reset_at)
    raise GitHubAPIError(f"{message} (HTTP {error.code})")


def _request_json(url: str, token: Optional[str]) -> Tuple[Dict, Dict[str, str]]:
    req = Request(url, headers=_build_headers(token))
    try:
        with urlopen(req) as resp:
            body = resp.read().decode("utf-8")
            data = json.loads(body)
            headers = dict(resp.headers)
            return data, headers
    except HTTPError as err:
        _handle_error(err)
    except URLError as err:
        raise GitHubAPIError(f"Network error: {err}") from err

    # Unreachable, but placates type checkers.
    return {}, {}


def fetch_repo_stars(repo: str, token: Optional[str] = None) -> RepoStars:
    """
    Fetch star count for a single repository.

    Args:
        repo: Owner/repo slug or full GitHub URL.
        token: Optional PAT; if omitted we attempt to discover one.
    """
    slug = normalize_slug(repo)
    token = token if token is not None else discover_token()
    data, _ = _request_json(f"https://api.github.com/repos/{slug}", token)
    return RepoStars(
        slug=slug,
        stars=int(data.get("stargazers_count", 0)),
        html_url=data.get("html_url", f"https://github.com/{slug}"),
        description=data.get("description"),
    )


def fetch_many(repos: Iterable[str], token: Optional[str] = None) -> List[RepoStars]:
    """
    Fetch star counts for many repositories sequentially.
    """
    token = token if token is not None else discover_token()
    results: List[RepoStars] = []
    for repo in repos:
        results.append(fetch_repo_stars(repo, token=token))
    return results


def format_stars(stars: int) -> str:
    """
    Return a human-friendly rounded representation (thousands, one decimal place).
    """
    if stars >= 1000:
        return f"{stars/1000:.1f}k"
    return str(stars)


def rate_limit_message(reset_at: Optional[int]) -> str:
    """
    Build a user-facing message when the rate limit is exceeded.
    """
    if reset_at:
        delta = max(0, reset_at - int(time.time()))
        minutes = int(delta / 60)
        return f"GitHub rate limit exceeded. Try again in ~{minutes} minutes."
    return "GitHub rate limit exceeded. Try again later or provide a PAT."
