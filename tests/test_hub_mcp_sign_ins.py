"""Jorge signs in to a picked MCP server from the web app: the hub runs the sign-in, the server's
page sends the browser back to the hub's redirect route, and the hub keeps the sign-in
(ADR 0128)."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import aiohttp
import pytest
from mcp.shared.auth import OAuthClientInformationFull

from kinby.cli.client import ContractClient
from kinby.contracts import (
    HOME_SCOPES,
    INSTANCE_MCP_SIGN_IN_START,
    INSTANCE_MCP_SIGN_OUT,
    INSTANCE_PICKS_GET,
    INSTANCE_PICKS_SET,
    INSTANCE_SECRETS_SET,
    ErrorCode,
    ErrorEnvelope,
    InstanceMcpSignInStartCommand,
    InstanceMcpSignOutCommand,
    InstancePicksGetCommand,
    InstancePicksResult,
    InstancePicksSetCommand,
    InstanceSecretsSetCommand,
    LifecycleOperationResult,
    LoginState,
    McpServer,
    McpServerChoice,
    McpSignInSetup,
    McpSignInStartResult,
    Scope,
)
from kinby.hub import Hub
from kinby.hub.mcp_sign_ins import CALLBACK_PATH
from kinby.instance import Serve
from tests.mcp_oauth_server import OAuthMcpServer, oauth_mcp_server
from tests.test_hub import FakeControl, finished_operation, hub_at, hub_client, started_instance
from tests.test_hub_deletion import deleted, previewed
from tests.test_hub_home_reads import answers, error_code, result_of
from tests.test_hub_pick_sync import _update
from tests.test_hub_removal import removed
from tests.test_hub_routing import control_token
from tests.test_hub_server import served, url
from tests.test_hub_setup import setup_of

_PUBLIC_URL = "https://kinby.example.com"


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def _hub(tmp_path: Path, **options) -> Hub:
    return hub_at(tmp_path / "hub", control=FakeControl(), **options)


def _library_server(hub: Hub, name: str, entry: dict[str, object]) -> None:
    folder = hub.directory / "mcp-servers"
    folder.mkdir(exist_ok=True)
    server = McpServer.model_validate(
        {"entry": entry, "tool_states": dict.fromkeys(("look", "headers", "expire"), "loaded")}
    )
    (folder / f"{name}.json").write_text(server.model_dump_json(), encoding="utf-8")


async def _picked(
    hub: Hub, entry: dict[str, object], secrets: dict[str, str] | None = None
) -> UUID:
    """Start an instance that picks the server *entry* defines, as cockpit."""
    client = hub_client(hub)
    instance_id = (await started_instance(client, hub, secrets=secrets)).instance_id
    _library_server(hub, "cockpit", entry)
    await _pick(client, instance_id, McpServerChoice(name="cockpit"))
    return instance_id


async def _pick(client: ContractClient, instance_id: UUID, *choices: McpServerChoice) -> None:
    read = await client.call(INSTANCE_PICKS_GET, InstancePicksGetCommand(instance_id=instance_id))
    assert isinstance(read, InstancePicksResult), read
    picked = await client.call(
        INSTANCE_PICKS_SET,
        InstancePicksSetCommand(
            instance_id=instance_id, skills=[], hash=read.hash, mcp_servers=list(choices)
        ),
    )
    assert isinstance(picked, InstancePicksResult), picked


async def _start(
    client: ContractClient, instance_id: UUID, origin: str
) -> McpSignInStartResult | ErrorEnvelope:
    return await client.call(
        INSTANCE_MCP_SIGN_IN_START,
        InstanceMcpSignInStartCommand(instance_id=instance_id, server="cockpit", origin=origin),
    )


async def _approved(session: aiohttp.ClientSession, started: object) -> str:
    """Approve on the server's page, which sends the browser back at once: where it sends it."""
    assert isinstance(started, McpSignInStartResult), started
    async with session.get(started.authorization_url, allow_redirects=False) as page:
        assert page.status == 302, await page.text()
        return page.headers["Location"]


