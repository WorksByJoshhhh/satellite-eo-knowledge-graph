"""
All SQL used by the application lives here (repository layer).
Every statement uses bind variables (:name) - never string concatenation - to prevent SQL injection.
"""
import json

import db
from services import ValidationError, centroid, normalise, parse_vector

# ------------------------------------------------------------------ dashboard
def dashboard_stats():
    return db.query_one("""
        SELECT (SELECT COUNT(*) FROM satellite)        AS satellites,
               (SELECT COUNT(*) FROM image_frame)      AS images,
               (SELECT COUNT(*) FROM detected_object)  AS objects,
               (SELECT COUNT(*) FROM location)         AS locations,
               (SELECT COUNT(*) FROM object_change)    AS lineage_links,
               (SELECT COUNT(*) FROM object_change WHERE change_type = 'DEFORESTATION') AS deforestation_events,
               (SELECT COUNT(*) FROM alert WHERE alert_status <> 'RESOLVED') AS open_alerts,
               (SELECT COUNT(*) FROM query_log)        AS questions,
               (SELECT ROUND(AVG(aggregated_confidence), 3) FROM query_log) AS avg_confidence,
               (SELECT MAX(capture_time) FROM image_frame) AS latest_capture
        FROM dual""")


def forest_loss_by_location(limit=None):
    rows = db.query("""
        SELECT l.location_id, l.location_name, l.country, l.region_type, b.basin_name,
               l.latitude, l.longitude,
               (SELECT COUNT(*) FROM image_frame i WHERE i.location_id = l.location_id) AS images,
               fn_forest_loss_pct(l.location_id,
                                  (SELECT MIN(TRUNC(capture_time)) FROM image_frame),
                                  (SELECT MAX(TRUNC(capture_time)) FROM image_frame)) AS loss_pct
        FROM location l LEFT JOIN river_basin b ON b.basin_id = l.basin_id
        ORDER BY loss_pct DESC NULLS LAST, l.location_name""")
    return rows[:limit] if limit else rows


# ------------------------------------------------------------------ lookups
def satellites_lookup():
    return db.query("SELECT satellite_id, satellite_name FROM satellite ORDER BY satellite_name")


def locations_lookup():
    return db.query("SELECT location_id, location_name FROM location ORDER BY location_name")


def object_types():
    return db.query("SELECT object_type_id, type_name, category FROM object_type ORDER BY type_name")


def models():
    return db.query("SELECT model_id, model_name, model_version FROM detection_model ORDER BY model_name")


def users():
    return db.query("SELECT user_id, full_name, user_role FROM app_user ORDER BY full_name")


def images_for_select():
    return db.query("""
        SELECT i.image_id, i.capture_time, l.location_name, l.latitude, l.longitude
        FROM image_frame i JOIN location l ON l.location_id = i.location_id
        ORDER BY l.location_name, i.capture_time""")


# ------------------------------------------------------------------ images
def search_images(satellite=None, location=None, max_cloud=100, date_from=None, date_to=None):
    return db.query("""
        SELECT v.image_id, v.capture_time, v.cloud_cover, v.satellite_name, v.sensor_name,
               v.spatial_resolution_m, v.location_name, v.country, v.basin_name,
               (SELECT COUNT(*) FROM detected_object o WHERE o.image_id = v.image_id) AS objects
        FROM v_image_summary v
        WHERE (:sat IS NULL OR v.satellite_name = :sat)
          AND (:loc IS NULL OR v.location_name = :loc)
          AND v.cloud_cover <= :max_cloud
          AND v.capture_time >= :d_from
          AND v.capture_time <  :d_to + 1
        ORDER BY v.capture_time DESC, v.image_id
        FETCH FIRST 200 ROWS ONLY""",
        {"sat": satellite, "loc": location, "max_cloud": max_cloud, "d_from": date_from, "d_to": date_to})


def get_image(image_id):
    return db.query_one("""
        SELECT v.*, i.sun_elevation, i.file_path, l.location_id, l.latitude, l.longitude
        FROM v_image_summary v
        JOIN image_frame i ON i.image_id = v.image_id
        JOIN location l ON l.location_id = i.location_id
        WHERE v.image_id = :id""", {"id": image_id})


def image_objects(image_id):
    return db.query("""
        SELECT o.object_id, t.type_name, t.category, m.model_name, m.model_version,
               o.confidence_score, o.area_sq_m, o.attributes_json,
               ROUND((o.bbox_min_lat + o.bbox_max_lat) / 2, 5) AS center_lat,
               ROUND((o.bbox_min_lon + o.bbox_max_lon) / 2, 5) AS center_lon
        FROM detected_object o
        JOIN object_type t ON t.object_type_id = o.object_type_id
        JOIN detection_model m ON m.model_id = o.model_id
        WHERE o.image_id = :id
        ORDER BY t.type_name, o.object_id""", {"id": image_id})


