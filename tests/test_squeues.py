import json
import pickle
import sys
from collections import deque
from pathlib import Path
from typing import Any, cast

import pytest
from queuelib.tests import test_queue as t

from scrapy.http import Request
from scrapy.item import Field, Item
from scrapy.loader import ItemLoader
from scrapy.selector import Selector
from scrapy.squeues import (
    FifoMemoryQueue,
    LifoMemoryQueue,
    _encode_frame,
    _MarshalFifoSerializationDiskQueue,
    _MarshalFifoSerializationSQLiteQueue,
    _MarshalLifoSerializationDiskQueue,
    _MarshalLifoSerializationSQLiteQueue,
    _PickleFifoSerializationDiskQueue,
    _PickleFifoSerializationSQLiteQueue,
    _PickleLifoSerializationDiskQueue,
    _PickleLifoSerializationSQLiteQueue,
    _PickleResumableFifoSerializationDiskQueue,
    _PickleResumableLifoSerializationDiskQueue,
    _ResumableDiskQueue,
    _scrapy_non_serialization_queue,
    _serializable_queue,
)

# The disk and SQLite queue classes are built at run time, so they are untyped.
_MarshalFifoQueue: Any = _MarshalFifoSerializationDiskQueue
_MarshalLifoQueue: Any = _MarshalLifoSerializationDiskQueue
_PickleFifoQueue: Any = _PickleFifoSerializationDiskQueue
_PickleLifoQueue: Any = _PickleLifoSerializationDiskQueue
_MarshalFifoSQLiteQueue: Any = _MarshalFifoSerializationSQLiteQueue
_MarshalLifoSQLiteQueue: Any = _MarshalLifoSerializationSQLiteQueue
_PickleFifoSQLiteQueue: Any = _PickleFifoSerializationSQLiteQueue
_PickleLifoSQLiteQueue: Any = _PickleLifoSerializationSQLiteQueue
_PickleResumableFifoQueue: Any = _PickleResumableFifoSerializationDiskQueue
_PickleResumableLifoQueue: Any = _PickleResumableLifoSerializationDiskQueue


class ResumableQueueTestCase:
    @pytest.fixture(autouse=True)
    def _set_qpath(self, tmp_path: Path) -> None:
        self.qpath = tmp_path / "q"


class MyItem(Item):
    name = Field()


class NoPeekQueue:
    """Queue class without the optional peek method."""


_no_peek_queue = cast("Any", NoPeekQueue)


@pytest.mark.parametrize(
    "queue_class",
    [
        _serializable_queue(_no_peek_queue, pickle.dumps, pickle.loads),
        _scrapy_non_serialization_queue(_no_peek_queue),
    ],
)
def test_peek_unsupported(queue_class):
    with pytest.raises(
        NotImplementedError,
        match="The underlying queue class does not implement 'peek'",
    ):
        queue_class().peek()


def _test_procesor(x):
    return x + x


class MyLoader(ItemLoader):
    default_item_class = MyItem
    name_out = staticmethod(_test_procesor)


def nonserializable_object_test(self):
    q = self.queue()
    with pytest.raises(
        ValueError,
        match=r"unmarshallable object|Can't (get|pickle) local object|Can't pickle .*: it's not found as",
    ):
        q.push(lambda x: x)
    # Selectors should fail (lxml.html.HtmlElement objects can't be pickled)
    sel = Selector(text="<html><body><p>some text</p></body></html>")
    with pytest.raises(
        ValueError, match=r"unmarshallable object|can't pickle Selector objects"
    ):
        q.push(sel)
    q.close()


class FifoQueueTestMixin:
    def queue(self) -> Any:
        raise NotImplementedError

    def test_serialize(self):
        q = self.queue()
        q.push("a")
        q.push(123)
        q.push({"a": "dict"})
        assert q.pop() == "a"
        assert q.pop() == 123
        assert q.pop() == {"a": "dict"}
        q.close()

    test_nonserializable_object = nonserializable_object_test


class MarshalFifoDiskQueueTest(t.FifoDiskQueueTest, FifoQueueTestMixin):
    chunksize = 100000

    def queue(self) -> Any:
        return _MarshalFifoQueue(self.qpath, chunksize=self.chunksize)


