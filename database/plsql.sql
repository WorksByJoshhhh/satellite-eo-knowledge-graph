-- =====================================================================
-- plsql.sql  -- PL/SQL: functions, package, procedures, triggers
-- Run in SQL*Plus / SQL Developer as a SCRIPT (F5). Each block ends with "/".
-- =====================================================================
SET SERVEROUTPUT ON;
SET DEFINE OFF;

-- ---------------------------------------------------------------------
-- F1. Great-circle distance in km (used for "near a river basin" queries)
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_haversine_km (
  p_lat1 NUMBER, p_lon1 NUMBER, p_lat2 NUMBER, p_lon2 NUMBER
) RETURN NUMBER DETERMINISTIC IS
  c_pi   CONSTANT NUMBER := ACOS(-1);
  c_r    CONSTANT NUMBER := 6371;
  v_dlat NUMBER := (p_lat2 - p_lat1) * c_pi / 180;
  v_dlon NUMBER := (p_lon2 - p_lon1) * c_pi / 180;
  v_a    NUMBER;
BEGIN
  v_a := POWER(SIN(v_dlat / 2), 2)
       + COS(p_lat1 * c_pi / 180) * COS(p_lat2 * c_pi / 180) * POWER(SIN(v_dlon / 2), 2);
  RETURN c_r * 2 * ATAN2(SQRT(v_a), SQRT(1 - v_a));
END fn_haversine_km;
/

-- ---------------------------------------------------------------------
-- F2. Classify an area change (positive = area lost)
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_classify_change (p_pct NUMBER) RETURN VARCHAR2 DETERMINISTIC IS
BEGIN
  RETURN CASE
           WHEN p_pct >= 10  THEN 'DEFORESTATION'
           WHEN p_pct <= -10 THEN 'REGROWTH'
           WHEN p_pct >= 3   THEN 'GRADUAL_LOSS'
           ELSE 'STABLE'
         END;
END fn_classify_change;
/

-- ---------------------------------------------------------------------
-- F3. Forest loss % at a location between two dates (first vs last tree patch in window)
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_forest_loss_pct (
  p_location_id NUMBER, p_from DATE, p_to DATE
) RETURN NUMBER IS
  v_first NUMBER;
  v_last  NUMBER;
BEGIN
  SELECT MAX(o.area_sq_m) KEEP (DENSE_RANK FIRST ORDER BY i.capture_time),
         MAX(o.area_sq_m) KEEP (DENSE_RANK LAST  ORDER BY i.capture_time)
    INTO v_first, v_last
    FROM detected_object o
    JOIN image_frame i  ON i.image_id = o.image_id
    JOIN object_type t  ON t.object_type_id = o.object_type_id
   WHERE t.type_name = 'Tree Coverage'
     AND i.location_id = p_location_id
     AND i.capture_time BETWEEN p_from AND p_to + 1;
  IF v_first IS NULL OR v_first = 0 THEN
    RETURN NULL;
  END IF;
  RETURN ROUND((v_first - v_last) / v_first * 100, 2);
END fn_forest_loss_pct;
/

-- ---------------------------------------------------------------------
-- PACKAGE pkg_eo : the analytics layer (novelty: temporal lineage + confidence propagation)
-- ---------------------------------------------------------------------
CREATE OR REPLACE PACKAGE pkg_eo AS
  e_no_evidence EXCEPTION;
  PRAGMA EXCEPTION_INIT(e_no_evidence, -20001);

  -- builds the EVOLVED_FROM lineage: links each tree patch to the previous patch at the same location
  PROCEDURE compute_changes;

  -- raises alerts for significant, trustworthy losses
  PROCEDURE raise_alerts (p_min_loss  NUMBER DEFAULT 10,
                          p_min_conf  NUMBER DEFAULT 0.7,
                          p_max_cloud NUMBER DEFAULT 40);

  -- answer-level confidence: retrieval-weighted mean of (detection confidence x clear-sky fraction)
  FUNCTION fn_query_confidence (p_query_id NUMBER) RETURN NUMBER;

  -- stores aggregated_confidence + a generated response sentence on QUERY_LOG
  PROCEDURE finalize_query (p_query_id NUMBER);

  -- validated insert of a detection with SAVEPOINT / ROLLBACK
  PROCEDURE add_detection (p_image_id NUMBER, p_type_name VARCHAR2, p_model_id NUMBER,
                           p_conf NUMBER, p_lat NUMBER, p_lon NUMBER, p_area NUMBER,
                           p_new_id OUT NUMBER);

  -- report for one basin using an explicit parameterised cursor
  PROCEDURE basin_report (p_basin_code VARCHAR2);