def image_indices(image_id):
    return db.query("""SELECT index_name, mean_value, min_value, max_value
                       FROM spectral_index WHERE image_id = :id ORDER BY index_name""", {"id": image_id})


# ------------------------------------------------------------------ locations
def get_location(location_id):
    return db.query_one("""
        SELECT l.*, b.basin_name, b.basin_code, b.river_ref_lat, b.river_ref_lon,
               CASE WHEN b.basin_id IS NULL THEN NULL
                    ELSE ROUND(fn_haversine_km(l.latitude, l.longitude, b.river_ref_lat, b.river_ref_lon)) END AS km_to_river
        FROM location l LEFT JOIN river_basin b ON b.basin_id = l.basin_id
        WHERE l.location_id = :id""", {"id": location_id})


def location_tree_timeline(location_id):
    return db.query("""
        SELECT i.image_id, i.capture_time, o.object_id, o.area_sq_m, o.confidence_score,
               i.cloud_cover, si.mean_value AS ndvi
        FROM detected_object o
        JOIN image_frame i ON i.image_id = o.image_id
        JOIN object_type t ON t.object_type_id = o.object_type_id
        LEFT JOIN spectral_index si ON si.image_id = i.image_id AND si.index_name = 'NDVI'
        WHERE t.type_name = 'Tree Coverage' AND i.location_id = :id
        ORDER BY i.capture_time""", {"id": location_id})


def location_changes(location_id):
    return db.query("""
        SELECT fc.*, (SELECT a.alert_id FROM alert a WHERE a.change_id = fc.change_id) AS alert_id
        FROM v_forest_change fc WHERE fc.location_id = :id ORDER BY fc.curr_time""", {"id": location_id})


def location_images(location_id):
    return db.query("""
        SELECT v.image_id, v.capture_time, v.satellite_name, v.cloud_cover,
               (SELECT COUNT(*) FROM detected_object o WHERE o.image_id = v.image_id) AS objects
        FROM v_image_summary v JOIN image_frame i ON i.image_id = v.image_id
        WHERE i.location_id = :id ORDER BY v.capture_time""", {"id": location_id})


# ------------------------------------------------------------------ detections
def add_detection(c):
    """Validated insert through the PL/SQL package (pkg_eo.add_detection)."""
    img = db.query_one("SELECT image_id FROM image_frame WHERE image_id = :id", {"id": c["image_id"]})
    if not img:
        raise ValidationError([f"Image {c['image_id']} does not exist."])
    if c["type_name"] == "Tree Coverage":
        dup = db.scalar("""
            SELECT COUNT(*) FROM detected_object o JOIN object_type t ON t.object_type_id = o.object_type_id
            WHERE o.image_id = :id AND t.type_name = 'Tree Coverage'""", {"id": c["image_id"]})
        if dup:
            raise ValidationError(["Duplicate: this image already has a Tree Coverage patch "
                                   "(the lineage model allows one tree patch per image)."])
    with db.connection() as conn:
        cur = conn.cursor()
        new_id = cur.var(int)
        cur.callproc("pkg_eo.add_detection",
                     [c["image_id"], c["type_name"], c["model_id"], c["confidence"],
                      c["lat"], c["lon"], c["area"], new_id])
        return new_id.getvalue()


# ------------------------------------------------------------------ deforestation finder
def deforestation(date_from, date_to, max_km, min_loss):
    return db.query("""
        SELECT fc.change_id, fc.location_id, fc.location_name, fc.basin_name, fc.curr_time,
               fc.change_pct, fc.rate_pct_per_month, fc.time_delta_days,
               l.latitude, l.longitude,
               ROUND(fn_haversine_km(l.latitude, l.longitude, b.river_ref_lat, b.river_ref_lon)) AS km_to_river,
               ROUND(LEAST(fc.prev_conf, fc.curr_conf)
                     * (1 - GREATEST(fc.prev_cloud, fc.curr_cloud) / 100), 3) AS evidence_trust,
               (SELECT a.alert_id FROM alert a WHERE a.change_id = fc.change_id) AS alert_id
        FROM v_forest_change fc
        JOIN location l    ON l.location_id = fc.location_id
        JOIN river_basin b ON b.basin_id = l.basin_id
        WHERE fc.change_pct >= :min_loss
          AND fc.curr_time >= :d_from
          AND fc.curr_time <  :d_to + 1
          AND fn_haversine_km(l.latitude, l.longitude, b.river_ref_lat, b.river_ref_lon) <= :max_km
        ORDER BY fc.rate_pct_per_month DESC""",
        {"min_loss": min_loss, "d_from": date_from, "d_to": date_to, "max_km": max_km})


