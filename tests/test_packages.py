from types import SimpleNamespace

from kinby import packages as package_module
from kinby.packages import Package, RequiredSecret, inspect_installed_package


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
        required_secrets=(
            RequiredSecret("EDITOR_TOKEN", "Editor token", "Authenticates editing."),
        ),
        validate=validated.append,
    )

    class EntryPoint:
        name = "writer"
        dist = SimpleNamespace(name="kinby-writer", version="1.4.2")

        @staticmethod
        def load():
            return exported

    monkeypatch.setattr(
        package_module,
        "entry_points",
        lambda *, group: [EntryPoint()],
    )

    installed = inspect_installed_package("writer")

    assert installed.descriptor.id == "writer"
    assert installed.descriptor.distribution == "kinby-writer"
    assert installed.descriptor.version == "1.4.2"
    assert installed.descriptor.required_secrets[0].name == "EDITOR_TOKEN"
    assert installed.files == {"SYSTEM.md": "Write clearly.\n"}
    assert validated == [template.resolve()]
