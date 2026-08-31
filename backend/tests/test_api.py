"""Smoke tests for the phase 0 API.

Runs against a throwaway SQLite file so it never touches backend/tcf.db.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

# must be set before app.config is imported, since settings are cached
_tmp = Path(tempfile.mkdtemp()) / "test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}"
os.environ["SECRET_KEY"] = "test-key-not-used-in-production"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Question  # noqa: E402


@pytest.fixture(scope="module")
def client():
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        db.add_all([
            Question(id="0320260820101", tache=2, text="Question de tache 2, aout.",
                     source="reussir", year=2026, month=8, period="2026-08",
                     partie=1, sujet=1, occurrences=3),
            Question(id="0320260120101", tache=2, text="Question de tache 2, janvier.",
                     source="opal", year=2026, month=1, period="2026-01",
                     partie=1, sujet=1, occurrences=1),
            Question(id="0320260830101", tache=3, text="Question de tache 3.",
                     source="reussir", year=2026, month=8, period="2026-08",
                     partie=1, sujet=1, occurrences=1),
        ])
        db.commit()
    with TestClient(app) as c:
        yield c


def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_meta_counts(client):
    body = client.get("/api/questions/meta").json()
    assert body["counts"] == {"tache2": 2, "tache3": 1}
    assert set(body["sources"]) == {"reussir", "opal"}


def test_default_sort_is_newest_first(client):
    items = client.get("/api/questions?tache=2").json()["items"]
    assert [i["period"] for i in items] == ["2026-08", "2026-01"]


def test_tache_filter_is_enforced(client):
    body = client.get("/api/questions?tache=3").json()
    assert body["total"] == 1 and body["items"][0]["tache"] == 3


def test_tache_must_be_2_or_3(client):
    assert client.get("/api/questions?tache=1").status_code == 422


def test_search_and_source_filter(client):
    assert client.get("/api/questions?tache=2&q=janvier").json()["total"] == 1
    assert client.get("/api/questions?tache=2&source=opal").json()["total"] == 1


def test_pagination_envelope(client):
    body = client.get("/api/questions?tache=2&per_page=1").json()
    assert (body["total"], body["pages"], len(body["items"])) == (2, 2, 1)


def test_question_detail_and_404(client):
    assert client.get("/api/questions/0320260820101").json()["occurrences"] == 3
    assert client.get("/api/questions/nope").status_code == 404


def test_signup_login_me_flow(client):
    r = client.post("/api/auth/signup",
                    json={"email": "A@Example.com", "password": "hunter2hunter2"})
    assert r.status_code == 201
    token = r.json()["access_token"]

    # email is normalised, so the same address in another case is a conflict
    assert client.post("/api/auth/signup",
                       json={"email": "a@example.com",
                             "password": "hunter2hunter2"}).status_code == 409

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["email"] == "a@example.com"

    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me",
                      headers={"Authorization": "Bearer garbage"}).status_code == 401

    assert client.post("/api/auth/login",
                       json={"email": "a@example.com", "password": "wrong"}).status_code == 401
    assert client.post("/api/auth/login",
                       json={"email": "a@example.com",
                             "password": "hunter2hunter2"}).status_code == 200


def test_short_password_rejected(client):
    assert client.post("/api/auth/signup",
                       json={"email": "b@example.com", "password": "short"}).status_code == 422


def test_change_password(client):
    tok = client.post("/api/auth/signup",
                      json={"email": "c@example.com",
                            "password": "hunter2hunter2"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert client.post("/api/auth/change-password", headers=h,
                       json={"current_password": "nope",
                             "new_password": "brandnewpass1"}).status_code == 401
    assert client.post("/api/auth/change-password", headers=h,
                       json={"current_password": "hunter2hunter2",
                             "new_password": "brandnewpass1"}).status_code == 204
    assert client.post("/api/auth/login",
                       json={"email": "c@example.com",
                             "password": "brandnewpass1"}).status_code == 200


def test_google_disabled_by_default(client):
    assert client.get("/api/auth/google/config").json() == {"enabled": False}
    assert client.get("/api/auth/google/login").status_code == 503
