"""Selection and deduplication of new release announcement content."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass


def _first_text(entry: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


@dataclass(frozen=True)
class ReleaseNote:
    tag: str
    sha: str
    content: str
    published_at: str = ""


class ReleaseNoteSelector:
    """Choose the newest nonempty release note not recorded by tag or SHA."""

    def select(
        self,
        entries: Iterable[Mapping[str, object]],
        *,
        recorded_tags: set[str] | frozenset[str] = frozenset(),
        recorded_shas: set[str] | frozenset[str] = frozenset(),
    ) -> ReleaseNote | None:
        candidates: list[ReleaseNote] = []
        for entry in entries:
            tag = _first_text(entry, "tag_name", "tag")
            sha = _first_text(entry, "sha", "target_sha")
            content = _first_text(entry, "body", "content", "changelog")
            published_at = _first_text(entry, "published_at", "created_at")
            if content and (tag or sha):
                candidates.append(ReleaseNote(tag, sha, content, published_at))

        candidates.sort(key=lambda note: note.published_at, reverse=True)
        seen_tags = set(recorded_tags)
        seen_shas = set(recorded_shas)
        for note in candidates:
            if (note.tag and note.tag in seen_tags) or (note.sha and note.sha in seen_shas):
                continue
            if note.tag:
                seen_tags.add(note.tag)
            if note.sha:
                seen_shas.add(note.sha)
            return note
        return None


class AnnouncementSelector:
    """Collect optional project notes changed in a candidate commit."""

    _PREFIX = "xl_deploy/announcements/"
    _MAX_LENGTH = 4000

    def select(self, paths, *, read_text, commit_sha: str) -> str | None:
        selected_paths: list[str] = []
        for path in paths:
            if not isinstance(path, str):
                continue
            if path.startswith((self._PREFIX, "/xl_deploy/announcements/")):
                if not self._valid_path(path):
                    raise ValueError("announcement path is not allowlisted")
                selected_paths.append(path)

        bodies: list[str] = []
        for path in sorted(set(selected_paths)):
            try:
                body = read_text(commit_sha, path)
            except Exception:  # noqa: BLE001 - a missing note must never block deployment.
                return None
            if isinstance(body, str) and body.strip():
                bodies.append(body.strip())
        if not bodies:
            return None
        content = "\n\n".join(bodies)
        return content if len(content) <= self._MAX_LENGTH else None

    @classmethod
    def _valid_path(cls, path: str) -> bool:
        if "\\" in path or "\0" in path or path.startswith("/"):
            return False
        parts = path.split("/")
        return (
            len(parts) == 3
            and parts[0] == "xl_deploy"
            and parts[1] == "announcements"
            and parts[2].endswith(".md")
            and parts[2] not in {"", ".", ".."}
        )
