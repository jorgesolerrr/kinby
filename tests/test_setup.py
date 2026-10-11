from kinby.contracts import DeclaredSetup, SetupFieldKind, SetupFieldType
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
