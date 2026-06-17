from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Generic, TypeVar

from sqlalchemy import bindparam, case, delete, func, select, text
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


DEFAULT_NAS_ID = 1


def share_path_from_full_path(full_path: str) -> str:
    parts = [
        part
        for part in full_path.replace("\\", "/").split("/")
        if part
    ]
    if not parts:
        return "/"
    return f"/{parts[0]}"


class EntryRepository:
    def __init__(self, session: Session):
        self.session = session

    def upsert_batch(
        self,
        nas_id_or_items: int | list[IndexedItem],
        items_or_generation: list[IndexedItem] | int | None = None,
        generation: int | None = None,
    ) -> None:
        if isinstance(nas_id_or_items, list):
            nas_id = DEFAULT_NAS_ID
            items = nas_id_or_items
            generation = (
                int(items_or_generation)
                if generation is None
                else generation
            )
        else:
            nas_id = int(nas_id_or_items)
            items = items_or_generation
            if generation is None:
                raise TypeError("generation is required")
        if not isinstance(items, list):
            raise TypeError("items must be a list")
        if not items:
            return
        now = datetime.now(UTC)
        values = [
            {
                "name": item.name,
                "nas_id": nas_id,
                "share_path": item.share_path
                or share_path_from_full_path(item.full_path),
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
                index_elements=[Entry.nas_id, Entry.full_path],
                set_={
                    "share_path": statement.excluded.share_path,
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
        nas_id = DEFAULT_NAS_ID
        return self.get_by_nas_path(nas_id, full_path)

    def get_by_nas_path(
        self,
        nas_id: int,
        full_path: str,
    ) -> Entry | None:
        return self.session.scalar(
            select(Entry).where(
                Entry.nas_id == nas_id,
                Entry.full_path == full_path
            )
        )

    def list_children(
        self,
        nas_id_or_parent_path: int | str,
        parent_path: str | None = None,
        *,
        allowed_share_paths: tuple[str, ...] | None = None,
        page: int,
        page_size: int,
    ) -> Page[Entry]:
        if parent_path is None:
            nas_id = DEFAULT_NAS_ID
            parent_path = str(nas_id_or_parent_path)
        else:
            nas_id = int(nas_id_or_parent_path)
        if allowed_share_paths is not None and not allowed_share_paths:
            return Page([], 0, page, page_size)
        predicate = [
            Entry.nas_id == nas_id,
            Entry.parent_path == parent_path,
        ]
        if allowed_share_paths is not None:
            predicate.append(Entry.share_path.in_(allowed_share_paths))
        total = (
            self.session.scalar(
                select(func.count())
                .select_from(Entry)
                .where(*predicate)
            )
            or 0
        )
        rows = list(
            self.session.scalars(
                select(Entry)
                .where(*predicate)
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

    def list_children_legacy(
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
        nas_id_or_parent_path: int | str,
        parent_path: str | None = None,
        *,
        allowed_share_paths: tuple[str, ...] | None = None,
    ) -> list[Entry]:
        if parent_path is None:
            nas_id = DEFAULT_NAS_ID
            parent_path = str(nas_id_or_parent_path)
        else:
            nas_id = int(nas_id_or_parent_path)
        if allowed_share_paths is not None and not allowed_share_paths:
            return []
        predicate = [
            Entry.nas_id == nas_id,
            Entry.parent_path == parent_path,
            Entry.entry_type == "directory",
        ]
        if allowed_share_paths is not None:
            predicate.append(Entry.share_path.in_(allowed_share_paths))
        return list(
            self.session.scalars(
                select(Entry)
                .where(*predicate)
                .order_by(
                    func.lower(Entry.name),
                    Entry.id,
                )
            )
        )

    def list_child_directories_legacy(
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

    def delete_stale(
        self,
        nas_id_or_generation: int,
        generation: int | None = None,
    ) -> int:
        if generation is None:
            nas_id = DEFAULT_NAS_ID
            generation = nas_id_or_generation
        else:
            nas_id = nas_id_or_generation
        result = self.session.execute(
            delete(Entry).where(
                Entry.nas_id == nas_id,
                Entry.scan_generation < generation
            )
        )
        return result.rowcount or 0

    def replace_children(
        self,
        nas_id: int,
        parent_path: str,
        observed_full_paths: set[str],
    ) -> int:
        predicate = [
            Entry.nas_id == nas_id,
            Entry.parent_path == parent_path,
        ]
        if observed_full_paths:
            predicate.append(
                Entry.full_path.not_in(observed_full_paths)
            )
        result = self.session.execute(
            delete(Entry).where(*predicate)
        )
        return result.rowcount or 0

    def counts(
        self,
        *,
        nas_id: int | None = None,
        allowed_share_paths: tuple[str, ...] | None = None,
    ) -> tuple[int, int]:
        predicates = []
        if nas_id is not None:
            predicates.append(Entry.nas_id == nas_id)
        if allowed_share_paths is not None:
            if not allowed_share_paths:
                return (0, 0)
            predicates.append(
                Entry.share_path.in_(allowed_share_paths)
            )
        rows = {
            entry_type: count
            for entry_type, count in self.session.execute(
                select(
                    Entry.entry_type,
                    func.count(),
                )
                .where(*predicates)
                .group_by(Entry.entry_type)
            )
        }
        return (
            int(rows.get("file", 0)),
            int(rows.get("directory", 0)),
        )

    def list_share_paths(self, nas_id: int) -> tuple[str, ...]:
        return tuple(
            self.session.scalars(
                select(Entry.share_path)
                .where(Entry.nas_id == nas_id)
                .distinct()
                .order_by(Entry.share_path)
            )
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
        nas_id: int = DEFAULT_NAS_ID,
        allowed_share_paths: tuple[str, ...] | None = None,
        page: int,
        page_size: int,
    ) -> Page[Entry]:
        query = query.strip()
        if not query:
            return Page([], 0, page, page_size)
        if allowed_share_paths is not None and not allowed_share_paths:
            return Page([], 0, page, page_size)

        offset = (page - 1) * page_size
        if len(query) < 3:
            escaped = (
                query.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            predicate = [
                Entry.nas_id == nas_id,
                Entry.name.ilike(
                    f"%{escaped}%",
                    escape="\\",
                ),
            ]
            if allowed_share_paths is not None:
                predicate.append(
                    Entry.share_path.in_(allowed_share_paths)
                )
            total = (
                self.session.scalar(
                    select(func.count())
                    .select_from(Entry)
                    .where(*predicate)
                )
                or 0
            )
            rows = list(
                self.session.scalars(
                    select(Entry)
                    .where(*predicate)
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
            CROSS JOIN entries AS e ON e.id = entry_search.rowid
            WHERE entry_search MATCH :query
              AND e.nas_id = :nas_id
              AND (
                :filter_shares = 0
                OR e.share_path IN :share_paths
              )
            """
        ).bindparams(bindparam("share_paths", expanding=True))
        rows_sql = text(
            """
            SELECT e.*
            FROM entry_search
            CROSS JOIN entries AS e ON e.id = entry_search.rowid
            WHERE entry_search MATCH :query
              AND e.nas_id = :nas_id
              AND (
                :filter_shares = 0
                OR e.share_path IN :share_paths
              )
            ORDER BY bm25(entry_search),
                     CASE
                       WHEN e.entry_type = 'directory'
                       THEN 0 ELSE 1
                     END,
                     lower(e.name),
                     e.id
            LIMIT :limit OFFSET :offset
            """
        ).bindparams(bindparam("share_paths", expanding=True))
        share_paths = list(allowed_share_paths or ("/",))
        params = {
            "query": match_query,
            "nas_id": nas_id,
            "filter_shares": 1
            if allowed_share_paths is not None
            else 0,
            "share_paths": share_paths,
        }
        total = int(
            self.session.execute(
                count_sql,
                params,
            ).scalar_one()
        )
        rows = list(
            self.session.scalars(
                select(Entry).from_statement(rows_sql),
                {
                    **params,
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

    def search_all(
        self,
        query: str,
        *,
        nas_id: int = DEFAULT_NAS_ID,
        allowed_share_paths: tuple[str, ...] | None = None,
    ) -> Page[Entry]:
        query = query.strip()
        if not query:
            return Page([], 0, 1, 0)
        if allowed_share_paths is not None and not allowed_share_paths:
            return Page([], 0, 1, 0)

        if len(query) < 3:
            escaped = (
                query.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            predicate = [
                Entry.nas_id == nas_id,
                Entry.name.ilike(
                    f"%{escaped}%",
                    escape="\\",
                ),
            ]
            if allowed_share_paths is not None:
                predicate.append(
                    Entry.share_path.in_(allowed_share_paths)
                )
            rows = list(
                self.session.scalars(
                    select(Entry)
                    .where(*predicate)
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
                )
            )
            return Page(
                rows,
                len(rows),
                1,
                len(rows),
            )

        match_query = (
            '"'
            + query.replace('"', '""')
            + '"'
        )
        rows_sql = text(
            """
            SELECT e.*
            FROM entry_search
            CROSS JOIN entries AS e ON e.id = entry_search.rowid
            WHERE entry_search MATCH :query
              AND e.nas_id = :nas_id
              AND (
                :filter_shares = 0
                OR e.share_path IN :share_paths
              )
            ORDER BY bm25(entry_search),
                     CASE
                       WHEN e.entry_type = 'directory'
                       THEN 0 ELSE 1
                     END,
                     lower(e.name),
                     e.id
            """
        ).bindparams(bindparam("share_paths", expanding=True))
        share_paths = list(allowed_share_paths or ("/",))
        params = {
            "query": match_query,
            "nas_id": nas_id,
            "filter_shares": 1
            if allowed_share_paths is not None
            else 0,
            "share_paths": share_paths,
        }
        rows = list(
            self.session.scalars(
                select(Entry).from_statement(rows_sql),
                params,
            )
        )
        return Page(
            rows,
            len(rows),
            1,
            len(rows),
        )
