"""Install a fake kinby package into the test environment the way a wheel would."""

from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent
from uuid import uuid4

VALID_CONFIG = "tone: plain\ntoken: EDITOR_TOKEN\n"
#: The editor's device sign-in, as the package declares it: source text of a SubscriptionLogin.
EDITOR_LOGIN = (
    'SubscriptionLogin(id="editor", label="Editor account", '
    'description="Signs the editor in with your subscription.", '
    'command=["editor", "login", "--device"], volume="/root/.editor", '
    r'prompt_pattern=r"Open (?P<url>https://\S+) and enter (?P<code>[A-Z0-9-]+)")'
)
#: The source of the setup fields the package declares by default: one choice in package.yaml.
TONE_FIELD = """
SetupField(
    name="tone",
    label="Tone",
    description="How drafts sound.",
    kind=SetupFieldKind.CONFIG,
    type=SetupFieldType.CHOICE,
    required=True,
    default="plain",
    choices=["plain", "formal"],
    target=SetupTarget(file=TargetFile.PACKAGE_YAML, key="tone"),
),
"""
#: The source of the secret field the package always declares, after its other setup fields.
EDITOR_TOKEN_FIELD = """
SetupField(
    name="EDITOR_TOKEN",
    label="Editor token",
    description="Authenticates editing.",
    kind=SetupFieldKind.SECRET,
    type=SetupFieldType.TEXT,
    required=True,
),
"""


@dataclass(frozen=True)
class FakePackage:
    site: Path
    module: str
    root: Path

    @property
    def template(self) -> Path:
        return self.root / "template"


def install_fake_package(
    site: Path,
    *,
    config: str | None = VALID_CONFIG,
    executables: tuple[str, ...] = (),
    record_template: bool = True,
    logins: tuple[str, ...] = (EDITOR_LOGIN,),
    setup_fields: str = TONE_FIELD,
) -> FakePackage:
    """Write package ``writer`` under *site* with a dist-info, entry points and RECORD.

    The module name is unique, so each test imports its own copy. *logins* is the source
    text of each login it declares. *setup_fields* is the source of its setup fields, which
    come before its ``EDITOR_TOKEN`` secret field.
    """
    module = f"kinby_fake_writer_{uuid4().hex[:8]}"
    root = site / module
    template = root / "template"
    (template / "routines" / "draft").mkdir(parents=True)
    (template / "SYSTEM.md").write_text("Write clearly.\n", encoding="utf-8")
    (template / "kinby.toml").write_text("[tools]\ndefaults = false\n", encoding="utf-8")
    (template / "permissions.toml").write_text(
        'mode = "full-access"\nceiling = "full-access"\n', encoding="utf-8"
    )
    if config is not None:
        (template / "package.yaml").write_text(config, encoding="utf-8")
    (template / "routines" / "draft" / "ROUTINE.md").write_text(
        "---\ndescription: Draft an article.\nenabled: false\n---\nDraft it.\n",
        encoding="utf-8",
    )
    (template / "routines" / "draft" / "run.py").write_text(
        f"from {module} import draft  # noqa: F401 - the import is the code step\n",
        encoding="utf-8",
    )
    skill = root / "skills" / "drafting"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: drafting\ndescription: Draft articles.\n---\n"
        "Follow [the style guide](style.md).\n",
        encoding="utf-8",
    )
    (skill / "style.md").write_text("Short sentences.\n", encoding="utf-8")
    (root / "__init__.py").write_text(
        dedent(
            f'''\
            import json
            from pathlib import Path
            from typing import Literal

            from kinby.packages import (
                Package,
                PackageConfig,
                SecretName,
                SetupField,
                SetupFieldKind,
                SetupFieldType,
                SetupTarget,
                SubscriptionLogin,
                TargetFile,
            )
            from kinby.plugins import ToolContext, tool

            ROOT = Path(__file__).parent


            class WriterConfig(PackageConfig):
                tone: Literal["plain", "formal"]
                token: SecretName


            @tool(write=False)
            def draft(context: ToolContext) -> None:
                """Record the configuration this run received."""
                config = context.package_config
                seen = None if config is None else config.model_dump(mode="json")
                (context.workspace / "seen.json").write_text(json.dumps(seen))


            PACKAGE = Package(
                display_name="Writing teammate",
                description="Drafts articles.",
                icon="pen",
                template=ROOT / "template",
                setup_fields=({{setup_fields}}),
                config=WriterConfig,
                executables={executables!r},
                logins=({"".join(f"{login}, " for login in logins)}),
            )
            SKILLS = ROOT / "skills"
            '''
        ).replace("{setup_fields}", setup_fields + EDITOR_TOKEN_FIELD),
        encoding="utf-8",
    )
    dist_info = site / f"{module}-1.4.2.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {module}\nVersion: 1.4.2\n", encoding="utf-8"
    )
    (dist_info / "entry_points.txt").write_text(
        f"[kinby.packages]\nwriter = {module}:PACKAGE\n\n"
        f"[kinby.skills]\nwriter = {module}:SKILLS\n",
        encoding="utf-8",
    )
    recorded = [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file() and (record_template or template not in path.parents)
    ]
    (dist_info / "RECORD").write_text(
        "".join(f"{path.relative_to(site).as_posix()},,\n" for path in recorded)
        + f"{dist_info.name}/METADATA,,\n{dist_info.name}/entry_points.txt,,\n"
        + f"{dist_info.name}/RECORD,,\n",
        encoding="utf-8",
    )
    return FakePackage(site=site, module=module, root=root)
