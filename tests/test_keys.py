"""Key lifecycle: create/verify/revoke/list, hashes never leave the module."""
from crispy_copilot_gateway import keys


def test_create_returns_ck_prefixed_secret_shown_once():
    raw = keys.create("zcode")
    assert raw.startswith("ck_") and len(raw) > 40
    assert keys.create("other") != raw


def test_verify_accepts_raw_and_stamps_last_used():
    raw = keys.create("zcode")
    assert keys.verify(raw) is True
    record = next(r for r in keys.list_keys() if r["name"] == "zcode")
    assert record["last_used"] is not None


def test_verify_rejects_wrong_or_empty_key():
    keys.create("zcode")
    assert keys.verify("ck_wrong") is False
    assert keys.verify("") is False


def test_duplicate_name_rejected():
    keys.create("zcode")
    try:
        keys.create("zcode")
        assert False, "duplicate name must raise"
    except ValueError:
        pass


def test_revoke_removes_key_then_reports_not_found():
    raw = keys.create("old")
    assert keys.revoke("old") is True
    assert keys.verify(raw) is False
    assert keys.revoke("old") is False


def test_list_keys_exposes_no_hashes():
    keys.create("zcode")
    listing = keys.list_keys()
    assert listing and "key_hash" not in listing[0]
    assert set(listing[0]) == {"name", "created_at", "last_used", "connection"}


def test_last_used_write_is_throttled():
    raw = keys.create("zcode")
    keys.verify(raw)
    first = next(r for r in keys.list_keys() if r["name"] == "zcode")["last_used"]
    keys.verify(raw)  # immediate second use within the 60s window
    second = next(r for r in keys.list_keys() if r["name"] == "zcode")["last_used"]
    assert second == first


def test_create_with_connection_pin():
    keys.create("zcode", connection="work")
    assert keys.connection_of("zcode") == "work"
    assert next(r for r in keys.list_keys() if r["name"] == "zcode")["connection"] == "work"


def test_set_connection_roundtrip_and_clear():
    keys.create("zcode")
    assert keys.connection_of("zcode") is None
    assert keys.set_connection("zcode", "work") is True
    assert keys.connection_of("zcode") == "work"
    assert keys.set_connection("zcode", None) is True
    assert keys.connection_of("zcode") is None


def test_set_connection_unknown_key_returns_false():
    assert keys.set_connection("ghost", "work") is False
    assert keys.connection_of("ghost") is None


def test_connection_pin_survives_last_used_stamp():
    raw = keys.create("zcode", connection="work")
    keys.verify(raw)  # stamps last_used and re-saves the entry
    assert keys.connection_of("zcode") == "work"