class ChunkSize1MarshalFifoDiskQueueTest(MarshalFifoDiskQueueTest):
    chunksize = 1


class ChunkSize2MarshalFifoDiskQueueTest(MarshalFifoDiskQueueTest):
    chunksize = 2


class ChunkSize3MarshalFifoDiskQueueTest(MarshalFifoDiskQueueTest):
    chunksize = 3


class ChunkSize4MarshalFifoDiskQueueTest(MarshalFifoDiskQueueTest):
    chunksize = 4


class PickleFifoDiskQueueTest(t.FifoDiskQueueTest, FifoQueueTestMixin):
    chunksize = 100000

    def queue(self) -> Any:
        return _PickleFifoQueue(self.qpath, chunksize=self.chunksize)

    def test_serialize_item(self):
        q = self.queue()
        i = MyItem(name="foo")
        q.push(i)
        i2 = q.pop()
        assert isinstance(i2, MyItem)
        assert i == i2
        q.close()

    def test_serialize_loader(self):
        q = self.queue()
        loader = MyLoader()
        q.push(loader)
        loader2 = q.pop()
        assert isinstance(loader2, MyLoader)
        assert loader2.default_item_class is MyItem
        assert loader2.name_out("x") == "xx"
        q.close()

    def test_serialize_request_recursive(self):
        q = self.queue()
        r = Request("http://www.example.com")
        r.meta["request"] = r
        q.push(r)
        r2 = q.pop()
        assert isinstance(r2, Request)
        assert r.url == r2.url
        assert r2.meta["request"] is r2
        q.close()

    def test_non_pickable_object(self):
        q = self.queue()
        with pytest.raises(
            ValueError,
            match=r"Can't (get|pickle) local object|Can't pickle .*: it's not found as",
        ) as exc_info:
            q.push(lambda x: x)
        if sys.version_info >= (3, 14) or hasattr(sys, "pypy_version_info"):
            assert isinstance(exc_info.value.__context__, pickle.PicklingError)
        else:
            assert isinstance(exc_info.value.__context__, AttributeError)
        sel = Selector(text="<html><body><p>some text</p></body></html>")
        with pytest.raises(
            ValueError, match="can't pickle Selector objects"
        ) as exc_info:
            q.push(sel)
        assert isinstance(exc_info.value.__context__, TypeError)
        q.close()


class ChunkSize1PickleFifoDiskQueueTest(PickleFifoDiskQueueTest):
    chunksize = 1


class ChunkSize2PickleFifoDiskQueueTest(PickleFifoDiskQueueTest):
    chunksize = 2


class ChunkSize3PickleFifoDiskQueueTest(PickleFifoDiskQueueTest):
    chunksize = 3


class ChunkSize4PickleFifoDiskQueueTest(PickleFifoDiskQueueTest):
    chunksize = 4


class LifoQueueTestMixin:
    def queue(self) -> Any:
        raise NotImplementedError

    def test_serialize(self):
        q = self.queue()
        q.push("a")
        q.push(123)
        q.push({"a": "dict"})
        assert q.pop() == {"a": "dict"}
        assert q.pop() == 123
        assert q.pop() == "a"
        q.close()

    test_nonserializable_object = nonserializable_object_test


class MarshalLifoDiskQueueTest(t.LifoDiskQueueTest, LifoQueueTestMixin):
    def queue(self) -> Any:
        return _MarshalLifoQueue(self.qpath)


class PickleLifoDiskQueueTest(t.LifoDiskQueueTest, LifoQueueTestMixin):
    def queue(self) -> Any:
        return _PickleLifoQueue(self.qpath)

    def test_serialize_item(self):
        q = self.queue()
        i = MyItem(name="foo")
        q.push(i)
        i2 = q.pop()
        assert isinstance(i2, MyItem)
        assert i == i2
        q.close()

    def test_serialize_loader(self):
        q = self.queue()
        loader = MyLoader()
        q.push(loader)
        loader2 = q.pop()
        assert isinstance(loader2, MyLoader)
        assert loader2.default_item_class is MyItem
        assert loader2.name_out("x") == "xx"
        q.close()

    def test_serialize_request_recursive(self):
        q = self.queue()
        r = Request("http://www.example.com")
        r.meta["request"] = r
        q.push(r)
        r2 = q.pop()
        assert isinstance(r2, Request)
        assert r.url == r2.url
        assert r2.meta["request"] is r2
        q.close()


