"""Title a new thread with the recap model from its first message, off its first turn's path."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Protocol, cast
from uuid import UUID

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from kinby.contracts import ThreadTitled
from kinby.core.errors import ModelNoResponse
from kinby.core.model_calls import completed_model_call
from kinby.core.models import init_model
from kinby.core.threads import ThreadStore, first_message_title
from kinby.instance import Instance, ModelName, RecapPolicy, reload_manifest

if TYPE_CHECKING:
    from kinby.core.events import EventLog

logger = logging.getLogger(__name__)
#: How much of the first message the model reads. Its opening is enough to name a thread.
_PROMPT_CHARACTERS = 2_000


class TitleModel(Protocol):
    async def ainvoke(self, messages: Sequence[BaseMessage], /) -> AIMessage: ...


type TitleModelFactory = Callable[[ModelName], TitleModel]


def _init_model(model: ModelName) -> TitleModel:
    return cast(TitleModel, init_model(model))


class TitleWriter:
    """Ask the recap model for a new thread's title while its first turn runs."""

    def __init__(
        self,
        store: ThreadStore,
        event_log: EventLog,
        instance: Instance,
        *,
        model_factory: TitleModelFactory = _init_model,
        model_override: str | None = None,
    ) -> None:
        self._store = store
        self._event_log = event_log
        self._instance = instance
        self._model_factory = model_factory
        self._model_override = model_override
        self._writing: set[asyncio.Task[None]] = set()

    def schedule(self, thread_id: UUID, turn_id: UUID, message: str) -> None:
        """Title the thread from *message*, its first turn's, without waiting for the title."""
        task = asyncio.create_task(self._title(thread_id, turn_id, message))
        self._writing.add(task)
        task.add_done_callback(self._writing.discard)

    async def drain(self) -> None:
        """Wait until every title being written has landed."""
        await asyncio.gather(*self._writing)

    async def _title(self, thread_id: UUID, turn_id: UUID, message: str) -> None:
        try:
            titled = await self._write(message)
        except Exception:
            logger.warning("The recap model could not title thread %s.", thread_id, exc_info=True)
            return
        if titled is None:
            return
        thread = self._store.thread(thread_id)
        if thread is not None and thread.title == first_message_title(message):
            self._store.rename(thread_id, titled.title)
        await self._event_log.append(thread_id, turn_id, titled)

    async def _write(self, message: str) -> ThreadTitled | None:
        """The recap model's title for *message*, or None when the instance recaps trace-only."""
        manifest = reload_manifest(self._instance, model_override=self._model_override)
        if manifest.memory.recap is RecapPolicy.TRACE_ONLY:
            return None
        model = manifest.models.recap
        started_at = asyncio.get_running_loop().time()
        answer = await self._model_factory(model).ainvoke((HumanMessage(_prompt(message)),))
        title = first_message_title(answer.text)
        if not title:
            raise ModelNoResponse("The model returned no title.")
        completed = completed_model_call(
            model,
            answer.usage_metadata,
            started_at=started_at,
            completed_at=asyncio.get_running_loop().time(),
        )
        return ThreadTitled(
            title=title,
            model=model,
            input_tokens=completed.input_tokens,
            output_tokens=completed.output_tokens,
            cache_read_tokens=completed.cache_read_tokens,
            cache_creation_tokens=completed.cache_creation_tokens,
        )


def _prompt(message: str) -> str:
    return (
        "Write a title of a few words for a conversation that opens with the message below. "
        "Name what the conversation is about, in the message's language. "
        "Answer with the title alone, without quotes.\n\n"
        f"<message>\n{message[:_PROMPT_CHARACTERS]}\n</message>"
    )
