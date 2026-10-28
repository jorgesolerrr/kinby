"""The vault: each instance's sign-ins on the hub, stored encrypted, listed by name and site, and
written by the user alone. The hub fills them into its browser's pages (ADR 0114), which
tests/test_browser_service.py drives."""

import asyncio
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from kinby.contracts import (
    BROWSER_VAULT_ADD,
    BROWSER_VAULT_DELETE,
    BROWSER_VAULT_EDIT,
    BROWSER_VAULT_LIST,
    ErrorCode,
    ErrorEnvelope,
    VaultEntry,
    VaultEntryAddCommand,
    VaultEntryDeleteCommand,
    VaultEntryEditCommand,
    VaultEntryListCommand,
    VaultEntryListResult,
    VaultField,
)
from kinby.core.errors import VaultEntryNotFound, VaultFillRefused
from kinby.hub.vault import Vault, totp
from tests.test_hub import hub_client
from tests.test_hub_browser import _LIMITS, browser_hub, result
from tests.test_hub_home_reads import answers, error_code, home_hub

_USERNAME = "alice@example.com"
_PASSWORD = "correct horse battery staple"
#: The seed of RFC 6238's SHA-1 test vectors, "12345678901234567890" in base32.
_SEED = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


def _linkedin(instance_id, **values) -> VaultEntryAddCommand:
    return VaultEntryAddCommand.model_validate(
        {
            "instance_id": instance_id,
            "name": "LinkedIn",
            "site": "https://www.linkedin.com/login",
            "username": _USERNAME,
            "password": _PASSWORD,
            "totp_seed": _SEED,
        }
        | values
    )


def test_an_added_entry_is_stored_encrypted_and_listed_by_name_and_site_only(tmp_path):
    async def scenario() -> tuple[object, object, dict[str, object], Path, Path, UUID]:
        async with browser_hub(tmp_path / "hub", _LIMITS) as hub:
            client = hub_client(hub.hub)
            added = await client.call(BROWSER_VAULT_ADD, _linkedin(hub.alice))
            listed = await client.call(
                BROWSER_VAULT_LIST, VaultEntryListCommand(instance_id=hub.alice)
            )
            own = result(await hub.call(hub.alice, "browser.vault", {}))
            record = hub.hub.registry.instance(hub.alice)
            assert record is not None
            return added, listed, own, hub.hub.directory, record.path, hub.alice

    added, listed, own, hub_directory, instance_directory, alice = asyncio.run(scenario())

    entries = VaultEntryListResult(
        entries=(VaultEntry(name="LinkedIn", site="https://www.linkedin.com"),)
    )
    assert added == listed == entries
    assert own == {"entries": [{"name": "LinkedIn", "site": "https://www.linkedin.com"}]}
    files = [path for path in hub_directory.rglob("*") if path.is_file()]
    for value in (_USERNAME, _PASSWORD, _SEED):
        assert not [path for path in files if value.encode() in path.read_bytes()]
    (stored,) = (hub_directory / "vault").glob(f"{alice}*")
    assert stored.stat().st_mode & 0o777 == 0o600
    assert not stored.is_relative_to(instance_directory)


def test_the_home_instance_lists_vault_entries_and_is_refused_their_writes(tmp_path):
    async def scenario() -> list[dict[str, object]]:
        async with home_hub(tmp_path) as hub:
            alice = str(hub.alice)
            return await answers(
                hub.route(hub.home),
                hub.home_token,
                ("browser.vault.list", {"instance_id": alice}),
                (
                    "browser.vault.add",
                    _linkedin(hub.alice).model_dump(mode="json"),
                ),
                (
                    "browser.vault.edit",
                    {"instance_id": alice, "name": "LinkedIn", "password": "stolen"},
                ),
                ("browser.vault.delete", {"instance_id": alice, "name": "LinkedIn"}),
            )

    listed, *writes = asyncio.run(scenario())

    assert listed["result"] == {"entries": []}
    assert [error_code(write) for write in writes] == [ErrorCode.PERMISSION_DENIED.value] * 3