class MarshalFifoSQLiteQueueTest(t.FifoSQLiteQueueTest, FifoQueueTestMixin):
    def queue(self) -> Any:
        return _MarshalFifoSQLiteQueue(self.qpath)


class PickleFifoSQLiteQueueTest(t.FifoSQLiteQueueTest, FifoQueueTestMixin):
    def queue(self) -> Any:
        return _PickleFifoSQLiteQueue(self.qpath)


class MarshalLifoSQLiteQueueTest(t.LifoSQLiteQueueTest, LifoQueueTestMixin):
    def queue(self) -> Any:
        return _MarshalLifoSQLiteQueue(self.qpath)


class PickleLifoSQLiteQueueTest(t.LifoSQLiteQueueTest, LifoQueueTestMixin):
    def queue(self) -> Any:
        return _PickleLifoSQLiteQueue(self.qpath)


class TestResumableFifoDiskQueue(
    t.FifoTestMixin, t.QueueTestMixin, ResumableQueueTestCase
):
    def queue(self) -> Any:
        return _PickleResumableFifoQueue(self.qpath)


class TestResumableLifoDiskQueue(
    t.LifoTestMixin, t.QueueTestMixin, ResumableQueueTestCase
):
    def queue(self) -> Any:
        return _PickleResumableLifoQueue(self.qpath)


def _simulate_crash(q: _ResumableDiskQueue) -> None:
    """Simulate a process crash: pushed data is flushed, but the journal is
    never compacted and the queue is never closed."""
    q._journal_file.close()


