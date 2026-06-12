def test_browse_page_lists_direct_children(
    client,
    web_seeded_entries,
):
    response = client.get(
        "/browse",
        params={"path": "/Public"},
    )

    assert response.status_code == 200
    assert "年度项目计划.docx" in response.text
    assert "nested-only.txt" not in response.text


def test_tree_endpoint_returns_only_child_directories(
    client,
    web_seeded_entries,
):
    response = client.get(
        "/browse/tree",
        params={"path": "/Public"},
    )

    assert response.status_code == 200
    assert "资料" in response.text
    assert "年度项目计划.docx" not in response.text
