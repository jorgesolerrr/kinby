"""The vault: each instance's sign-ins on the hub, stored encrypted, listed by name and site, and
written by the user alone (ADR 0113)."""

import asyncio
from pathlib import Path
from uuid import UUID, uuid4

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
from kinby.hub.vault import totp
from kinby.plugins.browser import FillCredential
from tests.test_hub import hub_client
from tests.test_hub_browser import _LIMITS, BrowserHub, Turn, _result, browser_hub, turn_browser
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
            own = _result(await hub.call(hub.alice, "browser.vault", {}))
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


_FRAME = "frame@1"
_SIGN_IN = "https://www.linkedin.com/login"


async def _fill(
    browser: Turn, field: VaultField, *, name: str = "LinkedIn", ref: str = "e3"
) -> dict[str, object]:
    """Fill the field *ref* with the *field* of the entry *name*, as the adapter does."""
    placeholder = FillCredential(ref, name, field).placeholder
    return await browser.call(
        _FRAME, "fill", {"selector": f"aria-ref={ref}", "strict": True, "value": placeholder}
    )


def _filled(hub: BrowserHub) -> list[object]:
    """The values the service was asked to fill."""
    fills = [call["params"] for call in hub.service.calls if call["method"] == "fill"]
    return [params["value"] for params in fills if isinstance(params, dict)]


def test_a_fill_on_the_entrys_site_puts_the_value_in_the_page_and_never_in_the_turn(tmp_path):
    async def scenario() -> tuple[dict[str, object], dict[str, object], list[str], list[object]]:
        async with browser_hub(tmp_path / "hub", _LIMITS) as hub:
            await hub_client(hub.hub).call(BROWSER_VAULT_ADD, _linkedin(hub.alice))
            async with turn_browser(hub, uuid4()) as browser:
                await browser.call(_FRAME, "goto", {"url": _SIGN_IN})
                filled = await _fill(browser, VaultField.PASSWORD)
                read = await browser.call(_FRAME, "inputValue", {"selector": "aria-ref=e3"})
                await _fill(browser, VaultField.USERNAME, ref="e2")
                return filled, read, browser.received, _filled(hub)

    filled, read, received, asked = asyncio.run(scenario())

    assert filled["result"] == {}
    assert asked == [_PASSWORD, _USERNAME]
    # The route lets no read of a field's value through.
    assert read["error"] == {
        "error": {
            "name": "Error",
            "message": 'The hub\'s browser route refuses "inputValue".',
            "stack": "",
        }
    }
    assert not [frame for frame in received if _PASSWORD in frame or _USERNAME in frame]


def test_a_fill_on_another_site_is_refused_and_reaches_no_page(tmp_path):
    async def scenario() -> tuple[dict[str, object], list[object]]:
        async with browser_hub(tmp_path / "hub", _LIMITS) as hub:
            await hub_client(hub.hub).call(BROWSER_VAULT_ADD, _linkedin(hub.alice))
            async with turn_browser(hub, uuid4()) as browser:
                await browser.call(_FRAME, "goto", {"url": "https://linkedin.example.com/login"})
                return await _fill(browser, VaultField.PASSWORD), _filled(hub)

    refused, asked = asyncio.run(scenario())

    assert refused["error"] == {
        "error": {
            "name": "Error",
            "message": 'The vault entry "LinkedIn" fills pages on https://www.linkedin.com '
            "only, not on https://linkedin.example.com.",
            "stack": "",
        }
    }
    assert asked == []


def test_a_fill_by_a_ref_into_another_frame_or_of_an_entry_the_vault_lacks_is_refused(tmp_path):
    async def scenario() -> list[dict[str, object]]:
        async with browser_hub(tmp_path / "hub", _LIMITS) as hub:
            await hub_client(hub.hub).call(BROWSER_VAULT_ADD, _linkedin(hub.alice))
            async with turn_browser(hub, uuid4()) as browser:
                await browser.call(_FRAME, "goto", {"url": _SIGN_IN})
                return [
                    await _fill(browser, VaultField.PASSWORD, ref="f1e3"),
                    await _fill(browser, VaultField.PASSWORD, name="Indeed"),
                ]

    into_a_frame, unknown = asyncio.run(scenario())

    assert "error" in into_a_frame
    assert unknown["error"] == {
        "error": {"name": "Error", "message": 'The vault has no entry named "Indeed".', "stack": ""}
    }


def test_a_fill_that_fails_hides_the_value_playwrights_call_log_names(tmp_path):
    async def scenario() -> dict[str, object]:
        async with browser_hub(tmp_path / "hub", _LIMITS) as hub:
            hub.service.fill_fails = True
            await hub_client(hub.hub).call(BROWSER_VAULT_ADD, _linkedin(hub.alice))
            async with turn_browser(hub, uuid4()) as browser:
                await browser.call(_FRAME, "goto", {"url": _SIGN_IN})
                return await _fill(browser, VaultField.PASSWORD)

    failed = asyncio.run(scenario())

    assert failed["error"] == {
        "error": {
            "name": "TimeoutError",
            "message": 'Timeout 5000ms exceeded.\nCall log:\n  - fill("[a vault value]")',
        }
    }


def test_a_fill_after_an_edit_puts_in_the_new_value_and_the_kept_ones(tmp_path):
    async def scenario() -> list[object]:
        async with browser_hub(tmp_path / "hub", _LIMITS) as hub:
            client = hub_client(hub.hub)
            await client.call(BROWSER_VAULT_ADD, _linkedin(hub.alice))
            await client.call(
                BROWSER_VAULT_EDIT,
                VaultEntryEditCommand.model_validate(
                    {"instance_id": hub.alice, "name": "LinkedIn", "password": "a new one"}
                ),
            )
            async with turn_browser(hub, uuid4()) as browser:
                await browser.call(_FRAME, "goto", {"url": _SIGN_IN})
                await _fill(browser, VaultField.USERNAME)
                await _fill(browser, VaultField.PASSWORD)
            return _filled(hub)

    assert asyncio.run(scenario()) == [_USERNAME, "a new one"]