# ------------------------------------------------------------------ alerts
def alerts():
    return db.query("""
        SELECT a.alert_id, a.severity, a.alert_status, a.raised_at, a.resolved_at, a.assigned_user_id,
               u.full_name AS assignee, fc.location_id, fc.location_name, fc.basin_name,
               fc.change_pct, fc.curr_time
        FROM alert a
        JOIN v_forest_change fc ON fc.change_id = a.change_id
        LEFT JOIN app_user u ON u.user_id = a.assigned_user_id
        ORDER BY CASE a.alert_status WHEN 'OPEN' THEN 1 WHEN 'REVIEWING' THEN 2 ELSE 3 END,
                 CASE a.severity WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 ELSE 3 END,
                 fc.change_pct DESC""")


def run_analysis(min_loss, min_conf, max_cloud):
    """Runs the PL/SQL lineage builder and alert raiser; returns their DBMS_OUTPUT lines."""
    with db.connection() as conn:
        cur = conn.cursor()
        cur.callproc("dbms_output.enable", [None])
        cur.callproc("pkg_eo.compute_changes")
        cur.callproc("pkg_eo.raise_alerts", [min_loss, min_conf, max_cloud])
        return db.read_dbms_output(cur)


def update_alert(alert_id, alert_status, assigned_user_id):
    n = db.execute("""UPDATE alert SET alert_status = :s, assigned_user_id = :u WHERE alert_id = :id""",
                   {"s": alert_status, "u": assigned_user_id, "id": alert_id})
    if n == 0:
        raise ValidationError([f"Alert {alert_id} not found."])


# ------------------------------------------------------------------ GraphRAG ask
def _vectors(sql, params=None):
    vecs = [parse_vector(r["v"]) for r in db.query(sql, params)]
    return [v for v in vecs if v and any(abs(x) > 1e-9 for x in v)]


def concept_vector(concept):
    """Query vector = centroid of labelled examples in the database (data-driven prototype)."""
    if concept in ("deforestation", "intact_forest"):
        ctype = "DEFORESTATION" if concept == "deforestation" else "STABLE"
        vecs = _vectors("""SELECT o.object_embedding AS v FROM object_change oc
                           JOIN detected_object o ON o.object_id = oc.curr_object_id
                           WHERE oc.change_type = :t""", {"t": ctype})
        if not vecs:
            raise ValidationError(["No labelled examples yet. Run the change analysis on the Alerts page first."])
    else:
        tname = {"bare_soil": "Bare Soil", "vessel": "Vessel", "building": "Building", "water": "Water Body"}[concept]
        vecs = _vectors("""SELECT o.object_embedding AS v FROM detected_object o
                           JOIN object_type t ON t.object_type_id = o.object_type_id
                           WHERE t.type_name = :t""", {"t": tname})
        if not vecs:
            raise ValidationError([f"No {tname} detections in the database."])
    return centroid(vecs)


def retrieval_candidates():
    rows = db.query("""
        SELECT o.object_id, t.type_name, o.confidence_score, o.area_sq_m, o.object_embedding,
               i.image_id, i.capture_time, i.cloud_cover,
               l.location_id, l.location_name, l.latitude, l.longitude, b.basin_name
        FROM detected_object o
        JOIN object_type t ON t.object_type_id = o.object_type_id
        JOIN image_frame i ON i.image_id = o.image_id
        JOIN location l    ON l.location_id = i.location_id
        LEFT JOIN river_basin b ON b.basin_id = l.basin_id""")
    for r in rows:
        r["embedding"] = parse_vector(r.pop("object_embedding"))
    return rows


def basins():
    return db.query("SELECT basin_id, basin_name, river_ref_lat, river_ref_lon FROM river_basin")


def save_question(user_id, question, qvec, weights, evidence):
    """One transaction: QUERY_LOG + QUERY_EVIDENCE rows, then pkg_eo.finalize_query
    computes and stores the answer-level confidence. Returns (query_id, dbms_output lines)."""
    with db.connection() as conn:
        cur = conn.cursor()
        qid = cur.var(int)
        cur.execute("""
            INSERT INTO query_log (user_id, query_text, query_embedding, retrieval_weights_json)
            VALUES (:u, :t, :e, :w) RETURNING query_id INTO :qid""",
            {"u": user_id, "t": question, "e": json.dumps([round(x, 4) for x in normalise(qvec)]),
             "w": json.dumps({"alpha": weights[0], "beta": weights[1], "gamma": weights[2]}), "qid": qid})
        query_id = qid.getvalue()[0] if isinstance(qid.getvalue(), list) else qid.getvalue()
        cur.executemany("""
            INSERT INTO query_evidence (query_id, object_id, semantic_score, spatial_score,
                                        temporal_score, retrieval_score, rank_position)
            VALUES (:1, :2, :3, :4, :5, :6, :7)""",
            [(query_id, e["object_id"], e["semantic_score"], e["spatial_score"], e["temporal_score"],
              e["retrieval_score"], e["rank_position"]) for e in evidence])
        conn.commit()
        cur.callproc("dbms_output.enable", [None])
        cur.callproc("pkg_eo.finalize_query", [query_id])
        return query_id, db.read_dbms_output(cur)