END pkg_eo;
/

CREATE OR REPLACE PACKAGE BODY pkg_eo AS

  ---------------------------------------------------------------- compute_changes
  PROCEDURE compute_changes IS
    -- assumes one tree-coverage patch per location per image (true for this dataset)
    CURSOR c_trees IS
      SELECT o.object_id, i.capture_time, o.area_sq_m,
             LAG(o.object_id)    OVER (PARTITION BY i.location_id ORDER BY i.capture_time) AS prev_id,
             LAG(o.area_sq_m)    OVER (PARTITION BY i.location_id ORDER BY i.capture_time) AS prev_area,
             LAG(i.capture_time) OVER (PARTITION BY i.location_id ORDER BY i.capture_time) AS prev_time
        FROM detected_object o
        JOIN image_frame i ON i.image_id = o.image_id
        JOIN object_type t ON t.object_type_id = o.object_type_id
       WHERE t.type_name = 'Tree Coverage'
       ORDER BY i.location_id, i.capture_time;
    v_pct  NUMBER;
    v_days NUMBER;
    v_cnt  PLS_INTEGER := 0;
  BEGIN
    FOR r IN c_trees LOOP
      IF r.prev_id IS NOT NULL AND r.prev_area > 0 THEN
        v_pct  := ROUND((r.prev_area - r.area_sq_m) / r.prev_area * 100, 2);
        v_days := TRUNC(r.capture_time) - TRUNC(r.prev_time);
        IF v_days > 0 THEN
          MERGE INTO object_change oc
          USING (SELECT r.prev_id AS p, r.object_id AS c FROM dual) s
             ON (oc.prev_object_id = s.p AND oc.curr_object_id = s.c)
          WHEN MATCHED THEN UPDATE SET
               oc.change_pct = v_pct, oc.change_type = fn_classify_change(v_pct),
               oc.time_delta_days = v_days,
               oc.rate_pct_per_month = ROUND(v_pct / v_days * 30, 2),
               oc.computed_at = SYSTIMESTAMP
          WHEN NOT MATCHED THEN INSERT
               (prev_object_id, curr_object_id, change_type, change_pct, time_delta_days, rate_pct_per_month)
               VALUES (s.p, s.c, fn_classify_change(v_pct), v_pct, v_days, ROUND(v_pct / v_days * 30, 2));
          v_cnt := v_cnt + 1;
        END IF;
      END IF;
    END LOOP;
    COMMIT;
    DBMS_OUTPUT.PUT_LINE('compute_changes: ' || v_cnt || ' lineage links created/updated.');
  END compute_changes;

  ---------------------------------------------------------------- raise_alerts
  PROCEDURE raise_alerts (p_min_loss NUMBER DEFAULT 10, p_min_conf NUMBER DEFAULT 0.7,
                          p_max_cloud NUMBER DEFAULT 40) IS
    v_cnt PLS_INTEGER := 0;
    v_sev VARCHAR2(10);
  BEGIN
    FOR r IN (
      SELECT oc.change_id, oc.change_pct
        FROM object_change oc
        JOIN detected_object o1 ON o1.object_id = oc.prev_object_id
        JOIN detected_object o2 ON o2.object_id = oc.curr_object_id
        JOIN image_frame i1 ON i1.image_id = o1.image_id
        JOIN image_frame i2 ON i2.image_id = o2.image_id
       WHERE oc.change_pct >= p_min_loss
         AND o1.confidence_score >= p_min_conf AND o2.confidence_score >= p_min_conf
         AND i1.cloud_cover <= p_max_cloud     AND i2.cloud_cover <= p_max_cloud
         AND NOT EXISTS (SELECT 1 FROM alert a WHERE a.change_id = oc.change_id)
    ) LOOP
      v_sev := CASE WHEN r.change_pct >= 25 THEN 'CRITICAL'
                    WHEN r.change_pct >= 15 THEN 'HIGH'
                    ELSE 'MEDIUM' END;
      INSERT INTO alert (change_id, severity, alert_status) VALUES (r.change_id, v_sev, 'OPEN');
      v_cnt := v_cnt + 1;
    END LOOP;
    COMMIT;
    DBMS_OUTPUT.PUT_LINE('raise_alerts: ' || v_cnt || ' new alerts.');
  END raise_alerts;

  ---------------------------------------------------------------- fn_query_confidence
  FUNCTION fn_query_confidence (p_query_id NUMBER) RETURN NUMBER IS
    v_conf NUMBER;
  BEGIN
    SELECT SUM(qe.retrieval_score * o.confidence_score * (1 - i.cloud_cover / 100))
           / NULLIF(SUM(qe.retrieval_score), 0)
      INTO v_conf
      FROM query_evidence qe
      JOIN detected_object o ON o.object_id = qe.object_id
      JOIN image_frame i     ON i.image_id  = o.image_id
     WHERE qe.query_id = p_query_id;
    RETURN ROUND(v_conf, 4);
  END fn_query_confidence;

  ---------------------------------------------------------------- finalize_query
  PROCEDURE finalize_query (p_query_id NUMBER) IS
    v_conf NUMBER;
    v_n    NUMBER;
    v_text VARCHAR2(1000);
  BEGIN
    SELECT COUNT(*) INTO v_n FROM query_evidence WHERE query_id = p_query_id;
    IF v_n = 0 THEN
      RAISE_APPLICATION_ERROR(-20001, 'Query ' || p_query_id || ' has no evidence rows.');
    END IF;
    v_conf := fn_query_confidence(p_query_id);
    v_text := 'Answer based on ' || v_n || ' evidence objects. Overall confidence '
              || TO_CHAR(ROUND(v_conf * 100, 1)) || '% '
              || CASE WHEN v_conf >= 0.75 THEN '(high).'
                      WHEN v_conf >= 0.5  THEN '(moderate - some cloud cover or low-confidence detections).'
                      ELSE '(low - treat with caution).' END;
    UPDATE query_log
       SET aggregated_confidence = v_conf, response_text = v_text
     WHERE query_id = p_query_id;
    COMMIT;
    DBMS_OUTPUT.PUT_LINE('finalize_query(' || p_query_id || '): ' || v_text);
  END finalize_query;

  ---------------------------------------------------------------- add_detection
  PROCEDURE add_detection (p_image_id NUMBER, p_type_name VARCHAR2, p_model_id NUMBER,
                           p_conf NUMBER, p_lat NUMBER, p_lon NUMBER, p_area NUMBER,
                           p_new_id OUT NUMBER) IS
    v_type_id NUMBER;
    v_half    NUMBER := GREATEST(0.0005, SQRT(p_area) / 111000 / 2);
  BEGIN
    SAVEPOINT before_detection;
    BEGIN
      SELECT object_type_id INTO v_type_id FROM object_type WHERE type_name = p_type_name;
    EXCEPTION
      WHEN NO_DATA_FOUND THEN
        RAISE_APPLICATION_ERROR(-20020, 'Unknown object type: ' || p_type_name);
    END;
    INSERT INTO detected_object
      (image_id, object_type_id, model_id, confidence_score,
       bbox_min_lat, bbox_min_lon, bbox_max_lat, bbox_max_lon,
       area_sq_m, object_embedding, attributes_json)
    VALUES
      (p_image_id, v_type_id, p_model_id, p_conf,
       ROUND(p_lat - v_half, 5), ROUND(p_lon - v_half, 5), ROUND(p_lat + v_half, 5), ROUND(p_lon + v_half, 5),
       p_area, '[0,0,0,0,0,0,0,0]', '{"source":"manual"}')
    RETURNING object_id INTO p_new_id;
    COMMIT;
  EXCEPTION
    WHEN DUP_VAL_ON_INDEX OR VALUE_ERROR THEN
      ROLLBACK TO before_detection;
      RAISE_APPLICATION_ERROR(-20021, 'Detection rejected: ' || SQLERRM);
    WHEN OTHERS THEN
      ROLLBACK TO before_detection;
      RAISE;
  END add_detection;

  ---------------------------------------------------------------- basin_report
  PROCEDURE basin_report (p_basin_code VARCHAR2) IS
    CURSOR c_loc (cp_code VARCHAR2) IS
      SELECT l.location_id, l.location_name
        FROM location l JOIN river_basin b ON b.basin_id = l.basin_id
       WHERE b.basin_code = cp_code
       ORDER BY l.location_name;
    v_loss  NUMBER;
    v_found BOOLEAN := FALSE;
  BEGIN
    DBMS_OUTPUT.PUT_LINE('=== Forest loss report, basin ' || p_basin_code || ' (Apr-Sep 2026) ===');
    FOR r IN c_loc(p_basin_code) LOOP
      v_found := TRUE;
      v_loss  := fn_forest_loss_pct(r.location_id, DATE '2026-04-01', DATE '2026-09-30');
      DBMS_OUTPUT.PUT_LINE(RPAD(r.location_name, 28) ||
                           CASE WHEN v_loss IS NULL THEN 'no tree data'
                                ELSE TO_CHAR(v_loss, 'FM990.00') || ' % loss' END);
    END LOOP;
    IF NOT v_found THEN
      DBMS_OUTPUT.PUT_LINE('No locations found for basin code ' || p_basin_code);
    END IF;
  END basin_report;

