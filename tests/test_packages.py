from types import SimpleNamespace

import pytest

from kinby import packages as package_module
from kinby.contracts import SetupField, SetupFieldKind, SetupFieldType, SetupTarget, TargetFile
from kinby.packages import (
    Package,
    RequiredSecret,
    inspect_installed_package,
    installed_package_from_json,
    package_description,
    package_json,
)

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
DRAFTS = SetupField(
    name="drafts",
    label="Drafts per day",
    description="How many drafts a day at most.",
    kind=SetupFieldKind.CONFIG,
    type=SetupFieldType.INTEGER,
    required=False,
    default=3,
    target=SetupTarget(file=TargetFile.KINBY_TOML, key="budgets.steps"),
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


def test_setup_fields_and_required_secrets_round_trip_as_setup_fields(tmp_path, monkeypatch):
    template = tmp_path / "template"
    template.mkdir()
    exported = Package(
        display_name="Writing teammate",
        description="Drafts articles.",
        icon="pen",
        template=template,
        required_secrets=(
            RequiredSecret("EDITOR_TOKEN", "Editor token", "Authenticates editing."),
        ),
        setup_fields=(TONE, DRAFTS),
    )

    installed = _installed(monkeypatch, exported)
    parsed = installed_package_from_json(package_json(installed))

    assert parsed == installed
    assert parsed.descriptor.setup_fields == (
        TONE,
        DRAFTS,
        SetupField(
            name="EDITOR_TOKEN",
            label="Editor token",
            description="Authenticates editing.",
            kind=SetupFieldKind.SECRET,
            type=SetupFieldType.TEXT,
            required=True,
        ),
    )


def test_an_image_from_before_setup_fields_reads_its_required_secrets_as_secret_fields():
    printed = (
        '{"descriptor":{"id":"writer","display_name":"Writing teammate",'
        '"description":"Drafts articles.","icon":"pen","distribution":"kinby-writer",'
        '"version":"1.4.2","required_secrets":[{"name":"EDITOR_TOKEN",'
        '"label":"Editor token","description":"Authenticates editing."}]},"files":{}}'
    )

    parsed = installed_package_from_json(printed)

    assert [
        (field.name, field.kind, field.type, field.required)
        for field in parsed.descriptor.setup_fields
    ] == [("EDITOR_TOKEN", SetupFieldKind.SECRET, SetupFieldType.TEXT, True)]


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

    description = package_description(_installed(monkeypatch, exported))

    fields = {field.name: field for field in description.setup_fields}
    assert [field.name for field in description.setup_fields] == ["model", "api_key", "tone"]
    assert fields["model"].label == "Model"
    assert fields["model"].default == ("anthropic:claude-opus-5-5" if override else None)