async def _landed(session: aiohttp.ClientSession, back: str) -> aiohttp.ClientResponse:
    async with session.get(back, allow_redirects=False) as landing:
        await landing.read()
        return landing


async def _signed_in(hub: Hub, instance_id: UUID, address: Serve) -> None:
    async with aiohttp.ClientSession() as session:
        started = await _start(hub_client(hub), instance_id, url(address, ""))
        landing = await _landed(session, await _approved(session, started))
    assert landing.status == 303


async def _sign_ins(hub: Hub, instance_id: UUID) -> list[McpSignInSetup]:
    return (await setup_of(hub, instance_id)).mcp_sign_ins


def _query(address: str) -> dict[str, str]:
    return {name: values[0] for name, values in parse_qs(urlsplit(address).query).items()}


def _hub_files(hub: Hub) -> bytes:
    return b"".join(path.read_bytes() for path in hub.directory.rglob("*") if path.is_file())


def test_jorge_approves_on_the_servers_page_and_lands_on_the_instance_signed_in(
    tmp_path, oauth_server: OAuthMcpServer
):
    async def scenario() -> None:
        hub = _hub(tmp_path)
        instance_id = await _picked(hub, {"url": oauth_server.url})
        async with served(hub) as address, aiohttp.ClientSession() as session:
            started = await _start(hub_client(hub), instance_id, url(address, ""))

            assert isinstance(started, McpSignInStartResult), started
            asked = _query(started.authorization_url)
            assert started.authorization_url.startswith(f"{oauth_server.issuer}/authorize?")
            assert asked["redirect_uri"] == url(address, CALLBACK_PATH)
            assert asked["resource"] == oauth_server.url
            assert asked["code_challenge_method"] == "S256"
            [client] = oauth_server.clients
            assert "refresh_token" in client.grant_types

            landing = await _landed(session, await _approved(session, started))

        assert (landing.status, landing.headers["Location"]) == (303, f"/instances/{instance_id}")
        assert await _sign_ins(hub, instance_id) == [
            McpSignInSetup(server="cockpit", state=LoginState.SIGNED_IN)
        ]
        read = await hub_client(hub).call(
            INSTANCE_PICKS_GET, InstancePicksGetCommand(instance_id=instance_id)
        )
        assert isinstance(read, InstancePicksResult)
        assert [server.sign_in for server in read.mcp_servers] == [LoginState.SIGNED_IN]
        files = _hub_files(hub)
        assert oauth_server.tokens()
        assert not [token for token in oauth_server.tokens() if token.encode() in files]

    asyncio.run(scenario())


def test_a_sign_in_link_completes_once_and_one_the_hub_never_issued_completes_nothing(
    tmp_path, oauth_server: OAuthMcpServer
):
    async def scenario() -> None:
        hub = _hub(tmp_path)
        instance_id = await _picked(hub, {"url": oauth_server.url})
        async with served(hub) as address, aiohttp.ClientSession() as session:
            forged = await _landed(session, url(address, f"{CALLBACK_PATH}?code=c&state=forged"))
            back = await _approved(
                session, await _start(hub_client(hub), instance_id, url(address, ""))
            )
            first = await _landed(session, back)
            again = await _landed(session, back)

        assert forged.status == 400
        assert first.status == 303
        assert again.status == 400

    asyncio.run(scenario())


def test_a_sign_in_nobody_finishes_within_ten_minutes_expires(
    tmp_path, oauth_server: OAuthMcpServer
):
    async def scenario() -> None:
        clock = _Clock()
        hub = _hub(tmp_path, clock=clock)
        instance_id = await _picked(hub, {"url": oauth_server.url})
        async with served(hub) as address, aiohttp.ClientSession() as session:
            back = await _approved(
                session, await _start(hub_client(hub), instance_id, url(address, ""))
            )
            clock.now += timedelta(minutes=10, seconds=1)

            late = await _landed(session, back)

        assert late.status == 400
        assert await _sign_ins(hub, instance_id) == [
            McpSignInSetup(server="cockpit", state=LoginState.PENDING)
        ]

    asyncio.run(scenario())


