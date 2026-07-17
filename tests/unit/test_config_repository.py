from sqlalchemy.orm import Session

from nas_index.repositories.config import ConfigRepository
from nas_index.repositories.nas import NasRepository
from nas_index.types import NasConnection


def test_save_and_load_single_configuration(database):
    connection = NasConnection(
        base_url="https://192.168.1.20",
        port=443,
        use_https=True,
        username="indexer",
        password="secret",
        skip_tls_verify=True,
    )
    with Session(database) as session:
        repository = ConfigRepository(session)
        repository.save(connection)
        session.commit()
        saved = repository.get()

    assert saved == connection
    assert saved.endpoint == "https://192.168.1.20:443"
    assert saved.skip_tls_verify is True


def test_blank_password_preserves_saved_password(database):
    with Session(database) as session:
        repository = ConfigRepository(session)
        repository.save(
            NasConnection(
                "http://nas.local",
                8080,
                False,
                "indexer",
                "secret",
                False,
            )
        )
        repository.save(
            NasConnection(
                "http://nas.local",
                8080,
                False,
                "indexer",
                "",
                False,
            )
        )
        session.commit()

        assert repository.get().password == "secret"


def test_get_existing_multi_nas_connection_defaults_skip_tls_verify(
    database,
):
    with Session(database) as session:
        NasRepository(session).create_server(
            name="Office NAS",
            base_url="https://nas.local",
            port=5001,
            use_https=True,
            skip_tls_verify=False,
            enabled=True,
            sync_interval_minutes=30,
            username="indexer",
            password="secret",
        )
        session.commit()

    with Session(database) as session:
        loaded = ConfigRepository(session).get()

    assert loaded is not None
    assert loaded.skip_tls_verify is False