class TestResumableDiskQueue:
    @pytest.mark.parametrize("fifo", [True, False])
    def test_recovery_after_close(self, tmp_path, fifo: bool) -> None:
        q = _ResumableDiskQueue(tmp_path, fifo=fifo)
        q.push(b"a")
        q.push(b"b")
        q.close()

        q = _ResumableDiskQueue(tmp_path, fifo=fifo)
        assert len(q) == 2
        first, second = (b"a", b"b") if fifo else (b"b", b"a")
        assert q.pop() == first
        assert q.pop() == second
        assert q.pop() is None
        q.close()

    @pytest.mark.parametrize("fifo", [True, False])
    def test_recovery_after_crash(self, tmp_path, fifo: bool) -> None:
        q = _ResumableDiskQueue(tmp_path, fifo=fifo)
        q.push(b"a")
        q.push(b"b")
        _simulate_crash(q)

        q = _ResumableDiskQueue(tmp_path, fifo=fifo)
        assert len(q) == 2
        first, second = (b"a", b"b") if fifo else (b"b", b"a")
        assert q.pop() == first
        assert q.pop() == second
        q.close()

    def test_interleaved_push_pop_recovery(self, tmp_path) -> None:
        q = _ResumableDiskQueue(tmp_path)
        q.push(b"a")
        q.push(b"b")
        q.push(b"c")
        assert q.pop() == b"a"
        q.push(b"d")
        assert q.pop() == b"b"
        _simulate_crash(q)

        q = _ResumableDiskQueue(tmp_path)
        assert len(q) == 2
        assert q.pop() == b"c"
        assert q.pop() == b"d"
        assert q.pop() is None
        q.close()

    def test_duplicate_items(self, tmp_path) -> None:
        q = _ResumableDiskQueue(tmp_path)
        for _ in range(3):
            q.push(b"same")
        _simulate_crash(q)

        q = _ResumableDiskQueue(tmp_path)
        assert len(q) == 3
        assert q.pop() == b"same"
        assert q.pop() == b"same"
        assert q.pop() == b"same"
        assert q.pop() is None
        q.close()

    def test_corrupted_frame_skipped(self, tmp_path, caplog) -> None:
        journal = tmp_path / "journal.dat"
        corrupted = bytearray(_encode_frame(b"+", b"lost"))
        corrupted[5] ^= 0xFF  # flip a payload bit to break the CRC
        journal.write_bytes(
            _encode_frame(b"+", b"a")
            + bytes(corrupted)
            + _encode_frame(b"+", b"b")
            + _encode_frame(b"-", b"")
        )

        with caplog.at_level("WARNING"):
            q = _ResumableDiskQueue(tmp_path)
        assert "corrupted" in caplog.text
        # The corrupted push is lost, but confirmed items survive and the
        # pop still applies to the oldest confirmed item.
        assert len(q) == 1
        assert q.pop() == b"b"
        assert q.pop() is None
        q.close()

    def test_truncated_journal(self, tmp_path) -> None:
        journal = tmp_path / "journal.dat"
        frame = _encode_frame(b"+", b"truncated")
        journal.write_bytes(_encode_frame(b"+", b"a") + frame[: len(frame) // 2])

        q = _ResumableDiskQueue(tmp_path)
        assert len(q) == 1
        assert q.pop() == b"a"
        assert q.pop() is None
        q.close()

    def test_garbage_journal(self, tmp_path) -> None:
        journal = tmp_path / "journal.dat"
        journal.write_bytes(b"\x00" * 64 + _encode_frame(b"+", b"a") + b"\xff\xfe")

        q = _ResumableDiskQueue(tmp_path)
        assert len(q) == 1
        assert q.pop() == b"a"
        q.close()

    def test_corrupted_stats_ignored(self, tmp_path, caplog) -> None:
        (tmp_path / "stats.json").write_text("not json", encoding="utf-8")
        with caplog.at_level("WARNING"):
            q = _ResumableDiskQueue(tmp_path)
        assert "Ignoring corrupted queue stats file" in caplog.text
        q.push(b"a")
        assert q.pop() == b"a"
        q.close()

    def test_stats_saved_atomically_on_close(self, tmp_path) -> None:
        q = _ResumableDiskQueue(tmp_path)
        q.push(b"a")
        q.push(b"b")
        q.pop()
        q.close()

        stats = json.loads((tmp_path / "stats.json").read_text(encoding="utf-8"))
        assert stats == {"pushed": 2, "popped": 1}
        assert not list(tmp_path.glob("*.tmp"))

    def test_compaction_preserves_items(self, tmp_path) -> None:
        q = _ResumableDiskQueue(tmp_path)
        for index in range(2100):
            q.push(str(index).encode())
        for _ in range(1100):
            q.pop()
        # Popping more than 1024 items triggers a journal compaction.
        stats = json.loads((tmp_path / "stats.json").read_text(encoding="utf-8"))
        assert stats["pushed"] == 2100
        assert 1024 <= stats["popped"] <= 1100
        _simulate_crash(q)

        q = _ResumableDiskQueue(tmp_path)
        assert len(q) == 1000
        for index in range(1100, 2100):
            assert q.pop() == str(index).encode()
        assert q.pop() is None
        q.close()

    def test_stress(self, tmp_path) -> None:
        q = _ResumableDiskQueue(tmp_path)
        model: deque[bytes] = deque()
        for index in range(10000):
            item = str(index).encode()
            q.push(item)
            model.append(item)
            if index % 3 == 0:
                assert q.pop() == model.popleft()
        _simulate_crash(q)

        q = _ResumableDiskQueue(tmp_path)
        assert len(q) == len(model)
        while model:
            assert q.pop() == model.popleft()
        assert q.pop() is None
        q.close()


class TestMemoryQueues:
    @pytest.mark.parametrize(
        ("queue_class", "expected"),
        [
            (FifoMemoryQueue, ["a", "b", "a", "c"]),
            (LifoMemoryQueue, ["c", "a", "b", "a"]),
        ],
    )
    def test_duplicate_items(self, queue_class, expected: list[str]) -> None:
        q = queue_class()
        for item in ["a", "b", "a", "c"]:
            q.push(item)
        assert len(q) == 4
        assert [q.pop() for _ in range(4)] == expected
        assert q.pop() is None

    @pytest.mark.parametrize(
        ("queue_class", "fifo"),
        [(FifoMemoryQueue, True), (LifoMemoryQueue, False)],
    )
    def test_stress(self, queue_class, fifo: bool) -> None:
        q = queue_class()
        model: deque[int] = deque()
        for index in range(10000):
            q.push(index)
            model.append(index)
            if index % 3 == 0:
                expected = model.popleft() if fifo else model.pop()
                assert q.pop() == expected
        assert len(q) == len(model)
        while model:
            expected = model.popleft() if fifo else model.pop()
            assert q.pop() == expected
        assert q.pop() is None
