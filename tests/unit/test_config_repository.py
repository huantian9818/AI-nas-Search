from sqlalchemy.orm import Session

from nas_index.repositories.config import ConfigRepository
from nas_index.types import NasConnection


def test_save_and_load_single_configuration(database):
    connection = NasConnection(
        base_url="https://192.168.1.20",
        port=443,
        use_https=True,
        username="indexer",
        password="secret",
    )
    with Session(database) as session:
        repository = ConfigRepository(session)
        repository.save(connection)
        session.commit()
        saved = repository.get()

    assert saved == connection
    assert saved.endpoint == "https://192.168.1.20:443"


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
            )
        )
        repository.save(
            NasConnection(
                "http://nas.local",
                8080,
                False,
                "indexer",
                "",
            )
        )
        session.commit()

        assert repository.get().password == "secret"
