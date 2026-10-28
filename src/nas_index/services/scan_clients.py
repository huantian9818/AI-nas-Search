from typing import Any, AsyncIterator

from nas_index.types import IndexedItem


class WebdavScanClient:
    """Use File Station for share discovery and WebDAV for traversal.

    Share visibility and user permissions still come from File Station's
    ``get_tree`` call; WebDAV is only a faster read-only directory lister.
    """

    def __init__(
        self,
        shares_client: Any,
        listing_client: Any,
    ):
        self.shares_client = shares_client
        self.listing_client = listing_client

    async def __aenter__(self) -> "WebdavScanClient":
        try:
            await self.shares_client.__aenter__()
        except BaseException:
            await self.listing_client.aclose()
            raise
        return self

    async def __aexit__(self, *exc_info) -> None:
        try:
            await self.shares_client.__aexit__(*exc_info)
        finally:
            await self.listing_client.aclose()

    async def list_shares(self) -> list[IndexedItem]:
        return await self.shares_client.list_shares()

    async def iter_children(
        self,
        path: str,
        *,
        page_size: int = 0,
    ) -> AsyncIterator[IndexedItem]:
        async for item in self.listing_client.iter_children(
            path
        ):
            yield item
