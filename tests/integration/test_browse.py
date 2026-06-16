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


def test_browse_tree_keeps_current_branch_open_without_expand_button(
    client,
    web_seeded_entries,
):
    response = client.get(
        "/browse",
        params={"path": "/Public/资料"},
    )

    assert response.status_code == 200
    assert 'href="/browse?path=/Public"' in response.text
    assert (
        'href="/browse?path=/Public/%E8%B5%84%E6%96%99"'
        in response.text
    )
    assert "tree-list-nested" in response.text
    assert "tree-link is-ancestor" in response.text
    assert "tree-link is-current" in response.text
    assert "nested-only.txt" in response.text
    assert "展开" not in response.text
