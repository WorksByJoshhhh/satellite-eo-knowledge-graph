"""
UI tests with the repository layer replaced by fakes, so every page and form path can be
checked without Oracle. Real database behaviour is covered by test_integration_db.py.
"""
from datetime import date, datetime

import pytest

import db
import repo
from app import create_app

T = datetime(2026, 9, 29, 5, 30)


@pytest.fixture
def calls():
    return {}


@pytest.fixture
def client(monkeypatch, calls):
    fake = {
        "dashboard_stats": lambda: {"satellites": 5, "images": 60, "objects": 247, "locations": 12, "lineage_links": 36,
                                    "deforestation_events": 10, "open_alerts": 3, "questions": 4,
                                    "avg_confidence": 0.62, "latest_capture": T},
        "forest_loss_by_location": lambda limit=None: [
            {"location_id": 1, "location_name": "Manaus Forest Edge", "country": "Brazil", "region_type": "FOREST",
             "basin_name": "Amazon Basin", "latitude": -2.6, "longitude": -60.3, "images": 5, "loss_pct": 38.72},
            {"location_id": 10, "location_name": "Chennai Coast", "country": "India", "region_type": "COASTAL",
             "basin_name": None, "latitude": 13.08, "longitude": 80.29, "images": 5, "loss_pct": None}],
        "alerts": lambda: [{"alert_id": 10000, "severity": "HIGH", "alert_status": "OPEN", "raised_at": T,
                            "resolved_at": None, "assigned_user_id": None, "assignee": None, "location_id": 3,
                            "location_name": "Kisangani Rainforest", "basin_name": "Congo Basin",
                            "change_pct": 17.7, "curr_time": T}],
        "recent_questions": lambda limit=10: [{"query_id": 1, "query_text": "Find deforestation", "submitted_at": T,
                                               "aggregated_confidence": 0.6121, "full_name": "Aarav Menon", "evidence": 5}],
        "satellites_lookup": lambda: [{"satellite_id": 1, "satellite_name": "Sentinel-2A"}],
        "locations_lookup": lambda: [{"location_id": 1, "location_name": "Manaus Forest Edge"}],
        "search_images": lambda *a, **k: [{"image_id": 1, "capture_time": T, "cloud_cover": 31.4,
                                           "satellite_name": "Sentinel-2B", "sensor_name": "MSI",
                                           "spatial_resolution_m": 10, "location_name": "Manaus Forest Edge",
                                           "country": "Brazil", "basin_name": "Amazon Basin", "objects": 3}],
        "get_image": lambda i: None if i == 999 else {
            "image_id": i, "capture_time": T, "cloud_cover": 31.4, "satellite_name": "Sentinel-2B", "sensor_name": "MSI",
            "spatial_resolution_m": 10, "location_name": "Manaus Forest Edge", "country": "Brazil",
            "basin_name": "Amazon Basin", "sun_elevation": 50.1, "file_path": "/archive/x.tif", "location_id": 1,
            "latitude": -2.6, "longitude": -60.3},
        "image_objects": lambda i: [{"object_id": 1, "type_name": "Tree Coverage", "category": "VEGETATION",
                                     "model_name": "ForestNet", "model_version": "1.2", "confidence_score": 0.837,
                                     "area_sq_m": 5949857.19, "attributes_json": {"canopy_density": 0.8},
                                     "center_lat": -2.6, "center_lon": -60.3}],
        "image_indices": lambda i: [{"index_name": "NDVI", "mean_value": 0.79, "min_value": 0.64, "max_value": 0.94}],
        "get_location": lambda i: {"location_id": i, "location_name": "Manaus Forest Edge", "country": "Brazil",
                                   "region_type": "FOREST", "latitude": -2.6, "longitude": -60.3,
                                   "basin_name": "Amazon Basin", "km_to_river": 65},
        "location_tree_timeline": lambda i: [{"image_id": 1, "capture_time": T, "object_id": 1, "area_sq_m": 5.9e6,
                                              "confidence_score": 0.8, "cloud_cover": 30, "ndvi": 0.79}],
        "location_changes": lambda i: [{"curr_time": T, "change_type": "DEFORESTATION", "change_pct": 17.77,
                                        "rate_pct_per_month": 16.15, "alert_id": None}],
        "location_images": lambda i: [{"image_id": 1, "capture_time": T, "satellite_name": "Sentinel-2B",
                                       "cloud_cover": 31.4, "objects": 3}],
        "deforestation": lambda *a: [{"change_id": 1, "location_id": 1, "location_name": "Manaus Forest Edge",
                                      "basin_name": "Amazon Basin", "curr_time": T, "change_pct": 17.77,
                                      "rate_pct_per_month": 16.15, "time_delta_days": 33, "latitude": -2.6,
                                      "longitude": -60.3, "km_to_river": 65, "evidence_trust": 0.466, "alert_id": None}],
        "users": lambda: [{"user_id": 1, "full_name": "Aarav Menon", "user_role": "ANALYST"}],
        "object_types": lambda: [{"object_type_id": 2, "type_name": "Building", "category": "URBAN"}],
        "models": lambda: [{"model_id": 2, "model_name": "YOLO-EO", "model_version": "8.0"}],
        "images_for_select": lambda: [{"image_id": 1, "capture_time": T, "location_name": "Manaus Forest Edge",
                                       "latitude": -2.6, "longitude": -60.3}],
        "list_satellites": lambda: [{"satellite_id": 1, "satellite_name": "Sentinel-2A", "operator_name": "ESA",
                                     "launch_date": datetime(2015, 6, 23), "orbit_type": "SUN-SYNCHRONOUS",
                                     "sat_status": "ACTIVE", "revisit_days": 10, "sensors": 1, "images": 15}],
        "get_satellite": lambda i: None if i == 999 else {
            "satellite_id": i, "satellite_name": "Sentinel-2A", "operator_name": "ESA",
            "launch_date": datetime(2015, 6, 23), "orbit_type": "SUN-SYNCHRONOUS", "sat_status": "ACTIVE",
            "revisit_days": 10},
        "audit_log": lambda t=None: [{"audit_id": 1, "table_name": "DETECTED_OBJECT", "operation": "INSERT",
                                      "record_id": 10000, "changed_by": "JOSHWA", "changed_at": T,
                                      "old_value": None, "new_value": "conf=.91, area=850"}],
        "get_question": lambda i: None if i == 999 else {
            "query_id": i, "query_text": "Find deforestation", "submitted_at": T, "response_text": "Answer based on 5",
            "aggregated_confidence": 0.6121, "retrieval_weights_json": {"alpha": 0.5, "beta": 0.3, "gamma": 0.2},
            "full_name": "Aarav Menon"},
        "question_evidence": lambda i: [{"rank_position": 1, "semantic_score": 0.98, "spatial_score": 0.83,
                                         "temporal_score": 1.0, "retrieval_score": 0.9484, "object_id": 41,
                                         "type_name": "Tree Coverage", "confidence_score": 0.775, "area_sq_m": 2.6e6,
                                         "image_id": 15, "capture_time": T, "cloud_cover": 8.1, "location_id": 3,
                                         "location_name": "Kisangani Rainforest", "latitude": 0.8, "longitude": 25.0,
                                         "basin_name": "Congo Basin"}],
        "concept_vector": lambda c: [1.0, 0.0],
        "retrieval_candidates": lambda: [
            {"object_id": n, "embedding": [1.0, n / 10], "latitude": 0.8, "longitude": 25.0, "capture_time": T,
             "confidence_score": 0.8, "cloud_cover": 10} for n in range(1, 9)],
        "basins": lambda: [{"basin_id": 1, "basin_name": "Congo", "river_ref_lat": 0.5, "river_ref_lon": 25.2}],
    }

    def save_question(user_id, question, qvec, weights, evidence):
        calls["save_question"] = (user_id, question, weights, evidence)
        return 77, ["finalize_query(77): ..."]

    def recorder(name, result=None, exc=None):
        def f(*a, **k):
            calls[name] = a
            if exc:
                raise exc
            return result
        return f

    fake.update(save_question=save_question,
                run_analysis=recorder("run_analysis", ["compute_changes: 36 lineage links created/updated."]),
                update_alert=recorder("update_alert"),
                insert_satellite=recorder("insert_satellite", 10005),
                update_satellite=recorder("update_satellite"),
                delete_satellite=recorder("delete_satellite"),
                add_detection=recorder("add_detection", 10001))
    for name, fn in fake.items():
        monkeypatch.setattr(repo, name, fn)
    monkeypatch.setattr(repo, "_fake", fake, raising=False)
    app = create_app(init_db=False)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.mark.parametrize("url,text", [
    ("/", "Forest loss by location"), ("/images", "Image catalogue"), ("/images/1", "Detected objects"),
    ("/locations", "Locations"), ("/locations/1", "EVOLVED_FROM lineage"), ("/finder", "1 event(s) found"),
    ("/alerts", "Run change analysis"), ("/ask", "Ask the knowledge graph"), ("/ask/1", "Answer confidence"),
    ("/detections/new", "Add a detected object"), ("/satellites", "Sentinel-2A"),
    ("/satellites/new", "New satellite"), ("/satellites/1/edit", "Edit Sentinel-2A"), ("/audit", "Audit log"),
])
def test_pages_render(client, url, text):
    r = client.get(url)
    assert r.status_code == 200, url
    assert text in r.get_data(as_text=True)


