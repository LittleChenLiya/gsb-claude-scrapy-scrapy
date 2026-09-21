"""
Scheduler queues
"""

from __future__ import annotations

import json
import logging
import marshal
import os
import pickle
import struct
import zlib
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any

from queuelib import queue

from scrapy.utils.request import request_from_dict

if TYPE_CHECKING:
    from collections.abc import Callable
    from os import PathLike

    # typing.Self requires Python 3.11
    from typing_extensions import Self

    from scrapy import Request
    from scrapy.crawler import Crawler

logger = logging.getLogger(__name__)


def _with_mkdir(queue_class: type[queue.BaseQueue]) -> type[queue.BaseQueue]:
    class DirectoriesCreated(queue_class):  # type: ignore[valid-type,misc]
        def __init__(self, path: str | PathLike[str], *args: Any, **kwargs: Any):
            dirname = Path(path).parent
            if not dirname.exists():
                dirname.mkdir(parents=True, exist_ok=True)
            super().__init__(path, *args, **kwargs)

    return DirectoriesCreated


def _serializable_queue(
    queue_class: type[queue.BaseQueue],
    serialize: Callable[[Any], bytes],
    deserialize: Callable[[bytes], Any],
) -> type[queue.BaseQueue]:
    class SerializableQueue(queue_class):  # type: ignore[valid-type,misc]
        def push(self, obj: Any) -> None:
            s = serialize(obj)
            super().push(s)

        def pop(self) -> Any | None:
            s = super().pop()
            if s:
                return deserialize(s)
            return None

        def peek(self) -> Any | None:
            """Returns the next object to be returned by :meth:`pop`,
            but without removing it from the queue.

            Raises :exc:`NotImplementedError` if the underlying queue class does
            not implement a ``peek`` method, which is optional for queues.
            """
            try:
                s = super().peek()
            except AttributeError as ex:
                raise NotImplementedError(
                    "The underlying queue class does not implement 'peek'"
                ) from ex
            if s:
                return deserialize(s)
            return None

    return SerializableQueue


def _scrapy_serialization_queue(
    queue_class: type[queue.BaseQueue],
) -> type[queue.BaseQueue]:
    class ScrapyRequestQueue(queue_class):  # type: ignore[valid-type,misc]
        def __init__(self, crawler: Crawler, key: str):
            self.spider = crawler.spider
            super().__init__(key)

        @classmethod
        def from_crawler(
            cls, crawler: Crawler, key: str, *args: Any, **kwargs: Any
        ) -> Self:
            return cls(crawler, key)

        def push(self, request: Request) -> None:
            request_dict = request.to_dict(spider=self.spider)
            super().push(request_dict)

        def pop(self) -> Request | None:
            request = super().pop()
            if not request:
                return None
            return request_from_dict(request, spider=self.spider)

        def peek(self) -> Request | None:
            """Returns the next object to be returned by :meth:`pop`,
            but without removing it from the queue.

            Raises :exc:`NotImplementedError` if the underlying queue class does
            not implement a ``peek`` method, which is optional for queues.
            """
            request = super().peek()
            if not request:
                return None
            return request_from_dict(request, spider=self.spider)

    return ScrapyRequestQueue


def _scrapy_non_serialization_queue(
    queue_class: type[queue.BaseQueue],
) -> type[queue.BaseQueue]:
    class ScrapyRequestQueue(queue_class):  # type: ignore[valid-type,misc]
        @classmethod
        def from_crawler(cls, crawler: Crawler, *args: Any, **kwargs: Any) -> Self:
            return cls()

        def peek(self) -> Any | None:
            """Returns the next object to be returned by :meth:`pop`,
            but without removing it from the queue.

            Raises :exc:`NotImplementedError` if the underlying queue class does
            not implement a ``peek`` method, which is optional for queues.
            """
            try:
                s = super().peek()
            except AttributeError as ex:
                raise NotImplementedError(
                    "The underlying queue class does not implement 'peek'"
                ) from ex
            return s

    return ScrapyRequestQueue


def _pickle_serialize(obj: Any) -> bytes:
    try:
        return pickle.dumps(obj, protocol=4)
    # Both pickle.PicklingError and AttributeError can be raised by pickle.dump(s)
    # TypeError is raised from parsel.Selector
    except (pickle.PicklingError, AttributeError, TypeError) as e:
        raise ValueError(str(e)) from e


