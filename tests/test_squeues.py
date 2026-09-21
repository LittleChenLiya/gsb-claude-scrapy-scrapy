import logging
import pickle
import random
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
    _RECORD_HEADER,
    _RECORD_PUSH,
    _MarshalFifoSerializationDiskQueue,
    _MarshalFifoSerializationSQLiteQueue,
    _MarshalLifoSerializationDiskQueue,
    _MarshalLifoSerializationSQLiteQueue,
    _PickleFifoSerializationDiskQueue,
    _PickleFifoSerializationSQLiteQueue,
    _PickleLifoSerializationDiskQueue,
    _PickleLifoSerializationSQLiteQueue,
    _PickleResumableFifoDiskQueue,
    _PickleResumableLifoDiskQueue,
    _ResumableJournalQueue,
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


def _crash(queue: Any) -> None:
    """Simulate a process crash: confirmed operations are already flushed to
    disk, so only the file handle needs to be released (a real close() would
    compact the journal, which a crash never does)."""
    queue._file.close()


class ResumableJournalQueueTestBase:
    queue_class: Any
    is_fifo: bool

    @pytest.fixture
    def qpath(self, tmp_path: Path) -> Path:
        return tmp_path / "requests.journal"

    def queue(self, path: Path) -> Any:
        return self.queue_class(path)

    def test_serialize(self, qpath):
        q = self.queue(qpath)
        q.push("a")
        q.push(123)
        q.push({"a": "dict"})
        expected = ["a", 123, {"a": "dict"}]
        if not self.is_fifo:
            expected.reverse()
        assert [q.pop(), q.pop(), q.pop()] == expected
        assert q.pop() is None
        q.close()

    def test_peek(self, qpath):
        q = self.queue(qpath)
        assert q.peek() is None
        q.push("a")
        q.push("b")
        assert q.peek() == ("a" if self.is_fifo else "b")
        _crash(q)
        q = self.queue(qpath)
        assert q.peek() == ("a" if self.is_fifo else "b")
        q.close()

    def test_recovery_after_crash(self, qpath):
        q = self.queue(qpath)
        q.push("a")
        q.push("b")
        _crash(q)
        q = self.queue(qpath)
        assert len(q) == 2
        expected = ["a", "b"] if self.is_fifo else ["b", "a"]
        assert [q.pop(), q.pop()] == expected
        q.close()

    def test_pop_survives_crash(self, qpath):
        q = self.queue(qpath)
        q.push("a")
        q.push("b")
        assert q.pop() is not None
        _crash(q)
        q = self.queue(qpath)
        assert len(q) == 1
        assert q.pop() == ("b" if self.is_fifo else "a")
        q.close()

    def test_interleaved_push_pop_crash(self, qpath):
        q = self.queue(qpath)
        q.push("a")
        assert q.pop() == "a"  # the queue is empty again
        q.push("b")
        q.push("c")
        assert q.pop() == ("b" if self.is_fifo else "c")
        q.push("d")
        _crash(q)
        q = self.queue(qpath)
        assert len(q) == 2
        expected = ["c", "d"] if self.is_fifo else ["d", "b"]
        assert [q.pop(), q.pop()] == expected
        q.close()

    def test_corrupted_record_is_skipped(self, qpath, caplog):
        q = self.queue(qpath)
        q.push("a")
        q.push("b")
        q.push("c")
        _crash(q)
        data = qpath.read_bytes()
        first = _ResumableJournalQueue._decode(data, 0)
        assert first is not None
        pos = first[2]  # start of the second record
        corrupted = bytearray(data)
        corrupted[pos + _RECORD_HEADER.size] ^= 0xFF  # flip a payload byte
        qpath.write_bytes(bytes(corrupted))
        with caplog.at_level(logging.WARNING, logger="scrapy.squeues"):
            q = self.queue(qpath)
        assert "corrupted" in caplog.text
        assert len(q) == 2
        expected = ["a", "c"] if self.is_fifo else ["c", "a"]
        assert [q.pop(), q.pop()] == expected
        q.close()

    def test_corrupted_pop_record_keeps_request(self, qpath):
        q = self.queue(qpath)
        q.push("a")
        assert q.pop() == "a"
        _crash(q)
        data = qpath.read_bytes()
        first = _ResumableJournalQueue._decode(data, 0)
        assert first is not None
        pos = first[2]  # start of the pop record
        corrupted = bytearray(data)
        corrupted[pos + _RECORD_HEADER.size - 1] ^= 0xFF  # flip a length byte
        qpath.write_bytes(bytes(corrupted))
        q = self.queue(qpath)
        # The tombstone is unreadable, so the request stays pending.
        assert len(q) == 1
        assert q.pop() == "a"
        q.close()

    def test_partially_written_record_is_dropped(self, qpath):
        q = self.queue(qpath)
        q.push("a")
        q.push("b")
        _crash(q)
        size_before = qpath.stat().st_size
        with qpath.open("ab") as f:
            f.write(b"\x00\x01partial")  # garbage left by a crashed write
        q = self.queue(qpath)
        assert len(q) == 2
        # The unusable tail is truncated so that later appends stay decodable.
        assert qpath.stat().st_size == size_before
        expected = ["a", "b"] if self.is_fifo else ["b", "a"]
        assert [q.pop(), q.pop()] == expected
        q.close()

    def test_close_compacts_the_journal(self, qpath):
        q = self.queue(qpath)
        for index in range(5):
            q.push(f"req-{index}")
        q.pop()
        q.pop()
        q.close()
        data = qpath.read_bytes()
        count = 0
        pos = 0
        while pos < len(data):
            record = _ResumableJournalQueue._decode(data, pos)
            assert record is not None
            assert record[0] == _RECORD_PUSH  # no tombstones survive
            count += 1
            pos = record[2]
        assert count == 3
        q = self.queue(qpath)
        assert len(q) == 3
        q.close()

    def test_close_empty_queue_removes_the_file(self, qpath):
        q = self.queue(qpath)
        q.push("a")
        q.pop()
        q.close()
        assert not qpath.exists()

    def test_stress_with_crashes(self, qpath):
        rng = random.Random(42)
        model: deque[str] = deque()
        q = self.queue(qpath)
        for step in range(300):
            if not model or rng.random() < 0.6:
                value = f"req-{step}"
                q.push(value)
                model.append(value)
            else:
                expected = model.popleft() if self.is_fifo else model.pop()
                assert q.pop() == expected
            if step % 25 == 24:
                _crash(q)
                q = self.queue(qpath)
                assert len(q) == len(model)
        while model:
            expected = model.popleft() if self.is_fifo else model.pop()
            assert q.pop() == expected
        assert q.pop() is None
        q.close()


class ResumableFifoJournalQueueTest(ResumableJournalQueueTestBase):
    queue_class = _PickleResumableFifoDiskQueue
    is_fifo = True


class ResumableLifoJournalQueueTest(ResumableJournalQueueTestBase):
    queue_class = _PickleResumableLifoDiskQueue
    is_fifo = False
