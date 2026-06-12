from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Generic, TypeVar

from sqlalchemy import case, delete, func, select, text
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from nas_index.models import Entry
from nas_index.types import IndexedItem

T = TypeVar("T")


@dataclass(frozen=True)
class Page(Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class EntryRepository:
    def __init__(self, session: Session):
        self.session = session

    def upsert_batch(
        self,
        items: list[IndexedItem],
        generation: int,
    ) -> None:
        if not items:
            return
        now = datetime.now(UTC)
        values = [
            {
                "name": item.name,
                "full_path": item.full_path,
                "parent_path": item.parent_path,
                "entry_type": item.entry_type,
                "size_bytes": item.size_bytes,
                "modified_at": item.modified_at,
                "scan_generation": generation,
                "created_at": now,
                "updated_at": now,
            }
            for item in items
        ]
        statement = insert(Entry).values(values)
        self.session.execute(
            statement.on_conflict_do_update(
                index_elements=[Entry.full_path],
                set_={
                    "name": statement.excluded.name,
                    "parent_path": statement.excluded.parent_path,
                    "entry_type": statement.excluded.entry_type,
                    "size_bytes": statement.excluded.size_bytes,
                    "modified_at": statement.excluded.modified_at,
                    "scan_generation": (
                        statement.excluded.scan_generation
                    ),
                    "updated_at": statement.excluded.updated_at,
                },
            )
        )

    def get_by_path(self, full_path: str) -> Entry | None:
        return self.session.scalar(
            select(Entry).where(
                Entry.full_path == full_path
            )
        )

    def list_children(
        self,
        parent_path: str,
        *,
        page: int,
        page_size: int,
    ) -> Page[Entry]:
        predicate = Entry.parent_path == parent_path
        total = (
            self.session.scalar(
                select(func.count())
                .select_from(Entry)
                .where(predicate)
            )
            or 0
        )
        rows = list(
            self.session.scalars(
                select(Entry)
                .where(predicate)
                .order_by(
                    case(
                        (
                            Entry.entry_type == "directory",
                            0,
                        ),
                        else_=1,
                    ),
                    func.lower(Entry.name),
                    Entry.id,
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return Page(rows, total, page, page_size)

    def list_child_directories(
        self,
        parent_path: str,
    ) -> list[Entry]:
        return list(
            self.session.scalars(
                select(Entry)
                .where(
                    Entry.parent_path == parent_path,
                    Entry.entry_type == "directory",
                )
                .order_by(
                    func.lower(Entry.name),
                    Entry.id,
                )
            )
        )

    def delete_stale(self, generation: int) -> int:
        result = self.session.execute(
            delete(Entry).where(
                Entry.scan_generation < generation
            )
        )
        return result.rowcount or 0

    def counts(self) -> tuple[int, int]:
        rows = {
            entry_type: count
            for entry_type, count in self.session.execute(
                select(
                    Entry.entry_type,
                    func.count(),
                ).group_by(Entry.entry_type)
            )
        }
        return (
            int(rows.get("file", 0)),
            int(rows.get("directory", 0)),
        )

    def page_for_entry(
        self,
        entry_id: int,
        *,
        page_size: int,
    ) -> int | None:
        selected = self.session.get(Entry, entry_id)
        if selected is None:
            return None
        order = (
            case(
                (
                    Entry.entry_type == "directory",
                    0,
                ),
                else_=1,
            ),
            func.lower(Entry.name),
            Entry.id,
        )
        ranked = (
            select(
                Entry.id,
                func.row_number()
                .over(order_by=order)
                .label("position"),
            )
            .where(
                Entry.parent_path
                == selected.parent_path
            )
            .subquery()
        )
        position = self.session.scalar(
            select(ranked.c.position).where(
                ranked.c.id == entry_id
            )
        )
        if position is None:
            return None
        return ((int(position) - 1) // page_size) + 1

    def search(
        self,
        query: str,
        *,
        page: int,
        page_size: int,
    ) -> Page[Entry]:
        query = query.strip()
        if not query:
            return Page([], 0, page, page_size)

        offset = (page - 1) * page_size
        if len(query) < 3:
            escaped = (
                query.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            predicate = Entry.name.ilike(
                f"%{escaped}%",
                escape="\\",
            )
            total = (
                self.session.scalar(
                    select(func.count())
                    .select_from(Entry)
                    .where(predicate)
                )
                or 0
            )
            rows = list(
                self.session.scalars(
                    select(Entry)
                    .where(predicate)
                    .order_by(
                        case(
                            (
                                Entry.entry_type
                                == "directory",
                                0,
                            ),
                            else_=1,
                        ),
                        func.lower(Entry.name),
                        Entry.id,
                    )
                    .offset(offset)
                    .limit(page_size)
                )
            )
            return Page(
                rows,
                total,
                page,
                page_size,
            )

        match_query = (
            '"'
            + query.replace('"', '""')
            + '"'
        )
        count_sql = text(
            """
            SELECT count(*)
            FROM entry_search
            WHERE entry_search MATCH :query
            """
        )
        rows_sql = text(
            """
            SELECT e.*
            FROM entry_search AS search_index
            JOIN entries AS e ON e.id = search_index.rowid
            WHERE entry_search MATCH :query
            ORDER BY bm25(entry_search),
                     CASE
                       WHEN e.entry_type = 'directory'
                       THEN 0 ELSE 1
                     END,
                     lower(e.name),
                     e.id
            LIMIT :limit OFFSET :offset
            """
        )
        total = int(
            self.session.execute(
                count_sql,
                {"query": match_query},
            ).scalar_one()
        )
        rows = list(
            self.session.scalars(
                select(Entry).from_statement(rows_sql),
                {
                    "query": match_query,
                    "limit": page_size,
                    "offset": offset,
                },
            )
        )
        return Page(
            rows,
            total,
            page,
            page_size,
        )
