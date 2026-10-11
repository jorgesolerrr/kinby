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
    """The files an update writes from its template, removes and keeps, by their paths."""

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

    def apply(self, directory: Path) -> None:
        for path in self.written:
            target = directory / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(self.template.files[path], encoding="utf-8")
        for path in self.removed:
            target = directory / path
            target.unlink()
            for parent in target.parents:
                if parent == directory or any(parent.iterdir()):
                    break
                parent.rmdir()


def template_sync(
    template: InstanceTemplate, directory: Path, baseline: dict[str, FileHash] | None
) -> TemplateSync:
    """Compare each file in *directory* with *baseline* and with what *template* supplies now.

    A file the instance changed since its baseline is kept, and so is its memory. With no
    baseline, the instance takes every file the template supplies and loses none of its own.
    """
    before = baseline or {}
    written: list[str] = []
    removed: list[str] = []
    kept: list[str] = []
    for path in sorted(template.baseline.keys() | before.keys()):
        wanted = template.baseline.get(path)
        found = directory / path
        held = file_hash(found.read_bytes()) if found.is_file() else None
        if held == wanted or wanted == before.get(path):
            continue
        if PurePosixPath(path).parts[0] == MEMORY_DIR or (
            baseline is not None and held != before.get(path)
        ):
            kept.append(path)
        elif wanted is None:
            removed.append(path)
        else:
            written.append(path)
    return TemplateSync(
        template=template,
        written=tuple(written),
        removed=tuple(removed),
        kept=tuple(kept),
        baseline={path: entry for path, entry in template.baseline.items() if path not in kept}
        | {path: before[path] for path in kept if path in before},
    )
