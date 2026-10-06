import re

import pytest

from app import case_codes

DISPLAY_PATTERN = re.compile(r"^WD-[0-9A-HJKMNP-TV-Z]{4}(-[0-9A-HJKMNP-TV-Z]{4}){3}$")


def test_1000_codes_are_unique_and_well_formed():
    codes = [case_codes.generate() for _ in range(1000)]
    assert len(set(codes)) == 1000
    for code in codes:
        assert len(code) == 16
        assert DISPLAY_PATTERN.match(case_codes.display(code))


def test_alphabet_has_no_ambiguous_letters():
    assert len(case_codes.ALPHABET) == 32
    assert not set("ILOU") & set(case_codes.ALPHABET)


def test_hash_is_keyed_and_not_plain_sha256():
    import hashlib

    code = "7K3MQ9XA2HFDR8TN"
    assert case_codes.hash_code(code) != hashlib.sha256(code.encode()).hexdigest()
    assert len(case_codes.hash_code(code)) == 64


@pytest.mark.parametrize(
    "typed",
    [
        "WD-7K3M-Q9XA-2HFD-R8TN",
        "wd-7k3m-q9xa-2hfd-r8tn",
        "7K3MQ9XA2HFDR8TN",
        "WD7K3MQ9XA2HFDR8TN",
        "  wd 7k3m q9xa 2hfd r8tn ",
        "7K3M-Q9XA-2HFD-R8TN",
    ],
)
def test_normalise_accepts_common_typing_variations(typed):
    assert case_codes.normalise(typed) == "7K3MQ9XA2HFDR8TN"


def test_normalise_maps_lookalike_letters():
    assert case_codes.normalise("WD-O1LI-0000-1111-2222") == "0111000011112222"


@pytest.mark.parametrize(
    "typed",
    ["", "WD-", "WD-7K3M-Q9XA-2HFD", "WD-7K3M-Q9XA-2HFD-R8TN-XXXX", "WD-7K3M-Q9XA-2HFD-R8TU", "WD-7K3M-Q9XA-2HFD-R8T!"],
)
def test_normalise_rejects_bad_formats(typed):
    assert case_codes.normalise(typed) is None


def test_raw_code_is_never_stored(submit, engine, tmp_path):
    display_code = submit().json()["case_code"]
    bare_code = case_codes.normalise(display_code)

    with engine.connect() as conn:
        rows = conn.exec_driver_sql("SELECT * FROM reports").all()
    for row in rows:
        for value in row:
            assert bare_code not in str(value)
            assert display_code not in str(value)

    # and not hiding anywhere else in the database file either
    raw_file = (tmp_path / "test.db").read_bytes()
    assert bare_code.encode() not in raw_file
    assert display_code.encode() not in raw_file


@pytest.mark.parametrize(
    "transform",
    [
        lambda c: c,
        lambda c: c.lower(),
        lambda c: c.replace("-", ""),
        lambda c: c.replace("-", " "),
        lambda c: c[3:],
        lambda c: c[3:].replace("-", "").lower(),
    ],
    ids=["as given", "lowercase", "no dashes", "spaces", "no prefix", "no prefix, no dashes, lowercase"],
)
def test_status_lookup_works_however_code_is_typed(submit, client, transform):
    code = submit().json()["case_code"]
    response = client.get("/api/reports/status", headers={"X-Case-Code": transform(code)})
    assert response.status_code == 200
    assert response.json()["status"] == "SUBMITTED"


def test_lookup_maps_lookalike_letters(submit, client, monkeypatch):
    monkeypatch.setattr(case_codes, "generate", lambda: "0123456789ABCDEF")
    submit()
    response = client.get("/api/reports/status", headers={"X-Case-Code": "wd-o123-4567-89ab-cdef"})
    assert response.status_code == 200
    response = client.get("/api/reports/status", headers={"X-Case-Code": "WD-0I23-4567-89AB-CDEF"})
    assert response.status_code == 200
    response = client.get("/api/reports/status", headers={"X-Case-Code": "WD-0L23-4567-89AB-CDEF"})
    assert response.status_code == 200


def test_missing_case_code_header_is_400(client):
    response = client.get("/api/reports/status")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "MISSING_CASE_CODE"


@pytest.mark.parametrize("bad", ["hello", "WD-1234", "WD-7K3M-Q9XA-2HFD-R8TU"])
def test_badly_formatted_case_code_is_400(client, bad):
    response = client.get("/api/reports/status", headers={"X-Case-Code": bad})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_CASE_CODE"


def test_valid_but_unknown_case_code_is_404(submit, client):
    submit()
    response = client.get("/api/reports/status", headers={"X-Case-Code": "WD-0000-0000-0000-0000"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CASE_NOT_FOUND"


def test_case_code_in_query_string_is_not_used(submit, client):
    code = submit().json()["case_code"]
    response = client.get("/api/reports/status", params={"case_code": code})
    assert response.status_code == 400


def test_duplicate_hash_gets_a_new_code(submit, monkeypatch):
    codes = iter(["AAAAAAAAAAAAAAAA", "AAAAAAAAAAAAAAAA", "AAAAAAAAAAAAAAAA", "BBBBBBBBBBBBBBBB"])
    monkeypatch.setattr(case_codes, "generate", lambda: next(codes))
    assert submit().json()["case_code"] == "WD-AAAA-AAAA-AAAA-AAAA"
    assert submit().json()["case_code"] == "WD-BBBB-BBBB-BBBB-BBBB"
