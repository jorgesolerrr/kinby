"""What an instance update does to the files its factory's template supplied (ADR 0084)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from kinby.contracts import FileHash
from kinby.hub.factories import InstanceTemplate
from kinby.instance.config_changes import file_hash
from kinby.instance.layout import MEMORY_DIR


@dataclass(frozen=True)
class TemplateSync:
    """The files an update wrote from its template, removed and kept, by their paths."""

    template: InstanceTemplate
    written: tuple[str, ...]
    removed: tuple[str, ...]
    kept: tuple[str, ...]
    #: The template's baseline, where a kept file keeps the entry it had.
    baseline: dict[str, FileHash]

    @property
    def detail(self) -> str:
        done = [
            f"{verb} {', '.join(paths)}."
            for verb, paths in (
                ("Wrote", self.written),
                ("Removed", self.removed),
                ("Kept the instance's own", self.kept),
            )
            if paths
        ]
        return " ".join(done) or f'The files already match template "{self.template.name}".'


def sync_template(
    template: InstanceTemplate, directory: Path, baseline: dict[str, FileHash] | None
) -> TemplateSync:
    """Bring the files in *directory* up to *template*, comparing each with *baseline*.

    A file the instance changed since its baseline is kept, and so are its memory, a path
    through a link it made, and a file of its own where the template's would go. With no
    baseline, the instance takes every file the template supplies and loses none of its own.
    """
    before = baseline or {}
    written: list[str] = []
    removed: list[str] = []
    kept: list[str] = []
    for path in sorted(template.baseline.keys() | before.keys()):
        wanted = template.baseline.get(path)
        if wanted == before.get(path):
            continue
        target = directory / path
        linked = _linked(directory, path)
        held = file_hash(target.read_bytes()) if not linked and target.is_file() else None
        if held == wanted:
            continue
        if (
            linked
            or PurePosixPath(path).parts[0] == MEMORY_DIR
            or (baseline is not None and held != before.get(path))
        ):
            kept.append(path)
        elif wanted is None:
            removed.append(path)
        else:
            written.append(path)
    # Removals go first, so a file the template turned into a folder, or back, makes way.
    for path in removed:
        _remove(directory, path)
    for path in [path for path in written if not _write(directory / path, template.files[path])]:
        written.remove(path)
        kept.append(path)
    return TemplateSync(
        template=template,
        written=tuple(written),
        removed=tuple(removed),
        kept=tuple(sorted(kept)),
        baseline={path: entry for path, entry in template.baseline.items() if path not in kept}
        | {path: before[path] for path in kept if path in before},
    )


def _linked(directory: Path, path: str) -> bool:
    """Whether *path* runs through a link, which could lead the hub out of *directory*."""
    target = directory
    for part in PurePosixPath(path).parts:
        target /= part
        if target.is_symlink():
            return True
    return False


def _remove(directory: Path, path: str) -> None:
    target = directory / path
    target.unlink()
    for parent in target.parents:
        if parent == directory or any(parent.iterdir()):
            break
        parent.rmdir()


def _write(target: Path, content: str) -> bool:
    """Write *target*, unless a file or folder of the instance's own is in its way."""
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except FileExistsError, NotADirectoryError, IsADirectoryError:
        return False
    return True
