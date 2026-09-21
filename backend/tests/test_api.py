"""Smoke tests for the public API.

Runs against a throwaway SQLite file so it never touches backend/tcf.db.

The fixture builds the four-layer model by hand rather than calling the
backfill: these tests are about what the API returns, and a fixture that went
through the pipeline would fail for pipeline reasons.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

# Must be set before app.config is imported, since settings are cached.
#
# Everything the tests depend on is set explicitly, including the variables
# they expect to be ABSENT: config.py loads backend/.env into the process
# environment, so a developer with real Google credentials there would
# otherwise see a different result from CI.
_tmp = Path(tempfile.mkdtemp()) / "test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}"
os.environ["SECRET_KEY"] = "test-key-not-used-in-production"
os.environ["GOOGLE_CLIENT_ID"] = ""
os.environ["GOOGLE_CLIENT_SECRET"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (CoreSubject, Fingerprint, ListQuestion,  # noqa: E402
                        RawQuestion, Theme)


@pytest.fixture(scope="module")
def client():
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        theme = Theme(tache=2, name="Travel & tourism")
        db.add(theme)
        db.flush()
        subject = CoreSubject(theme_id=theme.id, name="holiday trip")
        db.add(subject)
        db.flush()

        # one question seen in two months, one seen in one, one for tache 3 -
        # enough to tell a per-month row from the question behind it
        recurring = Fingerprint(fingerprint="a" * 64, tache=2,
                                text="Question de tache 2, aout et janvier.",
                                theme_id=theme.id, core_subject_id=subject.id,
                                abstract="a recurring one",
                                first_seen="2026-01", last_seen="2026-08",
                                total_sightings=3, months_seen=2)
        once = Fingerprint(fingerprint="b" * 64, tache=2,
                           text="Question de tache 2, janvier seulement.",
                           first_seen="2026-01", last_seen="2026-01",
                           total_sightings=1, months_seen=1)
        other = Fingerprint(fingerprint="c" * 64, tache=3,
                            text="Question de tache 3.",
                            first_seen="2026-08", last_seen="2026-08",
                            total_sightings=1, months_seen=1)
        db.add_all([recurring, once, other])
        db.flush()

        db.add_all([
            RawQuestion(id="0320260820101", f_id=recurring.id, tache=2,
                        text=recurring.text, period="2026-08", partie=1, sujet=1,
                        source="reussir"),
            RawQuestion(id="0320260120101", f_id=recurring.id, tache=2,
                        text=recurring.text, period="2026-01", partie=1, sujet=1,
                        source="opal"),
            RawQuestion(id="0220260120102", f_id=once.id, tache=2, text=once.text,
                        period="2026-01", partie=1, sujet=2, source="opal"),
            RawQuestion(id="0320260830101", f_id=other.id, tache=3, text=other.text,
                        period="2026-08", partie=1, sujet=1, source="reussir"),
        ])
        db.flush()

        db.add_all([
            ListQuestion(f_id=recurring.id, tache=2, period="2026-08",
                         month_sightings=1, representative_raw_id="0320260820101"),
            ListQuestion(f_id=recurring.id, tache=2, period="2026-01",
                         month_sightings=1, representative_raw_id="0320260120101"),
            ListQuestion(f_id=once.id, tache=2, period="2026-01",
                         month_sightings=1, representative_raw_id="0220260120102"),
            ListQuestion(f_id=other.id, tache=3, period="2026-08",
                         month_sightings=1, representative_raw_id="0320260830101"),
        ])
        db.commit()
    with TestClient(app) as c:
        yield c


def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_meta_separates_questions_from_monthly_entries(client):
    body = client.get("/api/questions/meta").json()
    # two distinct Task 2 questions, but three rows on the timeline because one
    # of them recurred - conflating these is what the four-layer model prevents
    assert body["counts"] == {"tache2": 2, "tache3": 1}
    assert body["entries"] == {"tache2": 3, "tache3": 1}
    assert [t["name"] for t in body["themes"]] == ["Travel & tourism"]


def test_default_sort_is_newest_first(client):
    items = client.get("/api/questions?tache=2").json()["items"]
    assert [i["period"] for i in items] == ["2026-08", "2026-01", "2026-01"]


def test_recurring_question_is_one_identity_across_months(client):
    items = client.get("/api/questions?tache=2").json()["items"]
    august = next(i for i in items if i["period"] == "2026-08")
    january = next(i for i in items if i["f_id"] == august["f_id"]
                   and i["period"] == "2026-01")
    # different rows, same question - so one label reaches both
    assert august["id"] != january["id"]
    assert august["theme"] == january["theme"] == "Travel & tourism"
    assert august["months_seen"] == 2


def test_period_counts_cover_the_whole_filter(client):
    body = client.get("/api/questions?tache=2&per_page=1").json()
    # one row on the page, but the heading must report the month's real size
    assert len(body["items"]) == 1
    assert body["period_counts"] == {"2026-08": 1, "2026-01": 2}


def test_tache_filter_is_enforced(client):
    body = client.get("/api/questions?tache=3").json()
    assert body["total"] == 1 and body["items"][0]["tache"] == 3


def test_tache_must_be_2_or_3(client):
    assert client.get("/api/questions?tache=1").status_code == 422


def test_search_and_label_filters(client):
    assert client.get("/api/questions?tache=2&q=seulement").json()["total"] == 1
    assert client.get("/api/questions?tache=2&theme=Travel %26 tourism").json()["total"] == 2
    assert client.get("/api/questions?tache=2&core_subject=holiday trip").json()["total"] == 2
    assert client.get("/api/questions?tache=2&period=2026-01").json()["total"] == 2


def test_pagination_envelope(client):
    body = client.get("/api/questions?tache=2&per_page=1").json()
    assert (body["total"], body["pages"], len(body["items"])) == (3, 3, 1)


def test_question_detail_and_404(client):
    first = client.get("/api/questions?tache=2").json()["items"][0]
    detail = client.get(f"/api/questions/{first['id']}").json()
    assert detail["f_id"] == first["f_id"]
    assert client.get("/api/questions/99999999").status_code == 404


def test_months_endpoint_lists_every_appearance(client):
    first = client.get("/api/questions?tache=2").json()["items"][0]
    assert client.get(f"/api/questions/{first['id']}/months").json() == \
        ["2026-08", "2026-01"]


def test_admin_is_refused_without_the_flag(client):
    tok = client.post("/api/auth/signup",
                      json={"email": "nobody@example.com",
                            "password": "hunter2hunter2"}).json()["access_token"]
    assert client.get("/api/admin/vocabulary").status_code == 401
    assert client.get("/api/admin/vocabulary",
                      headers={"Authorization": f"Bearer {tok}"}).status_code == 403


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
