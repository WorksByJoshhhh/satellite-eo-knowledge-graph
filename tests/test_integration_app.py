"""
End-to-end tests of the WEB APP against the real Oracle database:
every page is opened and every form is submitted through Flask's test client,
exactly as a browser would, but using the real data and real driver types.
Skipped unless EO_DB_USER / EO_DB_PASSWORD are set (.env). Each test cleans up after itself.
"""
import re

import pytest

import config
import db

pytestmark = pytest.mark.skipif(not (config.DB_USER and config.DB_PASSWORD),
                                reason="set EO_DB_USER / EO_DB_PASSWORD to run database tests")


@pytest.fixture(scope="module")
def client():
    from app import create_app
    app = create_app(init_db=True)
    app.config["TESTING"] = True
    return app.test_client()


def ok(client, url):
    r = client.get(url)
    html = r.get_data(as_text=True)
    assert r.status_code == 200, f"{url} -> HTTP {r.status_code}\n{html[:500]}"
    assert "Traceback" not in html and "Something went wrong" not in html, url
    return html


# ---------------------------------------------------------------- every page renders
def test_every_page_renders_with_real_data(client):
    img = db.scalar("SELECT MIN(image_id) FROM image_frame")
    loc = db.scalar("SELECT location_id FROM location WHERE location_name = 'Kisangani Rainforest'")
    sat = db.scalar("SELECT MIN(satellite_id) FROM satellite")
    for url in ["/", "/images", f"/images/{img}", "/locations", f"/locations/{loc}", "/finder",
                "/alerts", "/ask", "/detections/new", f"/detections/new?image_id={img}",
                "/satellites", "/satellites/new", f"/satellites/{sat}/edit", "/audit",
                "/audit?table=DETECTED_OBJECT", "/audit?table=ALERT"]:
        ok(client, url)
    assert client.get("/health").get_json()["status"] == "ok"


def test_every_question_result_page_renders(client):
    for qid in [r["query_id"] for r in db.query("SELECT query_id FROM query_log")]:
        html = ok(client, f"/ask/{qid}")
        assert "Answer confidence" in html and "α=" in html


def test_every_image_detail_renders_attributes(client):
    # all 60+ images, and JSON attributes are shown (not silently empty)
    found_attrs = False
    for iid in [r["image_id"] for r in db.query("SELECT image_id FROM image_frame")]:
        html = ok(client, f"/images/{iid}")
        found_attrs = found_attrs or "canopy_density=" in html or "vessel_class=" in html
    assert found_attrs


def test_every_location_profile_renders(client):
    for lid in [r["location_id"] for r in db.query("SELECT location_id FROM location")]:
        ok(client, f"/locations/{lid}")


def test_missing_records_are_404(client):
    for url in ["/images/987654", "/locations/987654", "/ask/987654", "/satellites/987654/edit"]:
        assert client.get(url).status_code == 404, url


# ---------------------------------------------------------------- read features
def test_finder_default_is_ten_events(client):
    assert "10 event(s) found" in ok(client, "/finder")


def test_image_filters_and_injection_attempt(client):
    html = ok(client, "/images?satellite=Sentinel-2A&max_cloud=20")
    assert "Sentinel-2A" in html
    html = ok(client, "/images?location=x%27%20OR%20%271%27%3D%271")
    assert "No images match these filters" in html
    assert "must be at most 100" in ok(client, "/images?max_cloud=150")


# ---------------------------------------------------------------- write features
def test_ask_end_to_end_through_the_ui(client):
    r = client.post("/ask", data={"question": "UI test: deforestation near rivers in Q3",
                                  "concept": "deforestation", "alpha": "0.5", "beta": "0.3", "gamma": "0.2",
                                  "top_k": "5", "date_from": "2026-07-01", "date_to": "2026-09-30", "user_id": "1"})
    assert r.status_code == 302, r.get_data(as_text=True)[:500]
    qid = int(r.headers["Location"].rstrip("/").split("/")[-1])
    try:
        html = ok(client, f"/ask/{qid}")
        assert "Answer based on 5 evidence objects" in html
        assert db.scalar("SELECT COUNT(*) FROM query_evidence WHERE query_id = :q", {"q": qid}) == 5
    finally:
        db.execute("DELETE FROM query_log WHERE query_id = :q", {"q": qid})


