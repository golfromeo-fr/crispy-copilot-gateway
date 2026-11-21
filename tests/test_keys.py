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
    assert set(listing[0]) == {"name", "created_at", "last_used"}


def test_last_used_write_is_throttled():
    raw = keys.create("zcode")
    keys.verify(raw)
    first = next(r for r in keys.list_keys() if r["name"] == "zcode")["last_used"]
    keys.verify(raw)  # immediate second use within the 60s window
    second = next(r for r in keys.list_keys() if r["name"] == "zcode")["last_used"]
    assert second == first
