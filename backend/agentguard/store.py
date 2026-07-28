import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel


Record = tuple[str, str, str, BaseModel]
Model = TypeVar("Model", bound=BaseModel)


class Store:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS records("
                "kind TEXT NOT NULL, id TEXT PRIMARY KEY, product_id TEXT NOT NULL, payload TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS records_by_product_kind ON records(product_id, kind)"
            )

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def save(self, kind: str, record_id: str, product_id: str, payload: BaseModel) -> None:
        self.save_many([(kind, record_id, product_id, payload)])

    def save_many(self, records: Iterable[Record]) -> None:
        rows = [
            (kind, record_id, product_id, payload.model_dump_json())
            for kind, record_id, product_id, payload in records
        ]
        with self.connect() as connection:
            connection.executemany(
                "INSERT INTO records(kind,id,product_id,payload) VALUES(?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET kind=excluded.kind, product_id=excluded.product_id, payload=excluded.payload",
                rows,
            )

    def get(self, kind: str, record_id: str, model: type[Model]) -> Model | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload FROM records WHERE kind=? AND id=?", (kind, record_id)
            ).fetchone()
        return model.model_validate_json(row["payload"]) if row else None

    def list(
        self, kind: str, model: type[Model], product_id: str | None = None
    ) -> list[Model]:
        query = "SELECT payload FROM records WHERE kind=?"
        args: list[str] = [kind]
        if product_id:
            query += " AND product_id=?"
            args.append(product_id)
        with self.connect() as connection:
            rows = connection.execute(query, args).fetchall()
        return [model.model_validate_json(row["payload"]) for row in rows]