# queue.*Queue aren't subclasses of queue.BaseQueue
_PickleFifoSerializationDiskQueue = _serializable_queue(
    _with_mkdir(queue.FifoDiskQueue),  # type: ignore[arg-type]
    _pickle_serialize,
    pickle.loads,
)
_PickleLifoSerializationDiskQueue = _serializable_queue(
    _with_mkdir(queue.LifoDiskQueue),  # type: ignore[arg-type]
    _pickle_serialize,
    pickle.loads,
)
_MarshalFifoSerializationDiskQueue = _serializable_queue(
    _with_mkdir(queue.FifoDiskQueue),  # type: ignore[arg-type]
    marshal.dumps,
    marshal.loads,
)
_MarshalLifoSerializationDiskQueue = _serializable_queue(
    _with_mkdir(queue.LifoDiskQueue),  # type: ignore[arg-type]
    marshal.dumps,
    marshal.loads,
)
_PickleFifoSerializationSQLiteQueue = _serializable_queue(
    _with_mkdir(queue.FifoSQLiteQueue),  # type: ignore[arg-type]
    _pickle_serialize,
    pickle.loads,
)
_PickleLifoSerializationSQLiteQueue = _serializable_queue(
    _with_mkdir(queue.LifoSQLiteQueue),  # type: ignore[arg-type]
    _pickle_serialize,
    pickle.loads,
)
_MarshalFifoSerializationSQLiteQueue = _serializable_queue(
    _with_mkdir(queue.FifoSQLiteQueue),  # type: ignore[arg-type]
    marshal.dumps,
    marshal.loads,
)
_MarshalLifoSerializationSQLiteQueue = _serializable_queue(
    _with_mkdir(queue.LifoSQLiteQueue),  # type: ignore[arg-type]
    marshal.dumps,
    marshal.loads,
)

# public queue classes
PickleFifoDiskQueue = _scrapy_serialization_queue(_PickleFifoSerializationDiskQueue)
PickleLifoDiskQueue = _scrapy_serialization_queue(_PickleLifoSerializationDiskQueue)
MarshalFifoDiskQueue = _scrapy_serialization_queue(_MarshalFifoSerializationDiskQueue)
MarshalLifoDiskQueue = _scrapy_serialization_queue(_MarshalLifoSerializationDiskQueue)
PickleFifoSQLiteQueue = _scrapy_serialization_queue(_PickleFifoSerializationSQLiteQueue)
PickleLifoSQLiteQueue = _scrapy_serialization_queue(_PickleLifoSerializationSQLiteQueue)
MarshalFifoSQLiteQueue = _scrapy_serialization_queue(
    _MarshalFifoSerializationSQLiteQueue
)
MarshalLifoSQLiteQueue = _scrapy_serialization_queue(
    _MarshalLifoSerializationSQLiteQueue
)
FifoMemoryQueue = _scrapy_non_serialization_queue(queue.FifoMemoryQueue)  # type: ignore[arg-type]
LifoMemoryQueue = _scrapy_non_serialization_queue(queue.LifoMemoryQueue)  # type: ignore[arg-type]