END pkg_eo;
/
SHOW ERRORS;

-- ---------------------------------------------------------------------
-- T1. Audit trigger on DETECTED_OBJECT (row level, INSERT/UPDATE/DELETE)
-- ---------------------------------------------------------------------
CREATE OR REPLACE TRIGGER trg_obj_audit
AFTER INSERT OR UPDATE OR DELETE ON detected_object
FOR EACH ROW
DECLARE
  v_op  VARCHAR2(10);
  v_id  NUMBER;
  v_old VARCHAR2(500);
  v_new VARCHAR2(500);
BEGIN
  IF INSERTING THEN
    v_op := 'INSERT'; v_id := :NEW.object_id;
    v_new := 'conf=' || :NEW.confidence_score || ', area=' || :NEW.area_sq_m;
  ELSIF UPDATING THEN
    v_op := 'UPDATE'; v_id := :NEW.object_id;
    v_old := 'conf=' || :OLD.confidence_score || ', area=' || :OLD.area_sq_m;
    v_new := 'conf=' || :NEW.confidence_score || ', area=' || :NEW.area_sq_m;
  ELSE
    v_op := 'DELETE'; v_id := :OLD.object_id;
    v_old := 'conf=' || :OLD.confidence_score || ', area=' || :OLD.area_sq_m;
  END IF;
  INSERT INTO audit_log (table_name, operation, record_id, old_value, new_value)
  VALUES ('DETECTED_OBJECT', v_op, v_id, v_old, v_new);
