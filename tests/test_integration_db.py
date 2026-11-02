"""
Integration tests against the real Oracle database.
Skipped automatically unless EO_DB_USER / EO_DB_PASSWORD are set (e.g. in .env).
Prerequisite: database/schema.sql, sample_data.sql, plsql.sql and run_procedures.sql have been run.
Each test cleans up after itself.
"""
from datetime import date

import pytest

import config
import db
import repo
import services
from services import ValidationError

pytestmark = pytest.mark.skipif(not (config.DB_USER and config.DB_PASSWORD),
                                reason="set EO_DB_USER / EO_DB_PASSWORD to run database tests")


@pytest.fixture(scope="module", autouse=True)
def pool():
    db.init_pool(config.DB_USER, config.DB_PASSWORD, config.DB_DSN)
    yield


def test_sample_data_loaded():
    s = repo.dashboard_stats()
    assert s["satellites"] >= 5 and s["images"] >= 60 and s["objects"] >= 247
    assert s["lineage_links"] >= 36


def test_flagship_query_returns_ten_q3_events():
    rows = repo.deforestation(date(2026, 7, 1), date(2026, 9, 30), 300, 10)
    assert len(rows) == 10
    assert {r["location_name"] for r in rows} == {"Manaus Forest Edge", "Kisangani Rainforest",
                                                  "Tonle Sap Fringe", "Bhadrachalam Forest"}


def test_satellite_crud_roundtrip():
    c = services.validate_satellite({"satellite_name": "PyTest-Sat", "operator_name": "VIT",
                                     "launch_date": "2024-01-15", "orbit_type": "POLAR", "sat_status": "ACTIVE"})
    new_id = repo.insert_satellite(c)
    try:
        assert repo.get_satellite(new_id)["satellite_name"] == "PyTest-Sat"
        c["sat_status"] = "DECOMMISSIONED"
        repo.update_satellite(new_id, c)
        assert repo.get_satellite(new_id)["sat_status"] == "DECOMMISSIONED"
    finally:
        repo.delete_satellite(new_id)
    assert repo.get_satellite(new_id) is None


def test_duplicate_satellite_name_rejected():
    c = services.validate_satellite({"satellite_name": "Sentinel-2A", "operator_name": "ESA",
                                     "launch_date": "2015-06-23", "orbit_type": "SUN-SYNCHRONOUS"})
    with pytest.raises(db.DBError) as ei:
        repo.insert_satellite(c)
    assert ei.value.code == 1 and "already exists" in ei.value.friendly


def test_delete_satellite_with_images_blocked():
    sat_id = db.scalar("SELECT satellite_id FROM satellite WHERE satellite_name = 'Sentinel-2A'")
    with pytest.raises(db.DBError) as ei:
        repo.delete_satellite(sat_id)
    assert ei.value.code == 2292


def test_delete_missing_satellite_reports_not_found():
    with pytest.raises(ValidationError):
        repo.delete_satellite(987654)


def test_check_constraint_blocks_bad_cloud_cover():
    with pytest.raises(db.DBError) as ei:
        db.execute("UPDATE image_frame SET cloud_cover = 140 WHERE image_id = 1")
    assert ei.value.code == 2290 and "between 0 and 100" in ei.value.friendly


def test_add_detection_and_audit_trigger():
    before = db.scalar("SELECT COUNT(*) FROM audit_log WHERE table_name = 'DETECTED_OBJECT'")
    c = services.validate_detection({"image_id": "57", "type_name": "Building", "model_id": "2",
                                     "confidence": "0.9", "lat": "25.08", "lon": "55.14", "area": "900"})
    new_id = repo.add_detection(c)
    try:
        after = db.scalar("SELECT COUNT(*) FROM audit_log WHERE table_name = 'DETECTED_OBJECT'")
        assert after == before + 1
    finally:
        db.execute("DELETE FROM detected_object WHERE object_id = :id", {"id": new_id})


def test_add_detection_unknown_type_raises_package_error():
    c = services.validate_detection({"image_id": "57", "type_name": "Spaceship", "model_id": "2",
                                     "confidence": "0.9", "lat": "25.08", "lon": "55.14", "area": "900"})
    with pytest.raises(db.DBError) as ei:
        repo.add_detection(c)
    assert ei.value.code == 20020 and "Spaceship" in ei.value.friendly


def test_duplicate_tree_patch_rejected():
    c = services.validate_detection({"image_id": "1", "type_name": "Tree Coverage", "model_id": "1",
                                     "confidence": "0.9", "lat": "-2.6", "lon": "-60.3", "area": "1000"})
    with pytest.raises(ValidationError):
        repo.add_detection(c)


def test_graphrag_question_end_to_end():
    c = services.validate_ask({"question": "PyTest: deforestation near rivers", "concept": "deforestation",
                               "alpha": "0.5", "beta": "0.3", "gamma": "0.2", "top_k": "5", "user_id": "1"})
    qvec = repo.concept_vector(c["concept"])
    weights = (c["alpha"], c["beta"], c["gamma"])
    ev = services.rank_evidence(qvec, repo.retrieval_candidates(), repo.basins(), weights,
                                c["date_from"], c["date_to"], c["top_k"])
    qid, lines = repo.save_question(c["user_id"], c["question"], qvec, weights, ev)
    try:
        q = repo.get_question(qid)
        assert 0 <= q["aggregated_confidence"] <= 1
        assert len(repo.question_evidence(qid)) == 5
        assert any("finalize_query" in l for l in lines)
        assert q["aggregated_confidence"] == pytest.approx(services.answer_confidence(ev), abs=1e-3)
    finally:
        db.execute("DELETE FROM query_log WHERE query_id = :id", {"id": qid})   # evidence cascades


def test_alert_resolve_trigger_sets_resolved_at():
    a = db.query_one("SELECT alert_id, alert_status, assigned_user_id FROM alert ORDER BY alert_id FETCH FIRST 1 ROWS ONLY")
    try:
        repo.update_alert(a["alert_id"], "RESOLVED", 1)
        assert db.scalar("SELECT resolved_at FROM alert WHERE alert_id = :id", {"id": a["alert_id"]}) is not None
        repo.update_alert(a["alert_id"], "OPEN", None)
        assert db.scalar("SELECT resolved_at FROM alert WHERE alert_id = :id", {"id": a["alert_id"]}) is None
    finally:
        repo.update_alert(a["alert_id"], a["alert_status"], a["assigned_user_id"])