def test_a_refused_consent_fails_the_sign_in_with_the_servers_reason(
    tmp_path, oauth_server: OAuthMcpServer
):
    async def scenario() -> None:
        hub = _hub(tmp_path)
        instance_id = await _picked(hub, {"url": oauth_server.url})
        async with served(hub) as address, aiohttp.ClientSession() as session:
            started = await _start(hub_client(hub), instance_id, url(address, ""))
            assert isinstance(started, McpSignInStartResult), started
            state = _query(started.authorization_url)["state"]

            landing = await _landed(
                session,
                url(
                    address,
                    f"{CALLBACK_PATH}?state={state}&error=access_denied"
                    "&error_description=The+user+said+no",
                ),
            )

        assert (landing.status, landing.headers["Location"]) == (303, f"/instances/{instance_id}")
        assert await _sign_ins(hub, instance_id) == [
            McpSignInSetup(
                server="cockpit",
                state=LoginState.FAILED,
                detail="The server refused the sign-in: The user said no",
            )
        ]

    asyncio.run(scenario())


def test_a_server_with_no_way_to_register_kinby_fails_the_sign_in(tmp_path):
    async def scenario() -> None:
        hub = _hub(tmp_path)
        with oauth_mcp_server(registration=False) as closed:
            instance_id = await _picked(hub, {"url": closed.url})

            refused = await _start(hub_client(hub), instance_id, "http://127.0.0.1:8080")

        reason = (
            "The server offers no way to register kinby. "
            "Give its entry the oauth clientId the server knows kinby as."
        )
        assert isinstance(refused, ErrorEnvelope), refused
        assert (refused.code, refused.message) == (ErrorCode.INVALID_ARGUMENT, reason)
        assert await _sign_ins(hub, instance_id) == [
            McpSignInSetup(server="cockpit", state=LoginState.FAILED, detail=reason)
        ]

    asyncio.run(scenario())


def test_an_entrys_oauth_client_signs_in_in_place_of_registering(tmp_path):
    async def scenario() -> None:
        hub = _hub(tmp_path)
        with oauth_mcp_server(registration=False) as closed:
            instance_id = await _picked(
                hub,
                {
                    "url": closed.url,
                    "oauth": {
                        "clientId": "kinby",
                        "clientSecret": "${COCKPIT_SECRET}",
                        "scopes": ["tasks", "comments"],
                    },
                },
                secrets={"COCKPIT_SECRET": "s3cret"},
            )
            async with served(hub) as address, aiohttp.ClientSession() as session:
                closed.add_client(
                    OAuthClientInformationFull(
                        client_id="kinby",
                        client_secret="s3cret",
                        redirect_uris=[url(address, CALLBACK_PATH)],
                        scope="tasks comments",
                        token_endpoint_auth_method="client_secret_post",
                    )
                )
                started = await _start(hub_client(hub), instance_id, url(address, ""))

                assert isinstance(started, McpSignInStartResult), started
                assert _query(started.authorization_url)["scope"] == "tasks comments"
                landing = await _landed(session, await _approved(session, started))

            assert [client.client_id for client in closed.clients] == ["kinby"]
        assert landing.status == 303
        assert await _sign_ins(hub, instance_id) == [
            McpSignInSetup(server="cockpit", state=LoginState.SIGNED_IN)
        ]

    asyncio.run(scenario())


def test_the_server_sends_the_browser_back_under_the_hubs_public_url_when_it_has_one(
    tmp_path, oauth_server: OAuthMcpServer
):
    async def scenario() -> None:
        hub = _hub(tmp_path, public_url=_PUBLIC_URL)
        instance_id = await _picked(hub, {"url": oauth_server.url})

        started = await _start(hub_client(hub), instance_id, "http://127.0.0.1:5173")

        assert isinstance(started, McpSignInStartResult), started
        assert _query(started.authorization_url)["redirect_uri"] == _PUBLIC_URL + CALLBACK_PATH

    asyncio.run(scenario())