@pytest.mark.parametrize("concept", ["intact_forest", "bare_soil", "vessel", "building", "water"])
def test_ask_every_concept(client, concept):
    r = client.post("/ask", data={"question": f"UI test concept {concept}", "concept": concept,
                                  "alpha": "1", "beta": "1", "gamma": "1", "top_k": "3"})
    assert r.status_code == 302, r.get_data(as_text=True)[:500]
    qid = int(r.headers["Location"].rstrip("/").split("/")[-1])
    try:
        ok(client, f"/ask/{qid}")
    finally:
        db.execute("DELETE FROM query_log WHERE query_id = :q", {"q": qid})


def test_run_change_analysis_through_the_ui(client):
    before = db.scalar("SELECT COUNT(*) FROM alert")
    html = client.post("/alerts/run", data={"min_loss": "10", "min_conf": "0.7", "max_cloud": "40"},
                       follow_redirects=True).get_data(as_text=True)
    assert "compute_changes:" in html and "raise_alerts:" in html
    assert db.scalar("SELECT COUNT(*) FROM alert") == before        # defaults raise no new alerts


def test_resolve_and_restore_alert_through_the_ui(client):
    a = db.query_one("SELECT alert_id, alert_status, assigned_user_id FROM alert ORDER BY alert_id FETCH FIRST 1 ROWS ONLY")
    try:
        html = client.post(f"/alerts/{a['alert_id']}", data={"alert_status": "RESOLVED", "assigned_user_id": "1"},
                           follow_redirects=True).get_data(as_text=True)
        assert "updated to RESOLVED" in html
        assert db.scalar("SELECT resolved_at FROM alert WHERE alert_id = :i", {"i": a["alert_id"]}) is not None
    finally:
        db.execute("UPDATE alert SET alert_status = :s, assigned_user_id = :u WHERE alert_id = :i",
                   {"s": a["alert_status"], "u": a["assigned_user_id"], "i": a["alert_id"]})


def test_satellite_crud_through_the_ui(client):
    name = "UI-Test-Sat"
    db.execute("DELETE FROM satellite WHERE satellite_name LIKE 'UI-Test-Sat%'")
    form = {"satellite_name": name, "operator_name": "VIT", "launch_date": "2024-01-15",
            "orbit_type": "POLAR", "sat_status": "ACTIVE", "revisit_days": "5"}
    try:
        assert "created" in client.post("/satellites/new", data=form, follow_redirects=True).get_data(as_text=True)
        sid = db.scalar("SELECT satellite_id FROM satellite WHERE satellite_name = :n", {"n": name})
        assert "already exists" in client.post("/satellites/new", data=form).get_data(as_text=True)
        ok(client, f"/satellites/{sid}/edit")
        form2 = dict(form, satellite_name=name + "-2", sat_status="DECOMMISSIONED")
        assert "updated" in client.post(f"/satellites/{sid}/edit", data=form2, follow_redirects=True).get_data(as_text=True)
        assert "deleted" in client.post(f"/satellites/{sid}/delete", follow_redirects=True).get_data(as_text=True)
        assert "not found" in client.post(f"/satellites/{sid}/delete", follow_redirects=True).get_data(as_text=True)
    finally:
        db.execute("DELETE FROM satellite WHERE satellite_name LIKE 'UI-Test-Sat%'")


def test_delete_satellite_with_images_is_blocked_in_the_ui(client):
    sid = db.scalar("SELECT satellite_id FROM satellite WHERE satellite_name = 'Sentinel-2A'")
    html = client.post(f"/satellites/{sid}/delete", follow_redirects=True).get_data(as_text=True)
    assert "Cannot delete" in html


def test_add_detection_through_the_ui(client):
    img = db.query_one("""SELECT i.image_id, l.latitude, l.longitude FROM image_frame i
                          JOIN location l ON l.location_id = i.location_id
                          WHERE l.location_name = 'Dubai Marina' ORDER BY i.image_id FETCH FIRST 1 ROWS ONLY""")
    data = {"image_id": str(img["image_id"]), "type_name": "Building", "model_id": "2", "confidence": "0.9",
            "lat": str(img["latitude"]), "lon": str(img["longitude"]), "area": "777"}
    bad = dict(data, confidence="1.5", area="-1")
    html = client.post("/detections/new", data=bad).get_data(as_text=True)
    assert "Confidence must be at most 1" in html
    r = client.post("/detections/new", data=data, follow_redirects=True)
    html = r.get_data(as_text=True)
    m = re.search(r"Detection (\d+) saved", html)
    assert m, html[:500]
    db.execute("DELETE FROM detected_object WHERE object_id = :i", {"i": int(m.group(1))})
