import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from nas_index.models import Entry, SyncRun
from nas_index.qnap.client import QnapClient
from nas_index.repositories.nas import NasRepository
from nas_index.services.scanner import Scanner
from nas_index.types import NasConnection


@pytest.mark.asyncio
async def test_real_qnap_adapter_and_scanner_index_complete_tree(
    database,
):
    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        if request.url.path.endswith(
            "authLogin.cgi"
        ):
            return httpx.Response(
                200,
                text=(
                    "<QDocRoot>"
                    "<authPassed>1</authPassed>"
                    "<authSid>sid1</authSid>"
                    "</QDocRoot>"
                ),
            )
        if request.url.path.endswith(
            "authLogout.cgi"
        ):
            return httpx.Response(
                200,
                text="<QDocRoot />",
            )
        if request.url.params.get("func") == "get_tree":
            return httpx.Response(
                200,
                json=[
                    {
                        "text": "Public",
                        "id": "/Public",
                        "iconCls": "folder",
                        "cls": "r",
                    }
                ],
            )
        if (
            request.url.params.get("path")
            == "/Public"
        ):
            return httpx.Response(
                200,
                json={
                    "total": 1,
                    "datas": [
                        {
                            "filename": "docs",
                            "isfolder": 1,
                            "filesize": "4096",
                            "epochmt": 1,
                        }
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "total": 1,
                "datas": [
                    {
                        "filename": "readme.txt",
                        "isfolder": 0,
                        "filesize": "7",
                        "epochmt": 2,
                    }
                ],
            },
        )

    connection = NasConnection(
        "http://nas.local",
        8080,
        False,
        "indexer",
        "secret",
    )
    with Session(database) as session:
        nas_id = NasRepository(session).create_server(
            name="Test NAS",
            base_url="http://nas.local",
            port=8080,
            use_https=False,
            enabled=True,
            sync_interval_minutes=30,
            full_resync_interval_hours=24,
            username="indexer",
            password="secret",
        ).id
        session.commit()

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http:
        scanner = Scanner(
            database,
            lambda: QnapClient(
                connection,
                http=http,
            ),
                page_size=100,
                batch_size=2,
                nas_id=nas_id,
            )
        await scanner.run()

    with Session(database) as session:
        assert set(
            session.scalars(
                select(Entry.full_path)
            )
        ) == {
            "/Public",
            "/Public/docs",
            "/Public/docs/readme.txt",
        }
        assert (
            session.scalar(
                select(SyncRun.status)
            )
            == "succeeded"
        )
