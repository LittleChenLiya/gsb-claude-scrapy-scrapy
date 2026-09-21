"""
Scheduler queues
"""

from __future__ import annotations

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

_JOURNAL_MAGIC = b"SCRQ"
_RECORD_PUSH = 1
_RECORD_POP = 2
_RECORD_HEADER = struct.Struct(">4sBI")  # magic, record type, payload length
_RECORD_CRC = struct.Struct(">I")


class _ResumableJournalQueue:
    """A disk queue backed by an append-only journal of checksummed records.

    Every :meth:`push` appends a push record and every :meth:`pop`
    appends a pop (tombstone) record; both are flushed and fsynced before the
    call returns, so a confirmed operation survives a process crash. On open,
    the journal is replayed: corrupted or partially written records are
    skipped and the file is truncated after the last valid record, while
    requests that were pushed and never popped stay pending. :meth:`close`
    compacts the journal, atomically replacing it with only the live records.
    """

    _lifo = False

    def __init__(self, path: str | PathLike[str]):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._items: deque[bytes] = deque()
        self._replay()
        self._file = self._path.open("ab")

    @staticmethod
    def _encode(record_type: int, payload: bytes) -> bytes:
        header = _RECORD_HEADER.pack(_JOURNAL_MAGIC, record_type, len(payload))
        crc = _RECORD_CRC.pack(zlib.crc32(header[4:] + payload))
        return header + payload + crc

    @staticmethod
    def _decode(data: bytes, pos: int) -> tuple[int, bytes, int] | None:
        """Read the record at *pos*, returning its type, payload and the
        offset of the next record, or None if it is invalid or incomplete."""
        if pos + _RECORD_HEADER.size > len(data):
            return None
        magic, record_type, length = _RECORD_HEADER.unpack_from(data, pos)
        end = pos + _RECORD_HEADER.size + length + _RECORD_CRC.size
        if (
            magic != _JOURNAL_MAGIC
            or record_type not in (_RECORD_PUSH, _RECORD_POP)
            or end > len(data)
        ):
            return None
        payload = data[pos + _RECORD_HEADER.size : pos + _RECORD_HEADER.size + length]
        (crc,) = _RECORD_CRC.unpack_from(data, end - _RECORD_CRC.size)
        if crc != zlib.crc32(data[pos + 4 : end - _RECORD_CRC.size]):
            return None
        return record_type, payload, end

    def _replay(self) -> None:
        if not self._path.exists():
            return
        data = self._path.read_bytes()
        pos = 0
        valid_end = 0
        corrupted = 0
        while pos < len(data):
            record = self._decode(data, pos)
            if record is None:
                corrupted += 1
                resync = self._resync(data, pos + 1)
                if resync is None:
                    break
                pos = resync
                continue
            record_type, payload, pos = record
            valid_end = pos
            if record_type == _RECORD_PUSH:
                self._items.append(payload)
            elif self._items:
                self._pop_item()
        if corrupted:
            logger.warning(
                f"Skipped {corrupted} corrupted record(s) while recovering "
                f"request queue {self._path}"
            )
            # Drop the unusable tail so that later appends stay decodable.
            with self._path.open("r+b") as f:
                f.truncate(valid_end)

    @staticmethod
    def _resync(data: bytes, start: int) -> int | None:
        """Return the offset of the next valid record at or after *start*."""
        pos = data.find(_JOURNAL_MAGIC, start)
        while pos != -1:
            if _ResumableJournalQueue._decode(data, pos) is not None:
                return pos
            pos = data.find(_JOURNAL_MAGIC, pos + 1)
        return None

    def _pop_item(self) -> bytes:
        return self._items.pop() if self._lifo else self._items.popleft()

    def _write_record(self, record_type: int, payload: bytes) -> None:
        self._file.write(self._encode(record_type, payload))
        self._file.flush()
        os.fsync(self._file.fileno())

    def push(self, payload: bytes) -> None:
        self._write_record(_RECORD_PUSH, payload)
        self._items.append(payload)

    def pop(self) -> bytes | None:
        if not self._items:
            return None
        # The tombstone is durable before the item leaves the queue, so a
        # crash cannot make a dequeued request pending again.
        self._write_record(_RECORD_POP, b"")
        return self._pop_item()

    def peek(self) -> bytes | None:
        """Return the next payload to be returned by :meth:`pop`, without
        removing it from the queue."""
        if not self._items:
            return None
        return self._items[-1] if self._lifo else self._items[0]

    def close(self) -> None:
        self._file.close()
        if not self._items:
            self._path.unlink(missing_ok=True)
            return
        tmp_path = self._path.with_name(self._path.name + ".tmp")
        with tmp_path.open("wb") as f:
            for payload in self._items:
                f.write(self._encode(_RECORD_PUSH, payload))
            f.flush()
            os.fsync(f.fileno())
        tmp_path.replace(self._path)

    def __len__(self) -> int:
        return len(self._items)


class _ResumableFifoJournalQueue(_ResumableJournalQueue):
    _lifo = False


class _ResumableLifoJournalQueue(_ResumableJournalQueue):
    _lifo = True


_PickleResumableFifoDiskQueue = _serializable_queue(
    _ResumableFifoJournalQueue,  # type: ignore[arg-type]
    _pickle_serialize,
    pickle.loads,
)
_PickleResumableLifoDiskQueue = _serializable_queue(
    _ResumableLifoJournalQueue,  # type: ignore[arg-type]
    _pickle_serialize,
    pickle.loads,
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
ResumableFifoDiskQueue = _scrapy_serialization_queue(_PickleResumableFifoDiskQueue)
ResumableLifoDiskQueue = _scrapy_serialization_queue(_PickleResumableLifoDiskQueue)
