"""Shared in-memory fake AsyncSession for the bridge/guilds unit tests.

Mirrors ``tests/test_auth.py``'s approach (a dict-backed stand-in implementing the
narrow ``AsyncSession`` surface the code touches) but adds the models the guild
onboarding + Discord bridge paths use and tracks the ``app.current_guild`` GUC set
via ``text("SELECT set_config(...)")`` so RLS-scoping intent is observable. NO real
database, network, or Discord gateway is involved (PLAN.md SHARED CONVENTIONS).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from app.models import (
    Guild,
    Registration,
    Team,
    TeamMember,
    Tournament,
    User,
    UserGuildRole,
)

# Models the fake session can store/query for these phases.
_MODELS: tuple[type, ...] = (
    User,
    Guild,
    UserGuildRole,
    Team,
    TeamMember,
    Tournament,
    Registration,
)


@dataclass
class _Result:
    rows: list[Any]

    def scalar_one_or_none(self) -> Any:
        return self.rows[0] if self.rows else None

    def scalar_one(self) -> Any:
        if not self.rows:
            raise LookupError("no rows for scalar_one")
        return self.rows[0]

    def first(self) -> Any:
        return self.rows[0] if self.rows else None

    def scalars(self) -> _Scalars:
        return _Scalars(self.rows)

    def all(self) -> list[Any]:
        return self.rows


@dataclass
class _Scalars:
    rows: list[Any]

    def all(self) -> list[Any]:
        return self.rows


class _FakeTxn:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: Any) -> None:
        return None


class FakeSession:
    """Dict-backed stand-in for AsyncSession covering the bridge/guilds paths."""

    def __init__(self) -> None:
        self.store: dict[type, list[Any]] = {m: [] for m in _MODELS}
        self.current_guild: str | None = None

    # --- unit-of-work -----------------------------------------------------
    def add(self, obj: Any) -> None:
        if getattr(obj, "id", None) is None and hasattr(obj, "id"):
            obj.id = uuid.uuid4()
        self.store[type(obj)].append(obj)

    def add_all(self, objs: Any) -> None:
        for obj in objs:
            self.add(obj)

    async def flush(self) -> None:
        for rows in self.store.values():
            for obj in rows:
                if getattr(obj, "id", None) is None and hasattr(obj, "id"):
                    obj.id = uuid.uuid4()

    async def delete(self, obj: Any) -> None:
        bucket = self.store.get(type(obj))
        if bucket is not None and obj in bucket:
            bucket.remove(obj)

    # --- query dispatch ---------------------------------------------------
    async def execute(self, statement: Any, params: Any = None) -> Any:
        # text() statements: only set_config('app.current_guild', ...) matters.
        if not hasattr(statement, "column_descriptions"):
            return self._execute_text(statement, params)
        return self._execute_select(statement)

    def _execute_text(self, statement: Any, params: Any) -> _Result:
        sql = str(statement).lower()
        if "set_config" in sql and params and "gid" in params:
            self.current_guild = str(params["gid"])
            return _Result([self.current_guild])
        if "current_setting" in sql:
            return _Result([self.current_guild] if self.current_guild else [])
        return _Result([])

    def _execute_select(self, statement: Any) -> _Result:
        entity = statement.column_descriptions[0]["entity"]
        crit = _equality_filters(statement)

        # Aggregate ``select(func.count()).select_from(Model)`` has entity=None;
        # resolve the model from the FROM clause and return the matching row count.
        if entity is None and _is_count(statement):
            model = _model_from_clause(statement)
            rows = [r for r in self.store[model] if _matches(r, crit)]
            return _Result([len(rows)])

        model = _model_of(entity)
        rows = [r for r in self.store[model] if _matches(r, crit)]
        cols = _selected_columns(statement)
        if cols and not _is_whole_entity(statement):
            projected = [tuple(getattr(r, c) for c in cols) for r in rows]
            return _Result(projected)
        return _Result(rows)

    # --- transaction / context -------------------------------------------
    def begin(self) -> _FakeTxn:
        return _FakeTxn()

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None


# --- tiny SQLAlchemy-construct introspection helpers ----------------------
def _model_of(entity: Any) -> type:
    if hasattr(entity, "class_"):  # column -> .class_
        return entity.class_
    if hasattr(entity, "entity"):
        return entity.entity
    name = getattr(entity, "name", None)
    for model in _MODELS:
        if model.__tablename__ == name:
            return model
    return entity


def _is_count(statement: Any) -> bool:
    """True for a ``select(func.count()...)`` aggregate (entity-less projection)."""
    descs = statement.column_descriptions
    if len(descs) != 1:
        return False
    return (
        "count" in str(descs[0].get("name", "")).lower()
        or "count" in str(descs[0].get("expr", "")).lower()
    )


def _model_from_clause(statement: Any) -> type:
    """Resolve the queried model from a count statement's explicit FROM clause."""
    froms = getattr(statement, "_from_obj", ()) or ()
    for f in froms:
        name = getattr(f, "name", None)
        for model in _MODELS:
            if model.__tablename__ == name:
                return model
    # Fall back to the WHERE-clause column's owning model (e.g. Registration.x == ...).
    for clause in list(getattr(statement, "_where_criteria", ()) or ()):
        for expr in _flatten_and(clause):
            left = getattr(expr, "left", None)
            owner = getattr(getattr(left, "table", None), "name", None)
            for model in _MODELS:
                if model.__tablename__ == owner:
                    return model
    raise LookupError("cannot resolve model for count statement")


def _selected_columns(statement: Any) -> list[str]:
    out = []
    for desc in statement.column_descriptions:
        expr = desc.get("expr")
        if expr is not None and hasattr(expr, "key") and desc.get("entity") is not expr:
            out.append(expr.key)
    return out


def _is_whole_entity(statement: Any) -> bool:
    descs = statement.column_descriptions
    if len(descs) != 1:
        return False
    return descs[0].get("expr") is descs[0].get("entity")


def _equality_filters(statement: Any) -> dict[str, Any]:
    crit: dict[str, Any] = {}
    clauses = list(getattr(statement, "_where_criteria", ()) or ())
    for clause in clauses:
        for bool_expr in _flatten_and(clause):
            left = getattr(bool_expr, "left", None)
            right = getattr(bool_expr, "right", None)
            if left is not None and hasattr(left, "key"):
                crit[left.key] = _literal(right)
    return crit


def _flatten_and(clause: Any) -> list[Any]:
    if clause is None:
        return []
    if clause.__class__.__name__ == "BooleanClauseList":
        out: list[Any] = []
        for c in clause.clauses:
            out.extend(_flatten_and(c))
        return out
    return [clause]


def _literal(value: Any) -> Any:
    return getattr(value, "value", value)


def _matches(row: Any, crit: dict[str, Any]) -> bool:
    for key, val in crit.items():
        actual = getattr(row, key, None)
        actual = getattr(actual, "value", actual)
        val = getattr(val, "value", val)
        if str(actual) != str(val):
            return False
    return True


__all__ = ["FakeSession"]
