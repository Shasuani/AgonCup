"""Tests for the shareable join-code helpers (PLAN.md §3.1).

Pure (no DB): the generator's shape/alphabet invariants and the normalizer's
canonicalization are asserted here. The redeem paths that consume these codes
(``tournaments.service.join_by_code`` / ``teams.service.join_by_code``) are
RLS-dependent and covered by the Postgres integration suite.
"""

from __future__ import annotations

from app.joincodes import (
    JOIN_CODE_ALPHABET,
    JOIN_CODE_LEN,
    generate_join_code,
    normalize_join_code,
)

_AMBIGUOUS = set("01OIL")


def test_generated_code_shape_and_alphabet() -> None:
    for _ in range(500):
        code = generate_join_code()
        assert len(code) == JOIN_CODE_LEN
        assert set(code) <= set(JOIN_CODE_ALPHABET)


def test_alphabet_excludes_ambiguous_glyphs() -> None:
    # No 0/O, 1/I/L — codes must survive being read aloud / hand-copied.
    assert not (_AMBIGUOUS & set(JOIN_CODE_ALPHABET))


def test_generator_is_effectively_unique() -> None:
    # 31^8 space — 2000 draws should not collide (a floor, not a guarantee).
    codes = {generate_join_code() for _ in range(2000)}
    assert len(codes) == 2000


def test_normalize_upper_cases_and_strips_separators() -> None:
    raw = generate_join_code()
    lowered = raw.lower()
    hyphenated = f" {lowered[:4]}-{lowered[4:]} "
    assert normalize_join_code(hyphenated) == raw


def test_normalize_drops_ambiguous_and_foreign_chars() -> None:
    # Non-alphabet characters (including ambiguous glyphs) are dropped entirely.
    assert normalize_join_code("AB!CD@01") == "ABCD"
    assert normalize_join_code("") == ""
    assert normalize_join_code("----") == ""


def test_normalize_is_idempotent() -> None:
    code = generate_join_code()
    assert normalize_join_code(normalize_join_code(code)) == code