def test_signing_out_deletes_the_sign_in_and_sets_it_pending(
    tmp_path, oauth_server: OAuthMcpServer
):
    async def scenario() -> None:
        hub = _hub(tmp_path)
        instance_id = await _picked(hub, {"url": oauth_server.url})
        async with served(hub) as address:
            await _signed_in(hub, instance_id, address)

        signed_out = await hub_client(hub).call(
            INSTANCE_MCP_SIGN_OUT,
            InstanceMcpSignOutCommand(instance_id=instance_id, server="cockpit"),
        )

        pending = McpSignInSetup(server="cockpit", state=LoginState.PENDING)
        assert signed_out == pending
        assert await _sign_ins(hub, instance_id) == [pending]
        assert not [token for token in oauth_server.tokens() if token.encode() in _hub_files(hub)]

    asyncio.run(scenario())


def test_signing_out_or_dropping_the_pick_ends_a_sign_in_still_waiting_on_the_server(
    tmp_path, oauth_server: OAuthMcpServer
):
    async def scenario() -> None:
        hub = _hub(tmp_path)
        client = hub_client(hub)
        instance_id = await _picked(hub, {"url": oauth_server.url})
        async with served(hub) as address, aiohttp.ClientSession() as session:
            back = await _approved(session, await _start(client, instance_id, url(address, "")))
            await client.call(
                INSTANCE_MCP_SIGN_OUT,
                InstanceMcpSignOutCommand(instance_id=instance_id, server="cockpit"),
            )
            after_sign_out = await _landed(session, back)
            back = await _approved(session, await _start(client, instance_id, url(address, "")))
            await _pick(client, instance_id)
            await _pick(client, instance_id, McpServerChoice(name="cockpit"))
            after_drop = await _landed(session, back)

        assert (after_sign_out.status, after_drop.status) == (400, 400)
        assert await _sign_ins(hub, instance_id) == [
            McpSignInSetup(server="cockpit", state=LoginState.PENDING)
        ]

    asyncio.run(scenario())


def test_dropping_the_pick_and_deleting_the_instance_delete_the_sign_in(
    tmp_path, oauth_server: OAuthMcpServer
):
    async def scenario() -> None:
        hub = _hub(tmp_path)
        client = hub_client(hub)
        instance_id = await _picked(hub, {"url": oauth_server.url})
        async with served(hub) as address:
            await _signed_in(hub, instance_id, address)
            await _pick(client, instance_id)
            await _pick(client, instance_id, McpServerChoice(name="cockpit"))

            assert await _sign_ins(hub, instance_id) == [
                McpSignInSetup(server="cockpit", state=LoginState.PENDING)
            ]

            await _signed_in(hub, instance_id, address)
        stored = list(hub.directory.rglob(f"{instance_id}.mcp-sign-ins"))
        assert stored
        instance = LifecycleOperationResult(operation_id=instance_id, instance_id=instance_id)
        await removed(client, instance)
        await deleted(client, await previewed(client, instance))

        assert not [path for path in stored if path.exists()]

    asyncio.run(scenario())


def test_an_update_that_changes_the_servers_address_or_its_issuer_resets_the_sign_in(
    tmp_path, oauth_server: OAuthMcpServer
):
    hub = _hub(tmp_path)

    async def signed_in(entry: dict[str, object], secrets: dict[str, str]) -> UUID:
        instance_id = await _picked(hub, entry, secrets)
        async with served(hub) as address:
            await _signed_in(hub, instance_id, address)
        return instance_id

    def sign_ins(instance_id: UUID) -> list[McpSignInSetup]:
        _update(hub, hub.instances_directory / str(instance_id))
        return asyncio.run(_sign_ins(hub, instance_id))

    instance_id = asyncio.run(
        signed_in({"url": "${COCKPIT_URL}"}, {"COCKPIT_URL": oauth_server.url})
    )
    signed = McpSignInSetup(server="cockpit", state=LoginState.SIGNED_IN)
    pending = McpSignInSetup(server="cockpit", state=LoginState.PENDING)

    assert sign_ins(instance_id) == [signed]

    with oauth_mcp_server() as other:

        async def moved() -> None:
            replaced = await hub_client(hub).call(
                INSTANCE_SECRETS_SET,
                InstanceSecretsSetCommand(
                    instance_id=instance_id, secrets={"COCKPIT_URL": other.url}
                ),
            )
            assert isinstance(replaced, LifecycleOperationResult), replaced
            await finished_operation(hub_client(hub), replaced)

        asyncio.run(moved())

        assert sign_ins(instance_id) == [pending]

        async def signed_in_again() -> None:
            async with served(hub) as address:
                await _signed_in(hub, instance_id, address)

        asyncio.run(signed_in_again())
        other.named_issuer = oauth_server.issuer

        assert sign_ins(instance_id) == [pending]


