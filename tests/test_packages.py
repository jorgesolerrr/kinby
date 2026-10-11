from types import SimpleNamespace

import pytest

from kinby import packages as package_module
from kinby.contracts import SetupField, SetupFieldKind, SetupFieldType, SetupTarget, TargetFile
from kinby.packages import Package, inspect_installed_package, with_built_in_fields

TONE = SetupField(
    name="tone",
    label="Tone",
    description="How drafts sound.",
    kind=SetupFieldKind.CONFIG,
    type=SetupFieldType.CHOICE,
    required=True,
    default="plain",
    choices=["plain", "formal"],
    target=SetupTarget(file=TargetFile.PACKAGE_YAML, key="tone"),
)
MODEL = SetupField(
    name="model",
    label="Our model",
    description="Ignored: a package overrides a built-in field's default only.",
    kind=SetupFieldKind.CONFIG,
    type=SetupFieldType.TEXT,
    required=True,
    default="anthropic:claude-opus-5-5",
)


def _installed(monkeypatch, exported: Package):
    class EntryPoint:
        name = "writer"
        dist = SimpleNamespace(name="kinby-writer", version="1.4.2")

        @staticmethod
        def load():
            return exported

    monkeypatch.setattr(package_module, "entry_points", lambda *, group: [EntryPoint()])
    return inspect_installed_package("writer")


def test_installed_package_entry_point_supplies_descriptor_template_and_validation(
    tmp_path,
    monkeypatch,
):
    template = tmp_path / "template"
    template.mkdir()
    (template / "SYSTEM.md").write_text("Write clearly.\n", encoding="utf-8")
    validated = []
    exported = Package(
        display_name="Writing teammate",
        description="Drafts articles.",
        icon="pen",
        template=template,
        validate=validated.append,
    )

    installed = _installed(monkeypatch, exported)

    assert installed.descriptor.id == "writer"
    assert installed.descriptor.distribution == "kinby-writer"
    assert installed.descriptor.version == "1.4.2"
    assert installed.files == {"SYSTEM.md": "Write clearly.\n"}
    assert validated == [template.resolve()]


@pytest.mark.parametrize("override", [True, False])
def test_a_package_overrides_a_built_in_default_but_never_removes_the_field(
    tmp_path, monkeypatch, override
):
    template = tmp_path / "template"
    template.mkdir()
    exported = Package(
        display_name="Writing teammate",
        description="Drafts articles.",
        icon="pen",
        template=template,
        setup_fields=(MODEL, TONE) if override else (TONE,),
    )

    setup_fields = with_built_in_fields(_installed(monkeypatch, exported).descriptor.setup_fields)

    fields = {field.name: field for field in setup_fields}
    assert [field.name for field in setup_fields] == ["model", "api_key", "tone"]
    assert fields["model"].label == "Model"
    assert fields["model"].default == ("anthropic:claude-opus-5-5" if override else None)