def test_the_user_edits_and_deletes_an_instances_entries_and_no_other_instances(tmp_path):
    async def scenario() -> list[object]:
        async with browser_hub(tmp_path / "hub", _LIMITS) as hub:
            client = hub_client(hub.hub)
            await client.call(BROWSER_VAULT_ADD, _linkedin(hub.alice))
            return [
                await client.call(
                    BROWSER_VAULT_EDIT,
                    VaultEntryEditCommand(instance_id=hub.bob, name="LinkedIn", site="https://x"),
                ),
                await client.call(
                    BROWSER_VAULT_DELETE,
                    VaultEntryDeleteCommand(instance_id=hub.bob, name="LinkedIn"),
                ),
                await client.call(
                    BROWSER_VAULT_EDIT,
                    VaultEntryEditCommand.model_validate(
                        {
                            "instance_id": hub.alice,
                            "name": "LinkedIn",
                            "site": "https://linkedin.com",
                            "password": "a new password",
                        }
                    ),
                ),
                await client.call(
                    BROWSER_VAULT_DELETE,
                    VaultEntryDeleteCommand(instance_id=hub.alice, name="LinkedIn"),
                ),
            ]

    bobs_edit, bobs_delete, edited, deleted = asyncio.run(scenario())

    assert isinstance(bobs_edit, ErrorEnvelope)
    assert bobs_edit.code is ErrorCode.NOT_FOUND
    assert isinstance(bobs_delete, ErrorEnvelope)
    assert bobs_delete.code is ErrorCode.NOT_FOUND
    assert edited == VaultEntryListResult(
        entries=(VaultEntry(name="LinkedIn", site="https://linkedin.com"),)
    )
    assert deleted == VaultEntryListResult(entries=())


def test_an_entry_needs_a_web_site_a_base32_seed_and_a_name_of_its_own(tmp_path):
    async def scenario() -> list[object]:
        async with browser_hub(tmp_path / "hub", _LIMITS) as hub:
            client = hub_client(hub.hub)
            await client.call(BROWSER_VAULT_ADD, _linkedin(hub.alice))
            return [
                await client.call(BROWSER_VAULT_ADD, _linkedin(hub.alice, name="Other", site=site))
                for site in ("www.linkedin.com", "ftp://files.example.com")
            ] + [
                await client.call(
                    BROWSER_VAULT_ADD, _linkedin(hub.alice, name="Other", totp_seed="not base32!")
                ),
                await client.call(BROWSER_VAULT_ADD, _linkedin(hub.alice)),
            ]

    refusals = asyncio.run(scenario())

    for refusal in refusals:
        assert isinstance(refusal, ErrorEnvelope)
        assert refusal.code is ErrorCode.INVALID_ARGUMENT
        assert _PASSWORD not in refusal.message


def test_the_totp_code_is_rfc_6238s_for_the_seed_at_the_time() -> None:
    assert totp(_SEED, 59) == "287082"
    assert totp(_SEED, 1111111109) == "081804"
    assert totp("gezd gnbv gy3t qojq gezd gnbv gy3t qojq", 1111111109) == "081804"


_SIGN_IN = "https://www.linkedin.com/login"


def test_a_value_is_given_for_a_page_on_the_entrys_site_with_an_edit_and_the_kept_ones(
    tmp_path,
):
    vault = Vault(tmp_path, clock=lambda: 59)
    alice = uuid4()
    vault.add(_linkedin(alice))
    vault.edit(
        VaultEntryEditCommand.model_validate(
            {"instance_id": alice, "name": "LinkedIn", "password": "a new one"}
        )
    )

    assert [vault.value(alice, "LinkedIn", field, _SIGN_IN) for field in VaultField] == [
        _USERNAME,
        "a new one",
        "287082",
    ]


@pytest.mark.parametrize(
    ("name", "url", "refusal", "message"),
    [
        (
            "LinkedIn",
            "https://linkedin.example.com/login",
            VaultFillRefused,
            "fills pages on https://www.linkedin.com only, not on https://linkedin.example.com",
        ),
        ("LinkedIn", "http://www.linkedin.com/login", VaultFillRefused, "not on http://"),
        ("Indeed", _SIGN_IN, VaultEntryNotFound, 'no entry named "Indeed"'),
    ],
)
def test_a_value_for_a_page_off_the_entrys_site_or_of_an_entry_the_vault_lacks_is_refused(
    tmp_path, name: str, url: str, refusal: type[Exception], message: str
):
    vault = Vault(tmp_path)
    alice = uuid4()
    vault.add(_linkedin(alice))

    with pytest.raises(refusal, match=message):
        vault.value(alice, name, VaultField.PASSWORD, url)


def test_an_entry_with_no_seed_gives_no_code(tmp_path):
    vault = Vault(tmp_path)
    alice = uuid4()
    vault.add(_linkedin(alice, totp_seed=None))

    with pytest.raises(VaultFillRefused, match="no TOTP seed"):
        vault.value(alice, "LinkedIn", VaultField.TOTP, _SIGN_IN)
