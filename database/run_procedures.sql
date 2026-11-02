-- =====================================================================
-- run_procedures.sql  -- populate derived tables via PL/SQL, then show results
-- Run AFTER schema.sql, sample_data.sql, plsql.sql.  Compare output with expected_results.txt
-- =====================================================================
SET SERVEROUTPUT ON;
SET LINESIZE 200;
SET PAGESIZE 100;

-- 1. temporal lineage (EVOLVED_FROM): expect 36 links
EXEC pkg_eo.compute_changes;

-- 2. alerts: expect 4 with default thresholds (loss>=10%, conf>=0.7, cloud<=40%)
EXEC pkg_eo.raise_alerts;

-- 3. answer-level confidence for the 4 logged GraphRAG queries
BEGIN
  FOR q IN (SELECT query_id FROM query_log ORDER BY query_id) LOOP
    pkg_eo.finalize_query(q.query_id);
  END LOOP;
END;
/

-- 4. per-basin report (cursor + function)
EXEC pkg_eo.basin_report('MKG');
EXEC pkg_eo.basin_report('AMZ');

-- 5. validated insert with SAVEPOINT (valid, then invalid type -> rolled back with error)
DECLARE
  v_id NUMBER;
BEGIN
  pkg_eo.add_detection(p_image_id => 1, p_type_name => 'Building', p_model_id => 2,
                       p_conf => 0.91, p_lat => -2.6, p_lon => -60.3, p_area => 850,
                       p_new_id => v_id);
  DBMS_OUTPUT.PUT_LINE('Inserted detection id = ' || v_id);
  pkg_eo.add_detection(1, 'Spaceship', 2, 0.9, -2.6, -60.3, 100, v_id);   -- raises ORA-20020
EXCEPTION
  WHEN OTHERS THEN DBMS_OUTPUT.PUT_LINE('Expected error caught: ' || SQLERRM);
END;
/

-- 6. trigger demos
-- 6a. future image is rejected by trg_img_validate (ORA-20010)
BEGIN
  INSERT INTO image_frame (sensor_id, location_id, capture_time, cloud_cover, file_path)
  VALUES (1, 1, TIMESTAMP '2030-01-01 00:00:00', 10, '/archive/future.tif');
EXCEPTION WHEN OTHERS THEN DBMS_OUTPUT.PUT_LINE('Expected error caught: ' || SQLERRM);
END;
/
-- 6b. resolving an alert stamps resolved_at (trg_alert_resolve) and detection updates are audited
UPDATE alert SET alert_status = 'RESOLVED', assigned_user_id = 1 WHERE alert_id = (SELECT MIN(alert_id) FROM alert);
UPDATE detected_object SET confidence_score = 0.99 WHERE object_id = (SELECT MIN(object_id) FROM detected_object);
COMMIT;

COLUMN table_name    FORMAT A16
COLUMN operation     FORMAT A9
COLUMN old_value     FORMAT A30
COLUMN new_value     FORMAT A30
COLUMN response_text FORMAT A90
COLUMN resolved_at   FORMAT A30

SELECT alert_id, severity, alert_status, assigned_user_id, resolved_at FROM alert ORDER BY alert_id;
SELECT audit_id, table_name, operation, record_id, old_value, new_value FROM audit_log ORDER BY audit_id;
SELECT query_id, aggregated_confidence, response_text FROM query_log ORDER BY query_id;
