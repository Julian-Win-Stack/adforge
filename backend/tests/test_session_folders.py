"""Folders for sessions, tested the way the sidebar uses them: make a named folder, move a
session into it and out again, rename the folder, and remove it without losing a session."""

import pytest
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


def _folder(api: APIClient, name: str) -> str:
    made = api.post("/api/folders/", {"name": name}, format="json")
    assert made.status_code == 201, made.json()
    return str(made.json()["id"])


def _session(api: APIClient) -> str:
    return str(api.post("/api/sessions/", {}, format="json").json()["id"])


def test_a_new_folder_is_listed_with_its_name(api: APIClient) -> None:
    folder = _folder(api, "Round 15")

    listed = api.get("/api/folders/").json()

    assert [(one["id"], one["name"]) for one in listed] == [(folder, "Round 15")]


def test_a_folder_name_is_trimmed_and_cannot_be_blank(api: APIClient) -> None:
    assert api.post("/api/folders/", {"name": "  Round 2  "}, format="json").json()["name"] == (
        "Round 2"
    )

    blank = api.post("/api/folders/", {"name": "   "}, format="json")

    assert blank.status_code == 400
    assert api.get("/api/folders/").json()[0]["name"] == "Round 2"


def test_two_folders_cannot_share_a_name(api: APIClient) -> None:
    _folder(api, "Round 3")

    again = api.post("/api/folders/", {"name": "round 3"}, format="json")

    assert again.status_code == 400
    assert "Round 3" in str(again.json())


def test_a_session_moves_into_a_folder_and_back_out(api: APIClient) -> None:
    folder = _folder(api, "Round 4")
    session = _session(api)

    moved = api.patch(f"/api/sessions/{session}/", {"folder": folder}, format="json")

    assert moved.status_code == 200
    assert moved.json()["folder"] == folder
    assert api.get("/api/sessions/").json()[0]["folder"] == folder

    out = api.patch(f"/api/sessions/{session}/", {"folder": None}, format="json")

    assert out.json()["folder"] is None


def test_moving_a_session_keeps_its_name(api: APIClient) -> None:
    folder = _folder(api, "Round 5")
    session = _session(api)
    api.patch(f"/api/sessions/{session}/", {"name": "Mug ad"}, format="json")

    moved = api.patch(f"/api/sessions/{session}/", {"folder": folder}, format="json")

    assert moved.json()["name"] == "Mug ad"


def test_a_session_cannot_move_into_a_folder_that_isnt_there(api: APIClient) -> None:
    session = _session(api)

    moved = api.patch(
        f"/api/sessions/{session}/",
        {"folder": "00000000-0000-0000-0000-000000000000"},
        format="json",
    )

    assert moved.status_code == 400
    assert api.get("/api/sessions/").json()[0]["folder"] is None


def test_a_change_to_a_session_has_to_say_what_changes(api: APIClient) -> None:
    session = _session(api)

    assert api.patch(f"/api/sessions/{session}/", {}, format="json").status_code == 400


def test_a_folder_can_be_renamed(api: APIClient) -> None:
    folder = _folder(api, "Rnd 6")

    renamed = api.patch(f"/api/folders/{folder}/", {"name": "Round 6"}, format="json")

    assert renamed.status_code == 200
    assert api.get("/api/folders/").json()[0]["name"] == "Round 6"


def test_a_folder_can_keep_its_own_name_in_other_letters(api: APIClient) -> None:
    folder = _folder(api, "round 7")

    renamed = api.patch(f"/api/folders/{folder}/", {"name": "Round 7"}, format="json")

    assert renamed.status_code == 200


def test_a_folder_cannot_be_renamed_to_another_folders_name(api: APIClient) -> None:
    _folder(api, "Round 8")
    other = _folder(api, "Round 9")

    renamed = api.patch(f"/api/folders/{other}/", {"name": "Round 8"}, format="json")

    assert renamed.status_code == 400


def test_removing_a_folder_keeps_its_sessions_outside_any_folder(api: APIClient) -> None:
    folder = _folder(api, "Round 10")
    session = _session(api)
    api.patch(f"/api/sessions/{session}/", {"folder": folder}, format="json")

    removed = api.delete(f"/api/folders/{folder}/")

    assert removed.status_code == 204
    assert api.get("/api/folders/").json() == []
    [kept] = api.get("/api/sessions/").json()
    assert (kept["id"], kept["folder"]) == (session, None)


def test_a_folder_that_isnt_there_says_so(api: APIClient) -> None:
    missing = "00000000-0000-0000-0000-000000000000"

    assert api.patch(f"/api/folders/{missing}/", {"name": "x"}, format="json").status_code == 404
    assert api.delete(f"/api/folders/{missing}/").status_code == 404