def test_only_an_admin_session_starts_a_sign_in_or_signs_out(
    tmp_path, oauth_server: OAuthMcpServer
):
    async def scenario() -> None:
        hub = _hub(tmp_path)
        instance_id = await _picked(hub, {"url": oauth_server.url})
        home = hub_client(hub, set(HOME_SCOPES))

        started = await _start(home, instance_id, "http://127.0.0.1:8080")
        signed_out = await home.call(
            INSTANCE_MCP_SIGN_OUT,
            InstanceMcpSignOutCommand(instance_id=instance_id, server="cockpit"),
        )

        for refused in (started, signed_out):
            assert isinstance(refused, ErrorEnvelope), refused
            assert refused.code is ErrorCode.PERMISSION_DENIED

    asyncio.run(scenario())


async def _token(
    hub: Hub, address: Serve, instance_id: UUID, refused: str | None = None
) -> dict[str, object]:
    """Ask the instance's hub route for cockpit's access token, the server having refused
    *refused*."""
    (answer,) = await answers(
        url(address, f"/instances/{instance_id}/hub"),
        control_token(hub, str(instance_id)),
        ("mcp_sign_in.token", {"server": "cockpit", "refused": refused}),
    )
    return answer


def _access_token(answer: dict[str, object]) -> str:
    token = result_of(answer)["access_token"]
    assert isinstance(token, str)
    return token


def test_the_instances_hub_route_gives_its_own_sign_ins_access_token_and_no_others(
    tmp_path, oauth_server: OAuthMcpServer
):
    async def scenario() -> None:
        hub = _hub(tmp_path)
        alice = await _picked(hub, {"url": oauth_server.url})
        bob = (await started_instance(hub_client(hub), hub, name="bob")).instance_id
        await _pick(hub_client(hub), bob, McpServerChoice(name="cockpit"))
        async with served(hub) as address:
            await _signed_in(hub, alice, address)

            given = await _token(hub, address, alice)
            again = await _token(hub, address, alice)
            refused = await _token(hub, address, bob)

        assert Scope.MCP_TOKEN in hub.instance_scopes(alice)
        assert _access_token(given) in oauth_server.tokens()
        assert _access_token(again) == _access_token(given)
        assert error_code(refused) == ErrorCode.MCP_SIGN_IN_PENDING

    asyncio.run(scenario())


def test_the_hub_renews_an_access_token_close_to_expiring_or_one_the_server_refused(
    tmp_path, oauth_server: OAuthMcpServer
):
    async def scenario() -> None:
        clock = _Clock()
        hub = _hub(tmp_path, clock=clock)
        instance_id = await _picked(hub, {"url": oauth_server.url})
        async with served(hub) as address:
            await _signed_in(hub, instance_id, address)
            first = _access_token(await _token(hub, address, instance_id))
            clock.now += timedelta(minutes=58)
            renewed = _access_token(await _token(hub, address, instance_id))
            oauth_server.expire()
            refused = _access_token(await _token(hub, address, instance_id, refused=renewed))
            stale = _access_token(await _token(hub, address, instance_id, refused=renewed))

        assert len({first, renewed, refused}) == 3
        assert stale == refused
        assert await _sign_ins(hub, instance_id) == [
            McpSignInSetup(server="cockpit", state=LoginState.SIGNED_IN)
        ]

    asyncio.run(scenario())


