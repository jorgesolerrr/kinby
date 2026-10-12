from kinby.contracts import DeclaredSetup, SetupFieldKind, SetupFieldType
from kinby.instance import init_instance
from kinby.setup.__main__ import main as describe_image


def test_the_image_description_is_a_vanilla_instance(capsys):
    assert describe_image() == 0

    printed = DeclaredSetup.model_validate_json(capsys.readouterr().out)
    assert [
        (field.name, field.label, field.kind, field.type, field.required)
        for field in printed.setup_fields
    ] == [
        ("model", "Model", SetupFieldKind.CONFIG, SetupFieldType.TEXT, True),
        ("api_key", "API key", SetupFieldKind.SECRET, SetupFieldType.TEXT, True),
        (
            "behavior_prompt",
            "Behavior prompt",
            SetupFieldKind.CONFIG,
            SetupFieldType.MULTILINE,
            False,
        ),
    ]


def test_the_behavior_prompt_starts_from_the_one_kinby_writes(tmp_path, capsys):
    describe_image()
    printed = DeclaredSetup.model_validate_json(capsys.readouterr().out)
    init_instance(tmp_path / "default", model="openai:gpt-5")

    [prompt] = [field for field in printed.setup_fields if field.name == "behavior_prompt"]
    assert prompt.default == (tmp_path / "default" / "SYSTEM.md").read_text()