END trg_obj_audit;
/

-- ---------------------------------------------------------------------
-- T2. Business rule: an image cannot be captured in the future (not expressible as CHECK)
-- ---------------------------------------------------------------------
CREATE OR REPLACE TRIGGER trg_img_validate
BEFORE INSERT OR UPDATE OF capture_time ON image_frame
FOR EACH ROW
BEGIN
  IF :NEW.capture_time > SYSTIMESTAMP THEN
    RAISE_APPLICATION_ERROR(-20010, 'capture_time cannot be in the future: ' || TO_CHAR(:NEW.capture_time));
  END IF;
END trg_img_validate;
/

-- ---------------------------------------------------------------------
-- T3. Stamp resolved_at automatically when an alert is resolved
-- ---------------------------------------------------------------------
CREATE OR REPLACE TRIGGER trg_alert_resolve
BEFORE UPDATE OF alert_status ON alert
FOR EACH ROW
BEGIN
  IF :NEW.alert_status = 'RESOLVED' AND :OLD.alert_status <> 'RESOLVED' THEN
    :NEW.resolved_at := SYSTIMESTAMP;
  ELSIF :NEW.alert_status <> 'RESOLVED' THEN
    :NEW.resolved_at := NULL;
  END IF;
END trg_alert_resolve;
/

-- ---------------------------------------------------------------------
-- T4. Keep object_change consistent: prev and curr must be tree patches of the SAME location,
--     and prev must be older than curr.
-- ---------------------------------------------------------------------
CREATE OR REPLACE TRIGGER trg_change_validate
BEFORE INSERT OR UPDATE ON object_change
FOR EACH ROW
DECLARE
  v_loc1 NUMBER; v_loc2 NUMBER;
  v_t1 TIMESTAMP; v_t2 TIMESTAMP;
BEGIN
  SELECT i.location_id, i.capture_time INTO v_loc1, v_t1
    FROM detected_object o JOIN image_frame i ON i.image_id = o.image_id
   WHERE o.object_id = :NEW.prev_object_id;
  SELECT i.location_id, i.capture_time INTO v_loc2, v_t2
    FROM detected_object o JOIN image_frame i ON i.image_id = o.image_id
   WHERE o.object_id = :NEW.curr_object_id;
  IF v_loc1 <> v_loc2 THEN
    RAISE_APPLICATION_ERROR(-20030, 'EVOLVED_FROM must link objects at the same location.');
  END IF;
  IF v_t1 >= v_t2 THEN
    RAISE_APPLICATION_ERROR(-20031, 'Predecessor object must be older than the current object.');
  END IF;
END trg_change_validate;
/

SHOW ERRORS;