def test_a_server_that_takes_its_client_only_by_a_basic_header_signs_in_and_renews(tmp_path):
    async def scenario() -> None:
        hub = _hub(tmp_path)
        with oauth_mcp_server(basic_auth=True) as server:
            instance_id = await _picked(hub, {"url": server.url})
            async with served(hub) as address:
                await _signed_in(hub, instance_id, address)
                first = _access_token(await _token(hub, address, instance_id))
                server.expire()
                renewed = _access_token(await _token(hub, address, instance_id, refused=first))

        assert renewed != first
        assert await _sign_ins(hub, instance_id) == [
            McpSignInSetup(server="cockpit", state=LoginState.SIGNED_IN)
        ]

    asyncio.run(scenario())


def test_a_refresh_token_the_server_refuses_sets_the_sign_in_pending(
    tmp_path, oauth_server: OAuthMcpServer
):
    async def scenario() -> None:
        hub = _hub(tmp_path)
        instance_id = await _picked(hub, {"url": oauth_server.url})
        async with served(hub) as address:
            await _signed_in(hub, instance_id, address)
            given = _access_token(await _token(hub, address, instance_id))
            oauth_server.revoke()

            refused = await _token(hub, address, instance_id, refused=given)
            again = await _token(hub, address, instance_id)

        assert error_code(refused) == ErrorCode.MCP_SIGN_IN_PENDING
        assert error_code(again) == ErrorCode.MCP_SIGN_IN_PENDING
        assert await _sign_ins(hub, instance_id) == [
            McpSignInSetup(server="cockpit", state=LoginState.PENDING)
        ]

    asyncio.run(scenario())


def test_the_hub_route_gives_no_token_for_a_server_the_instance_signs_in_to_nowhere(
    tmp_path, oauth_server: OAuthMcpServer
):
    async def scenario() -> None:
        hub = _hub(tmp_path)
        instance_id = (await started_instance(hub_client(hub), hub)).instance_id
        async with served(hub) as address:
            unpicked = await _token(hub, address, instance_id)

        assert error_code(unpicked) == ErrorCode.NOT_FOUND

    asyncio.run(scenario())


def test_a_sign_in_with_no_refresh_token_gives_its_access_token_until_it_runs_out(tmp_path):
    async def scenario() -> None:
        clock = _Clock()
        hub = _hub(tmp_path, clock=clock)
        with oauth_mcp_server(refresh_tokens=False) as server:
            instance_id = await _picked(hub, {"url": server.url})
            async with served(hub) as address:
                await _signed_in(hub, instance_id, address)
                first = _access_token(await _token(hub, address, instance_id))
                clock.now += timedelta(minutes=58)
                close = _access_token(await _token(hub, address, instance_id))
                clock.now += timedelta(minutes=3)
                ran_out = await _token(hub, address, instance_id)

        assert close == first
        assert error_code(ran_out) == ErrorCode.MCP_SIGN_IN_PENDING

    asyncio.run(scenario())


@pytest.mark.parametrize("refused", [False, True])
def test_signing_in_again_while_a_renewal_runs_keeps_the_new_sign_in(
    tmp_path, oauth_server: OAuthMcpServer, refused: bool
):
    async def scenario() -> None:
        hub = _hub(tmp_path)
        instance_id = await _picked(hub, {"url": oauth_server.url})
        async with served(hub) as address:
            await _signed_in(hub, instance_id, address)
            given = _access_token(await _token(hub, address, instance_id))
            if refused:
                oauth_server.revoke()
            before = set(oauth_server.tokens())
            refreshing = oauth_server.hold_refreshes()
            renewal = asyncio.create_task(_token(hub, address, instance_id, refused=given))
            assert await asyncio.to_thread(refreshing.wait, 5)

            await _signed_in(hub, instance_id, address)
            signed_in_again = set(oauth_server.tokens()) - before
            oauth_server.release_refreshes()
            await renewal
            kept = _access_token(await _token(hub, address, instance_id))

        assert kept in signed_in_again
        assert await _sign_ins(hub, instance_id) == [
            McpSignInSetup(server="cockpit", state=LoginState.SIGNED_IN)
        ]

    asyncio.run(scenario())
