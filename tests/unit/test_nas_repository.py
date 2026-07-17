from sqlalchemy.orm import Session

from nas_index.repositories.nas import NasRepository


def test_create_and_update_nas_server(database):
    with Session(database) as session:
        repository = NasRepository(session)
        server = repository.create_server(
            name="Office NAS",
            base_url="http://10.0.0.2",
            port=8080,
            use_https=False,
            skip_tls_verify=False,
            enabled=True,
            sync_interval_minutes=15,
            username="indexer",
            password="secret",
        )
        session.commit()

    with Session(database) as session:
        repository = NasRepository(session)
        loaded = repository.get_server(server.id)
        credential = repository.get_credential(server.id)

        assert loaded is not None
        assert loaded.name == "Office NAS"
        assert loaded.sync_interval_minutes == 15
        assert loaded.skip_tls_verify is False
        assert credential is not None
        assert credential.username == "indexer"
        assert credential.password == "secret"

        updated = repository.update_server(
            server.id,
            name="Office NAS Renamed",
            base_url="https://nas.example.com",
            port=443,
            use_https=True,
            skip_tls_verify=True,
            enabled=False,
            sync_interval_minutes=60,
            username="new-indexer",
            password="",
        )
        session.commit()

        credential = repository.get_credential(server.id)
        assert updated.name == "Office NAS Renamed"
        assert updated.enabled is False
        assert updated.skip_tls_verify is True
        assert credential is not None
        assert credential.username == "new-indexer"
        assert credential.password == "secret"


def test_list_enabled_servers(database):
    with Session(database) as session:
        repository = NasRepository(session)
        repository.create_server(
            name="Enabled",
            base_url="http://enabled.local",
            port=8080,
            use_https=False,
            skip_tls_verify=False,
            enabled=True,
            sync_interval_minutes=30,
            username="indexer",
            password="secret",
        )
        repository.create_server(
            name="Disabled",
            base_url="http://disabled.local",
            port=8080,
            use_https=False,
            skip_tls_verify=False,
            enabled=False,
            sync_interval_minutes=30,
            username="indexer",
            password="secret",
        )
        session.commit()

    with Session(database) as session:
        names = [
            server.name
            for server in NasRepository(session).list_enabled_servers()
        ]

    assert names == ["Enabled"]