def test_missing_records_give_404(client):
    assert client.get("/images/999").status_code == 404
    assert client.get("/ask/999").status_code == 404
    assert client.get("/satellites/999/edit").status_code == 404


def test_create_satellite_valid(client, calls):
    r = client.post("/satellites/new", data={"satellite_name": "Demo-Sat", "operator_name": "VIT",
                                             "launch_date": "2024-01-15", "orbit_type": "POLAR",
                                             "sat_status": "ACTIVE", "revisit_days": "5"}, follow_redirects=True)
    assert "created (id 10005)" in r.get_data(as_text=True)
    assert "insert_satellite" in calls


def test_create_satellite_invalid_shows_errors(client, calls):
    r = client.post("/satellites/new", data={"satellite_name": "", "operator_name": "VIT",
                                             "launch_date": "2099-01-01", "orbit_type": "LUNAR"})
    html = r.get_data(as_text=True)
    assert "Satellite name is required" in html and "cannot be in the future" in html
    assert "insert_satellite" not in calls


def test_duplicate_satellite_shows_friendly_error(client, monkeypatch):
    err = db.DBError(1, "ORA-00001: unique constraint (JOSHWA.UQ_SATELLITE_NAME) violated",
                     "A satellite with this name already exists.")
    monkeypatch.setattr(repo, "insert_satellite", lambda c: (_ for _ in ()).throw(err))
    r = client.post("/satellites/new", data={"satellite_name": "Sentinel-2A", "operator_name": "ESA",
                                             "launch_date": "2015-06-23", "orbit_type": "SUN-SYNCHRONOUS",
                                             "sat_status": "ACTIVE"})
    assert "already exists" in r.get_data(as_text=True)


