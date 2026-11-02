"""Unit tests for validation, scoring and error translation (no database needed)."""
from datetime import date, datetime

import pytest

import services as s
from services import ValidationError


# ---------------- satellite validation ----------------
def good_sat(**over):
    f = {"satellite_name": "Demo-Sat", "operator_name": "VIT", "launch_date": "2024-01-15",
         "orbit_type": "POLAR", "sat_status": "ACTIVE", "revisit_days": "5"}
    f.update(over)
    return f


def test_valid_satellite_is_cleaned():
    c = s.validate_satellite(good_sat())
    assert c["launch_date"] == date(2024, 1, 15) and c["revisit_days"] == 5


@pytest.mark.parametrize("field,value,msg", [
    ("satellite_name", "", "Satellite name is required"),
    ("launch_date", "2099-01-01", "cannot be in the future"),
    ("launch_date", "15/01/2024", "YYYY-MM-DD"),
    ("orbit_type", "LUNAR", "Orbit type must be one of"),
    ("revisit_days", "0", "Revisit days must be at least 1"),
    ("revisit_days", "abc", "must be an integer"),
])
def test_invalid_satellite(field, value, msg):
    with pytest.raises(ValidationError) as ei:
        s.validate_satellite(good_sat(**{field: value}), today=date(2026, 10, 27))
    assert any(msg in e for e in ei.value.errors)


# ---------------- detection validation ----------------
def good_det(**over):
    f = {"image_id": "1", "type_name": "Building", "model_id": "2", "confidence": "0.9",
         "lat": "-2.6", "lon": "-60.3", "area": "850"}
    f.update(over)
    return f


def test_valid_detection():
    c = s.validate_detection(good_det())
    assert c["confidence"] == 0.9 and c["image_id"] == 1


@pytest.mark.parametrize("field,value", [("confidence", "1.5"), ("confidence", "-0.1"), ("area", "0"),
                                         ("area", "-5"), ("lat", "95"), ("lon", "abc"), ("image_id", ""),
                                         ("confidence", "nan")])
def test_invalid_detection(field, value):
    with pytest.raises(ValidationError):
        s.validate_detection(good_det(**{field: value}))


# ---------------- ask validation ----------------
def test_ask_weights_are_normalised():
    c = s.validate_ask({"question": "Where is deforestation?", "concept": "deforestation",
                        "alpha": "1", "beta": "1", "gamma": "0", "top_k": "5"})
    assert (c["alpha"], c["beta"], c["gamma"]) == (0.5, 0.5, 0.0)
    assert c["date_from"] == s.Q3_FROM


def test_ask_rejects_all_zero_weights_and_bad_dates():
    with pytest.raises(ValidationError) as ei:
        s.validate_ask({"question": "Where is deforestation?", "concept": "deforestation", "alpha": "0",
                        "beta": "0", "gamma": "0", "top_k": "5", "date_from": "2026-09-30", "date_to": "2026-07-01"})
    joined = " ".join(ei.value.errors)
    assert "At least one weight" in joined and "From date must be on or before" in joined


def test_ask_rejects_unknown_concept_and_top_k():
    with pytest.raises(ValidationError):
        s.validate_ask({"question": "hello there", "concept": "aliens", "alpha": "1", "beta": "0",
                        "gamma": "0", "top_k": "50"})


# ---------------- scoring ----------------
def test_cosine_and_centroid():
    assert s.cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert s.cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    c = s.centroid([[1, 0], [0, 1]])
    assert c == pytest.approx([0.7071, 0.7071], abs=1e-4)


def test_haversine_known_distance():
    # Chennai -> Delhi is about 1,760 km
    assert s.haversine_km(13.08, 80.27, 28.61, 77.21) == pytest.approx(1757, rel=0.02)


def test_temporal_score():
    assert s.temporal_score(datetime(2026, 8, 1), s.Q3_FROM, s.Q3_TO) == 1.0
    assert s.temporal_score(datetime(2026, 5, 1), s.Q3_FROM, s.Q3_TO) == 0.3


def test_rank_evidence_prefers_similar_near_recent():
    basins = [{"river_ref_lat": 0.0, "river_ref_lon": 0.0}]
    cands = [
        {"object_id": 1, "embedding": [1, 0], "latitude": 0.1, "longitude": 0.1, "capture_time": datetime(2026, 8, 1),
         "confidence_score": 0.9, "cloud_cover": 10},
        {"object_id": 2, "embedding": [0, 1], "latitude": 0.1, "longitude": 0.1, "capture_time": datetime(2026, 8, 1),
         "confidence_score": 0.9, "cloud_cover": 10},
        {"object_id": 3, "embedding": [1, 0], "latitude": 30, "longitude": 30, "capture_time": datetime(2026, 4, 1),
         "confidence_score": 0.9, "cloud_cover": 10},
    ]
    top = s.rank_evidence([1, 0], cands, basins, (0.5, 0.3, 0.2), s.Q3_FROM, s.Q3_TO, 2)
    assert [r["object_id"] for r in top] == [1, 3] or [r["object_id"] for r in top] == [1, 2]
    assert top[0]["object_id"] == 1 and top[0]["rank_position"] == 1
    assert all(0 <= r["retrieval_score"] <= 1 for r in top)


def test_answer_confidence_matches_plsql_formula():
    ev = [{"retrieval_score": 1.0, "confidence_score": 0.8, "cloud_cover": 50},
          {"retrieval_score": 0.0, "confidence_score": 0.1, "cloud_cover": 0}]
    assert s.answer_confidence(ev) == pytest.approx(0.4)


# ---------------- Oracle error translation ----------------
@pytest.mark.parametrize("code,msg,expected", [
    (1, "ORA-00001: unique constraint (JOSHWA.UQ_SATELLITE_NAME) violated", "already exists"),
    (2290, "ORA-02290: check constraint (JOSHWA.CK_IMG_CLOUD) violated", "between 0 and 100"),
    (2291, "ORA-02291: integrity constraint (JOSHWA.FK_OBJ_IMAGE) violated - parent key not found", "does not exist"),
    (2292, "ORA-02292: integrity constraint (JOSHWA.FK_IMG_SENSOR) violated - child record found", "Cannot delete"),
    (1400, 'ORA-01400: cannot insert NULL into ("JOSHWA"."SATELLITE"."SATELLITE_NAME")', "satellite_name"),
    (20020, "ORA-20020: Unknown object type: Spaceship\nORA-06512: at line 1", "Unknown object type: Spaceship"),
    (12541, "ORA-12541: Cannot connect", "Cannot connect"),
])
def test_friendly_db_error(code, msg, expected):
    assert expected in s.friendly_db_error(code, msg)
