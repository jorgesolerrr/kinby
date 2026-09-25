"""The hub adds up usage across its running instances and stores none of it."""

import asyncio
from datetime import UTC, date, datetime

from kinby.contracts import (
    STATS_SUMMARY,
    ApiUse,
    ErrorEnvelope,
    MemoryCallCounts,
    PlanLimit,
    PlanWindow,
    Scope,
    StatsBucket,
    StatsBucketSize,
    StatsGetCommand,
    StatsGetResult,
    StatsSummary,
    SubscriptionUse,
    UsageSource,
)
from kinby.core.dispatcher import build_dispatcher
from kinby.hub import ControlEndpoint, HttpInstanceControl
from tests.test_contract_server import TOKEN, served_dispatcher
from tests.test_hub import (
    FakeControl,
    created_instance,
    hub_at,
    hub_client,
    started_instance,
)

_DAY = date(2026, 9, 25)


def _used(
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost: float | None = None,
    claude: SubscriptionUse | None = None,
) -> StatsSummary:
    return StatsSummary(
        completed=1,
        failed=0,
        interrupted=0,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        recap_input_tokens=0,
        recap_output_tokens=0,
        cost=cost,
        tool_calls={},
        memory_calls=MemoryCallCounts(),
        turns_without_memory=0,
        approvals_requested=0,
        mean_duration_seconds=None,
        good_ratings=0,
        bad_ratings=0,
        subscriptions=[
            claude or SubscriptionUse(usage_source=UsageSource.CLAUDE_SUBSCRIPTION),
            SubscriptionUse(usage_source=UsageSource.CHATGPT_SUBSCRIPTION),
        ],
    )


def _answer(total: StatsSummary, limits: list[PlanLimit] | None = None) -> StatsGetResult:
    return StatsGetResult(
        records=[],
        buckets=[StatsBucket(start=_DAY, **total.model_dump())],
        total=total,
        plan_windows=[],
        limits=limits or [],
        unpriced_models=[],
    )


def _address(instance_id: object) -> str:
    return f"http://kinby-{instance_id}:8787"


def test_the_hub_adds_up_usage_across_two_running_instances(tmp_path):
    async def scenario() -> None:
        control = FakeControl()
        hub = hub_at(tmp_path / "hub", control=control)
        client = hub_client(hub)
        first = await started_instance(client, hub)
        second = await started_instance(client, hub)
        first_answer = _answer(
            _used(
                input_tokens=100,
                output_tokens=20,
                cost=0.5,
                claude=SubscriptionUse(
                    usage_source=UsageSource.CLAUDE_SUBSCRIPTION,
                    runs=1,
                    input_tokens=1000,
                    output_tokens=200,
                    cache_read_tokens=800,
                    duration_ms=60_000,
                ),
            )
        )
        second_answer = _answer(
            _used(
                input_tokens=50,
                output_tokens=10,
                cost=0.25,
                claude=SubscriptionUse(
                    usage_source=UsageSource.CLAUDE_SUBSCRIPTION,
                    runs=2,
                    input_tokens=3000,
                    output_tokens=400,
                    cache_read_tokens=2000,
                    duration_ms=120_000,
                ),
            )
        )
        control.usage[_address(first.instance_id)] = first_answer
        control.usage[_address(second.instance_id)] = second_answer
        asked = StatsGetCommand(
            since=datetime(2026, 9, 25, 10, tzinfo=UTC),
            until=datetime(2026, 9, 25, 15, tzinfo=UTC),
            by=StatsBucketSize.WEEK,
        )

        summary = await hub_client(hub, {Scope.HUB_READ}).call(STATS_SUMMARY, asked)

        assert not isinstance(summary, ErrorEnvelope)
        assert control.stats_asked == [asked, asked]
        assert summary.buckets == {
            first.instance_id: first_answer.buckets,
            second.instance_id: second_answer.buckets,
        }
        assert summary.api == ApiUse(input_tokens=150, output_tokens=30, cost=0.75)
        assert summary.subscriptions == [
            SubscriptionUse(
                usage_source=UsageSource.CLAUDE_SUBSCRIPTION,
                runs=3,
                input_tokens=4000,
                output_tokens=600,
                cache_read_tokens=2800,
                duration_ms=180_000,
            ),
            SubscriptionUse(usage_source=UsageSource.CHATGPT_SUBSCRIPTION),
        ]
        assert summary.limits == []
        assert summary.skipped == []
        assert summary.unreachable == []

    asyncio.run(scenario())


