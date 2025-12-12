import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

try:  # Support execution as script or module
    from .get_stars import (
        GitHubAPIError,
        RateLimitError,
        discover_token,
        discover_tokens,
        fetch_repo_stars,
        format_stars,
        rate_limit_message,
    )
except ImportError:
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    from github.get_stars import (  # type: ignore
        GitHubAPIError,
        RateLimitError,
        discover_token,
        discover_tokens,
        fetch_repo_stars,
        format_stars,
        rate_limit_message,
    )


@dataclass
class RepoEntry:
    name: str
    slug: str
    url: str
    category: str
    note: str
    stars: Optional[int] = None


def _clean_note(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip(" -–—:")
    text = re.sub(r"\[(@?[^\]]+)\]\([^)]+\)", r"\1", text)  # flatten markdown links
    text = re.sub(r"[:;][a-zA-Z0-9_]+:", "", text)  # remove emoji shortcuts like :skull:
    return text.strip()


def _current_category(headings: List[str]) -> str:
    if len(headings) <= 1:
        return ""
    return " / ".join(headings[1:])


def parse_repos(markdown: str) -> List[RepoEntry]:
    headings: List[str] = []
    entries: List[RepoEntry] = []
    seen: Dict[str, RepoEntry] = {}

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        header_match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if header_match:
            level = len(header_match.group(1))
            title = header_match.group(2).strip()
            headings = headings[: level - 1]
            headings.append(title)
            continue

        bullet_match = re.match(
            r"^-+\s*\[([^\]]+)\]\((https?://github.com/[^)]+)\)\s*(.*)$", line
        )
        if not bullet_match:
            continue

        name, url, tail = bullet_match.groups()
        slug = url.split("github.com/")[1].split("/?")[0].split("#")[0].strip("/")
        slug_parts = slug.split("/")
        if len(slug_parts) < 2:
            continue
        slug = "/".join(slug_parts[:2])
        note = _clean_note(tail)
        category = _current_category(headings)

        if slug in seen:
            continue  # keep first occurrence
        entry = RepoEntry(
            name=name.strip(),
            slug=slug,
            url=f"https://github.com/{slug}",
            category=category,
            note=note,
        )
        seen[slug] = entry
        entries.append(entry)

    return entries


def fetch_star_data(entries: Iterable[RepoEntry], tokens: List[Optional[str]]) -> None:
    token_index = 0
    if not tokens:
        tokens = [None]

    for entry in entries:
        while True:
            token = tokens[token_index] if token_index < len(tokens) else None
            try:
                info = fetch_repo_stars(entry.slug, token=token)
                entry.stars = info.stars
                break
            except GitHubAPIError as err:
                if "Not Found" in str(err):
                    entry.stars = 0
                    break
                # Try next token if current one is invalid
                if "Bad credentials" in str(err) and token_index + 1 < len(tokens):
                    token_index += 1
                    continue
                raise


def render_markdown(entries: List[RepoEntry]) -> str:
    lines = [
        "| Rank | Repository | Stars | Category | Note |",
        "| --- | --- | --- | --- | --- |",
    ]
    for idx, entry in enumerate(entries, start=1):
        star_text = format_stars(entry.stars or 0)
        lines.append(
            f"| {idx} | [{entry.slug}]({entry.url}) | {star_text} | {entry.category} | {entry.note} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a GitHub stars table from the Awesome Docker README."
    )
    parser.add_argument("input", help="Path to the Awesome Docker README markdown.")
    parser.add_argument(
        "--output",
        default="github/awesome-docker-stars.md",
        help="Where to write the generated markdown table.",
    )
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as fh:
        markdown = fh.read()

    entries = parse_repos(markdown)
    tokens = discover_tokens()
    try:
        fetch_star_data(entries, tokens=tokens)
    except RateLimitError as err:
        raise SystemExit(rate_limit_message(err.reset_at))

    entries.sort(key=lambda e: e.stars or 0, reverse=True)
    report = render_markdown(entries)
    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write(report)


if __name__ == "__main__":
    main()
