def test_search_page_returns_name_and_full_path(
    client,
    web_seeded_entries,
):
    response = client.get(
        "/search",
        params={"q": "项目"},
    )

    assert response.status_code == 200
    assert "年度项目计划.docx" in response.text
    assert "/Public/年度项目计划.docx" in response.text


def test_search_result_links_to_parent_and_selected_entry(
    client,
    web_seeded_entries,
):
    response = client.get(
        "/search",
        params={"q": "项目"},
    )

    assert "/browse?path=" in response.text
    assert "&amp;selected=" in response.text