def test_a_stopped_instance_is_skipped_and_named(tmp_path):
    async def scenario() -> None:
        control = FakeControl()
        hub = hub_at(tmp_path / "hub", control=control)
        client = hub_client(hub)
        running = await started_instance(client, hub)
        stopped = await created_instance(client)
        answer = _answer(_used(input_tokens=100, output_tokens=20, cost=0.5))
        control.usage[_address(running.instance_id)] = answer

        summary = await client.call(STATS_SUMMARY, StatsGetCommand())

        assert not isinstance(summary, ErrorEnvelope)
        assert len(control.stats_asked) == 1
        assert summary.buckets == {running.instance_id: answer.buckets}
        assert summary.api == ApiUse(input_tokens=100, output_tokens=20, cost=0.5)
        assert summary.skipped == [stopped.instance_id]
        assert summary.unreachable == []

    asyncio.run(scenario())


def test_an_instance_that_does_not_answer_in_time_is_unreachable_and_the_rest_count(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("kinby.hub.service.STATS_SECONDS", 0.05)

    async def scenario() -> None:
        control = FakeControl()
        hub = hub_at(tmp_path / "hub", control=control)
        client = hub_client(hub)
        answering = await started_instance(client, hub)
        silent = await started_instance(client, hub)
        answer = _answer(_used(input_tokens=100, output_tokens=20, cost=0.5))
        control.usage[_address(answering.instance_id)] = answer

        summary = await client.call(STATS_SUMMARY, StatsGetCommand())

        assert not isinstance(summary, ErrorEnvelope)
        assert summary.buckets == {answering.instance_id: answer.buckets}
        assert summary.api == ApiUse(input_tokens=100, output_tokens=20, cost=0.5)
        assert summary.skipped == []
        assert summary.unreachable == [silent.instance_id]

    asyncio.run(scenario())


def test_limits_merge_to_the_latest_reset_per_usage_source(tmp_path):
    async def scenario() -> None:
        control = FakeControl()
        hub = hub_at(tmp_path / "hub", control=control)
        client = hub_client(hub)
        first = await started_instance(client, hub)
        second = await started_instance(client, hub)
        control.usage[_address(first.instance_id)] = _answer(
            _used(),
            [
                PlanLimit(
                    usage_source=UsageSource.CLAUDE_SUBSCRIPTION,
                    resets_at=datetime(2026, 9, 25, 20, 40, tzinfo=UTC),
                ),
                PlanLimit(
                    usage_source=UsageSource.CHATGPT_SUBSCRIPTION,
                    resets_at=datetime(2026, 9, 25, 16, tzinfo=UTC),
                ),
            ],
        )
        control.usage[_address(second.instance_id)] = _answer(
            _used(),
            [
                PlanLimit(
                    usage_source=UsageSource.CLAUDE_SUBSCRIPTION,
                    resets_at=datetime(2026, 9, 25, 18, 40, tzinfo=UTC),
                ),
            ],
        )

        summary = await client.call(STATS_SUMMARY, StatsGetCommand())

        assert not isinstance(summary, ErrorEnvelope)
        assert summary.limits == [
            PlanLimit(
                usage_source=UsageSource.CLAUDE_SUBSCRIPTION,
                resets_at=datetime(2026, 9, 25, 20, 40, tzinfo=UTC),
            ),
            PlanLimit(
                usage_source=UsageSource.CHATGPT_SUBSCRIPTION,
                resets_at=datetime(2026, 9, 25, 16, tzinfo=UTC),
            ),
        ]

    asyncio.run(scenario())


def test_the_hub_reads_an_instance_s_stats_over_its_control_route(tmp_path):
    async def scenario() -> StatsGetResult:
        async with served_dispatcher(build_dispatcher(tmp_path)) as address:
            return await HttpInstanceControl().stats(
                ControlEndpoint(address=f"http://{address.host}:{address.port}", token=TOKEN),
                StatsGetCommand(by=StatsBucketSize.WEEK),
            )

    read = asyncio.run(scenario())

    assert read.records == []
    assert read.buckets == []
    assert read.plan_windows == [
        PlanWindow(usage_source=UsageSource.CLAUDE_SUBSCRIPTION, duration_seconds=18_000),
        PlanWindow(usage_source=UsageSource.CLAUDE_SUBSCRIPTION, duration_seconds=604_800),
        PlanWindow(usage_source=UsageSource.CHATGPT_SUBSCRIPTION, duration_seconds=18_000),
        PlanWindow(usage_source=UsageSource.CHATGPT_SUBSCRIPTION, duration_seconds=604_800),
    ]
