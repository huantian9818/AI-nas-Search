from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from nas_index.repositories.nas import NasRepository
from nas_index.repositories.syncs import SyncRepository


def _create_nas(session: Session) -> int:
    return NasRepository(session).create_server(
        name="Office",
        base_url="http://nas.local",
        port=8080,
        use_https=False,
        enabled=True,
        sync_interval_minutes=30,
        username="indexer",
        password="secret",
    ).id


def test_sync_repository_tracks_run_and_share_state(database):
    now = datetime(2026, 1, 1, tzinfo=UTC)
    with Session(database) as session:
        nas_id = _create_nas(session)
        repository = SyncRepository(session)
        run = repository.create_run(
            nas_id=nas_id,
            scope="share",
            share_path="/Public",
        )
        repository.ensure_share_state(
            nas_id=nas_id,
            share_path="/Public",
            next_sync_at=now,
        )
        repository.progress(
            run.id,
            processed=5,
            current_path="/Public",
        )
        repository.succeed(run.id, processed=10)
        repository.mark_share_succeeded(
            nas_id=nas_id,
            share_path="/Public",
            generation=run.generation,
            next_sync_at=now + timedelta(minutes=30),
            full=True,
        )
        session.commit()

        latest = repository.latest_for_nas(nas_id)
        state = repository.get_share_state(
            nas_id,
            "/Public",
        )

        assert latest is not None
        assert latest.status == "succeeded"
        assert latest.processed_entries == 10
        assert state is not None
        assert state.status == "succeeded"
        assert state.last_generation == run.generation
        assert state.next_sync_at == (
            now + timedelta(minutes=30)
        ).replace(tzinfo=None)