_FRAME_HEADER = struct.Struct(">cI")
_FRAME_CRC = struct.Struct(">I")
_FRAME_PUSH = b"+"
_FRAME_POP = b"-"
# Frames claiming a larger payload are considered corrupted without reading
# them, to keep journal recovery linear on garbage input.
_MAX_FRAME_PAYLOAD = 64 * 1024 * 1024


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    tmp_path = path.with_name(path.name + ".tmp")
    with tmp_path.open("wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    tmp_path.replace(path)


def _encode_frame(kind: bytes, payload: bytes) -> bytes:
    frame = _FRAME_HEADER.pack(kind, len(payload)) + payload
    return frame + _FRAME_CRC.pack(zlib.crc32(frame))


class _ResumableDiskQueue:
    """A disk queue that recovers from process crashes and partial writes.

    Pushes and pops are appended to a journal file as length-prefixed,
    CRC32-protected frames. When the queue is opened, the journal is
    replayed; corrupted or truncated frames are skipped, so a crash or a
    partial write does not lose the remaining confirmed items. The journal
    is compacted atomically on close and periodically while popping, and
    the queue statistics (total pushes and pops) are saved atomically at
    the same time.
    """

    _journal_name = "journal.dat"
    _stats_name = "stats.json"

    def __init__(self, path: str | PathLike[str], *, fifo: bool = True):
        self._fifo = fifo
        self._path = Path(path)
        self._path.mkdir(parents=True, exist_ok=True)
        self._items: deque[bytes] = deque()
        self._pushed = 0
        self._popped = 0
        self._uncompacted_pops = 0
        self._closed = False
        self._load_stats()
        self._load_journal()
        self._journal_file = (self._path / self._journal_name).open("ab")

    def _load_stats(self) -> None:
        stats_path = self._path / self._stats_name
        if not stats_path.exists():
            return
        try:
            stats = json.loads(stats_path.read_text(encoding="utf-8"))
            self._pushed = int(stats["pushed"])
            self._popped = int(stats["popped"])
        except (OSError, ValueError, KeyError, TypeError) as e:
            logger.warning(f"Ignoring corrupted queue stats file {stats_path}: {e}")

    def _load_journal(self) -> None:
        journal_path = self._path / self._journal_name
        if not journal_path.exists():
            return
        data = journal_path.read_bytes()
        offset = 0
        skipped = 0
        while offset + _FRAME_HEADER.size <= len(data):
            frame = self._parse_frame(data, offset)
            if frame is None:
                skipped += 1
                offset += 1
                continue
            kind, payload, frame_size = frame
            self._apply(kind, payload)
            offset += frame_size
        skipped += len(data) - offset
        if skipped:
            logger.warning(
                f"Skipped {skipped} corrupted bytes while recovering queue"
                f" journal {journal_path}"
            )

    def _parse_frame(self, data: bytes, offset: int) -> tuple[bytes, bytes, int] | None:
        kind, length = _FRAME_HEADER.unpack_from(data, offset)
        if kind not in (_FRAME_PUSH, _FRAME_POP) or length > _MAX_FRAME_PAYLOAD:
            return None
        end = offset + _FRAME_HEADER.size + length + _FRAME_CRC.size
        if end > len(data):
            return None
        (crc,) = _FRAME_CRC.unpack_from(data, end - _FRAME_CRC.size)
        if crc != zlib.crc32(data[offset : end - _FRAME_CRC.size]):
            return None
        payload = data[offset + _FRAME_HEADER.size : end - _FRAME_CRC.size]
        return kind, payload, end - offset

    def _apply(self, kind: bytes, payload: bytes) -> None:
        if kind == _FRAME_PUSH:
            self._items.append(payload)
        elif self._items:
            if self._fifo:
                self._items.popleft()
            else:
                self._items.pop()

    def push(self, item: bytes) -> None:
        self._journal_file.write(_encode_frame(_FRAME_PUSH, item))
        self._journal_file.flush()
        self._items.append(item)
        self._pushed += 1

    def pop(self) -> bytes | None:
        if not self._items:
            return None
        item = self._items.popleft() if self._fifo else self._items.pop()
        self._journal_file.write(_encode_frame(_FRAME_POP, b""))
        self._journal_file.flush()
        self._popped += 1
        self._uncompacted_pops += 1
        if self._uncompacted_pops >= max(1024, len(self._items)):
            self._compact()
        return item

    def peek(self) -> bytes | None:
        if not self._items:
            return None
        return self._items[0] if self._fifo else self._items[-1]

    def _compact(self) -> None:
        self._journal_file.close()
        stats = json.dumps({"pushed": self._pushed, "popped": self._popped})
        _atomic_write_bytes(self._path / self._stats_name, stats.encode())
        journal = b"".join(_encode_frame(_FRAME_PUSH, item) for item in self._items)
        _atomic_write_bytes(self._path / self._journal_name, journal)
        self._journal_file = (self._path / self._journal_name).open("ab")
        self._uncompacted_pops = 0

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._compact()
        self._journal_file.close()

    def __len__(self) -> int:
        return len(self._items)


class _ResumableFifoDiskQueue(_ResumableDiskQueue):
    def __init__(self, path: str | PathLike[str]):
        super().__init__(path, fifo=True)


class _ResumableLifoDiskQueue(_ResumableDiskQueue):
    def __init__(self, path: str | PathLike[str]):
        super().__init__(path, fifo=False)


_PickleResumableFifoSerializationDiskQueue = _serializable_queue(
    _ResumableFifoDiskQueue,  # type: ignore[arg-type]
    _pickle_serialize,
    pickle.loads,
)
_PickleResumableLifoSerializationDiskQueue = _serializable_queue(
    _ResumableLifoDiskQueue,  # type: ignore[arg-type]
    _pickle_serialize,
    pickle.loads,
)

# public queue classes with crash recovery
PickleResumableFifoDiskQueue = _scrapy_serialization_queue(
    _PickleResumableFifoSerializationDiskQueue
)
PickleResumableLifoDiskQueue = _scrapy_serialization_queue(
    _PickleResumableLifoSerializationDiskQueue
)
