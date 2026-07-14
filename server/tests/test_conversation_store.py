"""Tests for the in-memory ConversationStore."""

from datetime import UTC, datetime, timedelta

from soundings.ask.conversation_store import ConversationStore


def test_create_returns_uuid_string() -> None:
    store = ConversationStore()
    cid = store.create()
    assert isinstance(cid, str)
    assert len(cid) > 0


def test_get_returns_conversation() -> None:
    store = ConversationStore()
    cid = store.create(place_id="ltla24:E06000047")
    conv = store.get(cid)
    assert conv is not None
    assert conv.place_id == "ltla24:E06000047"
    assert conv.messages == []


def test_get_unknown_returns_none() -> None:
    store = ConversationStore()
    assert store.get("nonexistent-id") is None


def test_append_messages() -> None:
    store = ConversationStore()
    cid = store.create()
    msgs = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
    ]
    store.append_messages(cid, msgs)
    conv = store.get(cid)
    assert conv is not None
    assert len(conv.messages) == 2
    assert conv.messages[0]["role"] == "user"


def test_append_updates_last_active() -> None:
    store = ConversationStore(ttl_minutes=1)
    cid = store.create()
    conv1 = store.get(cid)
    assert conv1 is not None
    old_active = conv1.last_active

    # Append to update last_active
    store.append_messages(cid, [{"role": "user", "content": "test"}])
    conv2 = store.get(cid)
    assert conv2 is not None
    assert conv2.last_active > old_active


def test_expired_conversation_returns_none() -> None:
    store = ConversationStore(ttl_minutes=1)
    cid = store.create()
    # Manually expire by backdating last_active
    conv = store._store[cid]
    conv.last_active = datetime.now(tz=UTC) - timedelta(minutes=5)
    assert store.get(cid) is None


def test_cleanup_expired_removes_old_conversations() -> None:
    store = ConversationStore(ttl_minutes=1)
    cid1 = store.create()
    cid2 = store.create()
    # Expire cid1
    store._store[cid1].last_active = datetime.now(tz=UTC) - timedelta(minutes=5)
    # Trigger cleanup via create
    store.create()
    assert store.get(cid1) is None
    assert store.get(cid2) is not None


def test_append_to_unknown_conversation_is_noop() -> None:
    store = ConversationStore()
    store.append_messages("nonexistent", [{"role": "user", "content": "test"}])
    # Should not raise


def test_each_create_gets_unique_id() -> None:
    store = ConversationStore()
    cid1 = store.create()
    cid2 = store.create()
    assert cid1 != cid2


def test_update_place_id() -> None:
    store = ConversationStore()
    cid = store.create()
    assert store.get(cid).place_id is None
    store.update_place_id(cid, "ltla24:E08000035")
    assert store.get(cid).place_id == "ltla24:E08000035"


def test_update_place_id_unknown_is_noop() -> None:
    store = ConversationStore()
    store.update_place_id("nonexistent", "ltla24:E08000035")
    # Should not raise