def get_question(query_id):
    return db.query_one("""
        SELECT q.query_id, q.query_text, q.submitted_at, q.response_text, q.aggregated_confidence,
               q.retrieval_weights_json, u.full_name
        FROM query_log q LEFT JOIN app_user u ON u.user_id = q.user_id
        WHERE q.query_id = :id""", {"id": query_id})


def question_evidence(query_id):
    return db.query("""
        SELECT qe.rank_position, qe.semantic_score, qe.spatial_score, qe.temporal_score, qe.retrieval_score,
               o.object_id, t.type_name, o.confidence_score, o.area_sq_m,
               i.image_id, i.capture_time, i.cloud_cover,
               l.location_id, l.location_name, l.latitude, l.longitude, b.basin_name
        FROM query_evidence qe
        JOIN detected_object o ON o.object_id = qe.object_id
        JOIN object_type t     ON t.object_type_id = o.object_type_id
        JOIN image_frame i     ON i.image_id = o.image_id
        JOIN location l        ON l.location_id = i.location_id
        LEFT JOIN river_basin b ON b.basin_id = l.basin_id
        WHERE qe.query_id = :id
        ORDER BY qe.rank_position""", {"id": query_id})


def recent_questions(limit=10):
    return db.query("""
        SELECT q.query_id, q.query_text, q.submitted_at, q.aggregated_confidence, u.full_name,
               (SELECT COUNT(*) FROM query_evidence qe WHERE qe.query_id = q.query_id) AS evidence
        FROM query_log q LEFT JOIN app_user u ON u.user_id = q.user_id
        ORDER BY q.submitted_at DESC, q.query_id DESC
        FETCH FIRST 10 ROWS ONLY""")[:limit]


# ------------------------------------------------------------------ satellites (CRUD)
def list_satellites():
    return db.query("""
        SELECT s.*, (SELECT COUNT(*) FROM sensor sn WHERE sn.satellite_id = s.satellite_id) AS sensors,
               (SELECT COUNT(*) FROM image_frame i JOIN sensor sn ON sn.sensor_id = i.sensor_id
                 WHERE sn.satellite_id = s.satellite_id) AS images
        FROM satellite s ORDER BY s.satellite_name""")


def get_satellite(satellite_id):
    return db.query_one("SELECT * FROM satellite WHERE satellite_id = :id", {"id": satellite_id})


def insert_satellite(c):
    with db.connection() as conn:
        cur = conn.cursor()
        new_id = cur.var(int)
        cur.execute("""
            INSERT INTO satellite (satellite_name, operator_name, launch_date, orbit_type, sat_status, revisit_days)
            VALUES (:n, :o, :d, :orb, :st, :rv) RETURNING satellite_id INTO :nid""",
            {"n": c["satellite_name"], "o": c["operator_name"], "d": c["launch_date"], "orb": c["orbit_type"],
             "st": c["sat_status"], "rv": c["revisit_days"], "nid": new_id})
        conn.commit()
        v = new_id.getvalue()
        return v[0] if isinstance(v, list) else v


def update_satellite(satellite_id, c):
    n = db.execute("""
        UPDATE satellite SET satellite_name = :n, operator_name = :o, launch_date = :d,
               orbit_type = :orb, sat_status = :st, revisit_days = :rv
        WHERE satellite_id = :id""",
        {"n": c["satellite_name"], "o": c["operator_name"], "d": c["launch_date"], "orb": c["orbit_type"],
         "st": c["sat_status"], "rv": c["revisit_days"], "id": satellite_id})
    if n == 0:
        raise ValidationError([f"Satellite {satellite_id} not found."])


def delete_satellite(satellite_id):
    n = db.execute("DELETE FROM satellite WHERE satellite_id = :id", {"id": satellite_id})
    if n == 0:
        raise ValidationError([f"Satellite {satellite_id} not found (it may already have been deleted)."])


# ------------------------------------------------------------------ audit
def audit_log(table_name=None):
    return db.query("""
        SELECT audit_id, table_name, operation, record_id, changed_by, changed_at, old_value, new_value
        FROM audit_log WHERE (:t IS NULL OR table_name = :t)
        ORDER BY audit_id DESC FETCH FIRST 200 ROWS ONLY""", {"t": table_name})