def test_delete_satellite_with_children_is_blocked(client, monkeypatch):
    err = db.DBError(2292, "ORA-02292: child record found", "Cannot delete: other records still depend on this one.")
    monkeypatch.setattr(repo, "delete_satellite", lambda i: (_ for _ in ()).throw(err))
    r = client.post("/satellites/1/delete", follow_redirects=True)
    assert "Cannot delete" in r.get_data(as_text=True)


def test_add_detection_invalid(client, calls):
    r = client.post("/detections/new", data={"image_id": "1", "type_name": "Building", "model_id": "2",
                                             "confidence": "1.5", "lat": "-2.6", "lon": "-60.3", "area": "-1"})
    html = r.get_data(as_text=True)
    assert "Confidence must be at most 1" in html and "Area (m²) must be greater than 0" in html
    assert "add_detection" not in calls


def test_add_detection_valid(client, calls):
    r = client.post("/detections/new", data={"image_id": "1", "type_name": "Building", "model_id": "2",
                                             "confidence": "0.9", "lat": "-2.6", "lon": "-60.3", "area": "850"},
                    follow_redirects=True)
    assert "Detection 10001 saved" in r.get_data(as_text=True)


def test_ask_flow_saves_question_and_evidence(client, calls):
    r = client.post("/ask", data={"question": "Find deforestation near rivers", "concept": "deforestation",
                                  "alpha": "0.5", "beta": "0.3", "gamma": "0.2", "top_k": "5",
                                  "date_from": "2026-07-01", "date_to": "2026-09-30", "user_id": "1"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/ask/77")
    user_id, question, weights, evidence = calls["save_question"]
    assert user_id == 1 and len(evidence) == 5 and weights == (0.5, 0.3, 0.2)
    assert [e["rank_position"] for e in evidence] == [1, 2, 3, 4, 5]


def test_ask_invalid(client, calls):
    r = client.post("/ask", data={"question": "hi", "concept": "deforestation", "alpha": "0", "beta": "0",
                                  "gamma": "0", "top_k": "5"})
    html = r.get_data(as_text=True)
    assert "at least 5 characters" in html and "At least one weight" in html
    assert "save_question" not in calls


def test_run_analysis_and_invalid_thresholds(client, calls):
    r = client.post("/alerts/run", data={"min_loss": "10", "min_conf": "0.7", "max_cloud": "40"}, follow_redirects=True)
    assert "compute_changes: 36 lineage links" in r.get_data(as_text=True)
    r = client.post("/alerts/run", data={"min_loss": "150", "min_conf": "2", "max_cloud": "40"}, follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "Minimum loss % must be at most 100" in html and "Minimum confidence must be at most 1" in html


def test_update_alert(client, calls):
    r = client.post("/alerts/10000", data={"alert_status": "RESOLVED", "assigned_user_id": "1"}, follow_redirects=True)
    assert "updated to RESOLVED" in r.get_data(as_text=True)
    assert calls["update_alert"] == (10000, "RESOLVED", 1)


def test_finder_bad_dates_fall_back(client):
    r = client.get("/finder?date_from=2026-09-30&date_to=2026-07-01")
    assert "From date must be on or before To date" in r.get_data(as_text=True)


def test_database_down_shows_clear_message(client, monkeypatch):
    err = db.DBError(12541, "ORA-12541", "Cannot connect to the database.")
    monkeypatch.setattr(repo, "dashboard_stats", lambda: (_ for _ in ()).throw(err))
    r = client.get("/")
    assert r.status_code == 500 and "Cannot connect to the database" in r.get_data(as_text=True)


def test_question_page_accepts_json_as_text(client, monkeypatch):
    """The fixture returns dicts (as python-oracledb does); text JSON must work too."""
    q = repo.get_question(1)
    q["retrieval_weights_json"] = '{"alpha":0.5,"beta":0.3,"gamma":0.2}'
    monkeypatch.setattr(repo, "get_question", lambda i: q)
    r = client.get("/ask/1")
    assert r.status_code == 200 and "α=0.5" in r.get_data(as_text=True)


def test_image_attributes_accept_text(client, monkeypatch):
    objs = repo.image_objects(1)
    objs[0]["attributes_json"] = '{"canopy_density":0.8}'
    monkeypatch.setattr(repo, "image_objects", lambda i: objs)
    assert "canopy_density=0.8" in client.get("/images/1").get_data(as_text=True)


def test_negative_loss_is_shown_as_gain(client, monkeypatch):
    rows = repo.forest_loss_by_location()
    rows[0]["loss_pct"] = -0.2
    monkeypatch.setattr(repo, "forest_loss_by_location", lambda limit=None: rows)
    html = client.get("/locations").get_data(as_text=True)
    assert "0.2% gain" in html and "-0.2%" not in html
    assert "0.2% gain" in client.get("/").get_data(as_text=True)
