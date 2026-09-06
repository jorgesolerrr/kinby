# Packaged skills load between instance and workspace skills

Ticket #116 adds the `kinby.skills` entry-point group. Each entry point exports a
`pathlib.Path` containing `<name>/SKILL.md` directories. Kinby's defaults package
exports `SKILLS` alongside `TOOLS` and ships `write-routine` there, so the routine
format updates with kinby instead of freezing in a file copied by `kinby init`.

The loader reads instance skills, packaged skills, then workspace convention
skills. The first tier declaring a name wins silently. Within a tier, the first
declaration wins with a warning naming both sources, extending ADR 0013 to
packages. Broken exports warn and leave other packages available, as tool exports do.

`[tools] defaults = false` skips kinby's `defaults` skill entry point under the
same distribution check used for default tools. Other packages and instance or
workspace skills remain available. The skill loader keeps its existing turn-boundary
loading behavior.
