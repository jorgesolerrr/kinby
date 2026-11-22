"""Land a file the user gives a turn in the workspace, under the day's uploads folder, and send
the model a turn's uploads: named on its user message, an image also seen (ADR 0132)."""

from __future__ import annotations

import asyncio
import base64
import io
import re
from collections.abc import Callable, Sequence
from datetime import datetime
from itertools import chain, count
from pathlib import Path

from langchain_core.messages import AnyMessage, HumanMessage
from langchain_core.messages.content import ImageContentBlock
from PIL import Image

from kinby.contracts import UploadPath, UploadPutCommand, UploadPutResult
from kinby.core.prompt import render_uploads

_UPLOADS = "uploads"
#: What a file name may not hold on any system the workspace may sit on.
_UNSAFE = re.compile(r'[\x00-\x1f<>:"/\\|?*]')
#: The uploads the model sees, by extension.
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})
#: The media type of each image format the model takes, by Pillow's name for the format.
_MEDIA_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "GIF": "image/gif"}
#: The longest edge an image is sent at: Anthropic's limit once a request holds over 20 images.
_SENT_EDGE = 2000
#: The bytes an image is sent at, so that its base64 fits Anthropic's 5 MB.
_SENT_BYTES = 5 * 1024 * 1024 * 3 // 4


class InstanceUploads:
    def __init__(self, workspace: Path, clock: Callable[[], datetime]) -> None:
        self._workspace = workspace
        self._clock = clock

    async def put(self, command: UploadPutCommand) -> UploadPutResult:
        folder = self._workspace / _UPLOADS / self._clock().date().isoformat()
        path = await asyncio.to_thread(_write, folder, _safe_name(command.name), command.content)
        return UploadPutResult(path=UploadPath(path.relative_to(self._workspace).as_posix()))


def names_upload(workspace: Path, path: UploadPath) -> bool:
    """Whether *path* is a file under the workspace's uploads."""
    landed = (workspace / path).resolve()
    return landed.is_relative_to((workspace / _UPLOADS).resolve()) and landed.is_file()


def _safe_name(name: str) -> str:
    """*name*'s last part, with what a path can't hold replaced."""
    safe = _UNSAFE.sub("_", name.replace("\\", "/").rsplit("/", 1)[-1]).strip()
    return safe if safe.strip(".") else "upload"


def _write(folder: Path, name: str, content: bytes) -> Path:
    """Write *content* as *name* in *folder*, numbered before its extension past any file
    already there."""
    folder.mkdir(parents=True, exist_ok=True)
    stem, suffix = Path(name).stem, Path(name).suffix
    names = chain((name,), (f"{stem} ({number}){suffix}" for number in count(2)))
    while True:
        path = folder / next(names)
        try:
            with path.open("xb") as file:
                file.write(content)
        except FileExistsError:
            continue
        return path


def user_message(text: str, uploads: Sequence[UploadPath]) -> HumanMessage:
    """The user message of a turn that says *text* and names *uploads*, kept as they are so each
    send reads them afresh."""
    return HumanMessage(
        content=text, additional_kwargs={_UPLOADS: list(uploads)} if uploads else {}
    )


def named_uploads(message: HumanMessage) -> list[UploadPath]:
    """The uploads *message* names."""
    return message.additional_kwargs.get(_UPLOADS, [])


def sent_uploads(messages: Sequence[AnyMessage], workspace: Path) -> list[AnyMessage]:
    """*messages* with each user message's uploads as the model is sent them: named after its
    text, those gone from *workspace* as gone, and each image read now as an image block."""
    return [
        _with_uploads(message, uploads, workspace)
        if isinstance(message, HumanMessage) and (uploads := named_uploads(message))
        else message
        for message in messages
    ]


def _with_uploads(
    message: HumanMessage, uploads: Sequence[UploadPath], workspace: Path
) -> HumanMessage:
    gone = {path for path in uploads if not names_upload(workspace, path)}
    images = [
        block
        for path in uploads
        if path not in gone and (block := _image_block(workspace / path)) is not None
    ]
    text = render_uploads(message.text, uploads, gone)
    return message.model_copy(update={"content": [*images, {"type": "text", "text": text}]})


def _image_block(path: Path) -> ImageContentBlock | None:
    """The image at *path* as the model is sent it, or None for a file that is no image the
    model takes, whatever its extension says."""
    if path.suffix.lower() not in _IMAGE_SUFFIXES:
        return None
    try:
        content = path.read_bytes()
        with Image.open(io.BytesIO(content)) as image:
            # Pillow decodes lazily, so a truncated image fails only once it loads.
            image.load()
            mime_type = _MEDIA_TYPES.get(image.format or "")
            sent = _scaled(image, content)
    except OSError, Image.DecompressionBombError:
        return None
    if mime_type is None:
        return None
    return {"type": "image", "base64": base64.b64encode(sent).decode(), "mime_type": mime_type}


def _scaled(image: Image.Image, content: bytes) -> bytes:
    """*content*, the encoded *image*, scaled down in its own format until it fits what the
    model takes."""
    edge = max(image.size)
    while edge > _SENT_EDGE or len(content) > _SENT_BYTES:
        edge = _SENT_EDGE if edge > _SENT_EDGE else edge * 3 // 4
        scaled = image.copy()
        scaled.thumbnail((edge, edge))
        out = io.BytesIO()
        scaled.save(out, format=image.format)
        content = out.getvalue()
    return content
