-- =====================================================================
-- extras.sql  -- extra DA2 coverage: DDL (ALTER), DCL (GRANT/REVOKE),
--   explicit cursor (OPEN/FETCH/CLOSE), cursor FOR UPDATE / WHERE CURRENT OF,
--   user-defined exception, standalone procedure with IN/OUT, %TYPE / %ROWTYPE,
--   statement-level trigger, COMMENT ON, DELETE with ROLLBACK.
-- Run AFTER the other scripts, with F5 (Run Script). Turn on View -> Dbms Output first.
-- Safe to re-run.
-- =====================================================================
SET SERVEROUTPUT ON;
SET DEFINE OFF;

-- =====================================================================
-- E1. DDL : ALTER TABLE (add / modify / rename / constraint / disable / enable / drop)
-- =====================================================================
-- clean-up so the script can be re-run
BEGIN EXECUTE IMMEDIATE 'ALTER TABLE satellite DROP COLUMN mass_kg';        EXCEPTION WHEN OTHERS THEN NULL; END;
/
BEGIN EXECUTE IMMEDIATE 'ALTER TABLE satellite DROP COLUMN launch_mass_kg'; EXCEPTION WHEN OTHERS THEN NULL; END;
/

-- add a column
ALTER TABLE satellite ADD (launch_mass_kg NUMBER(6));

-- fill it
UPDATE satellite SET launch_mass_kg = CASE satellite_name
  WHEN 'Sentinel-2A' THEN 1140 WHEN 'Sentinel-2B' THEN 1130
  WHEN 'Landsat 8'   THEN 2623 WHEN 'Landsat 9'   THEN 2711
  WHEN 'Sentinel-1A' THEN 2300 END;
COMMIT;

-- modify its size, add a CHECK constraint, rename it
ALTER TABLE satellite MODIFY (launch_mass_kg NUMBER(7));
ALTER TABLE satellite ADD CONSTRAINT ck_sat_mass CHECK (launch_mass_kg > 0);
ALTER TABLE satellite RENAME COLUMN launch_mass_kg TO mass_kg;

SELECT satellite_name, mass_kg FROM satellite ORDER BY mass_kg DESC;

-- disable / enable a constraint (and show its status from the data dictionary)
ALTER TABLE image_frame DISABLE CONSTRAINT ck_img_cloud;
SELECT constraint_name, status FROM user_constraints WHERE constraint_name = 'CK_IMG_CLOUD';
ALTER TABLE image_frame ENABLE CONSTRAINT ck_img_cloud;
SELECT constraint_name, status FROM user_constraints WHERE constraint_name = 'CK_IMG_CLOUD';

-- table / column comments (stored in the data dictionary)
COMMENT ON TABLE  object_change IS 'EVOLVED_FROM lineage: links the same real-world object across two dates';
COMMENT ON COLUMN object_change.change_pct IS 'Positive = area lost, negative = area gained (percent)';
SELECT table_name, comments FROM user_tab_comments WHERE table_name = 'OBJECT_CHANGE';

-- drop the demo column again (keeps the schema identical to schema.sql)
ALTER TABLE satellite DROP COLUMN mass_kg;

-- =====================================================================
-- E2. DCL : GRANT and REVOKE (object privileges on our own objects)
-- =====================================================================
GRANT SELECT ON v_image_summary TO PUBLIC;
GRANT SELECT, INSERT ON query_log TO PUBLIC;

SELECT grantee, table_name, privilege
FROM user_tab_privs_made
WHERE table_name IN ('V_IMAGE_SUMMARY', 'QUERY_LOG')
ORDER BY table_name, privilege;

REVOKE INSERT ON query_log FROM PUBLIC;
REVOKE SELECT ON query_log FROM PUBLIC;
REVOKE SELECT ON v_image_summary FROM PUBLIC;

SELECT COUNT(*) AS privileges_left
FROM user_tab_privs_made
WHERE table_name IN ('V_IMAGE_SUMMARY', 'QUERY_LOG');

-- =====================================================================
-- E3. Explicit cursor: OPEN / FETCH / CLOSE with %ISOPEN, %NOTFOUND, %ROWCOUNT, %ROWTYPE
-- =====================================================================
DECLARE
  CURSOR c_sat IS
    SELECT s.satellite_name,
           COUNT(i.image_id)          AS images,
           ROUND(AVG(i.cloud_cover), 1) AS avg_cloud
      FROM satellite s
      LEFT JOIN sensor sn     ON sn.satellite_id = s.satellite_id
      LEFT JOIN image_frame i ON i.sensor_id = sn.sensor_id
     GROUP BY s.satellite_name
     ORDER BY images DESC, s.satellite_name;
  r c_sat%ROWTYPE;
BEGIN
  OPEN c_sat;
  IF c_sat%ISOPEN THEN
    DBMS_OUTPUT.PUT_LINE('--- E3: explicit cursor opened ---');
  END IF;
  LOOP
    FETCH c_sat INTO r;
    EXIT WHEN c_sat%NOTFOUND;
    DBMS_OUTPUT.PUT_LINE(RPAD(r.satellite_name, 14) || ' images=' || LPAD(r.images, 3)
                         || '  avg_cloud=' || NVL(TO_CHAR(r.avg_cloud), 'n/a'));
  END LOOP;
  DBMS_OUTPUT.PUT_LINE('Rows fetched (%ROWCOUNT) = ' || c_sat%ROWCOUNT);
  CLOSE c_sat;
END;
/

-- =====================================================================
-- E4. Cursor FOR UPDATE + WHERE CURRENT OF (escalate big open alerts), then ROLLBACK
-- =====================================================================
-- The cursor reads ALERT only (WHERE CURRENT OF needs a single-table cursor, otherwise ORA-01410);
-- the change % is looked up inside the loop.
DECLARE
  CURSOR c_alert IS
    SELECT a.alert_id, a.severity, a.change_id
      FROM alert a
     WHERE a.alert_status = 'OPEN' AND a.severity = 'MEDIUM'
       FOR UPDATE OF a.severity;
  v_pct object_change.change_pct%TYPE;
  v_cnt PLS_INTEGER := 0;
BEGIN
  DBMS_OUTPUT.PUT_LINE('--- E4: cursor FOR UPDATE ---');
  FOR r IN c_alert LOOP
    SELECT change_pct INTO v_pct FROM object_change WHERE change_id = r.change_id;
    IF v_pct >= 13 THEN
      UPDATE alert SET severity = 'HIGH' WHERE CURRENT OF c_alert;
      v_cnt := v_cnt + 1;
      DBMS_OUTPUT.PUT_LINE('Escalated alert ' || r.alert_id || ' (loss ' || v_pct || '%) to HIGH');
    END IF;
  END LOOP;
  DBMS_OUTPUT.PUT_LINE(v_cnt || ' alert(s) escalated; rolling back to keep demo data unchanged.');
  ROLLBACK;
END;
/

-- =====================================================================
-- E5. User-defined exception with RAISE (plus %TYPE anchoring)
-- =====================================================================
DECLARE
  e_too_cloudy EXCEPTION;
  v_cloud    image_frame.cloud_cover%TYPE;
  v_location location.location_name%TYPE;
  c_limit    CONSTANT NUMBER := 40;
BEGIN
  DBMS_OUTPUT.PUT_LINE('--- E5: user-defined exception ---');
  FOR img IN (SELECT image_id FROM image_frame WHERE image_id IN (23, 18) ORDER BY image_id DESC) LOOP
    BEGIN
      SELECT i.cloud_cover, l.location_name INTO v_cloud, v_location
        FROM image_frame i JOIN location l ON l.location_id = i.location_id
       WHERE i.image_id = img.image_id;
      IF v_cloud > c_limit THEN
        RAISE e_too_cloudy;
      END IF;
      DBMS_OUTPUT.PUT_LINE('Image ' || img.image_id || ' (' || v_location || ', cloud ' || v_cloud
                           || '%) accepted for analysis.');
    EXCEPTION
      WHEN e_too_cloudy THEN
        DBMS_OUTPUT.PUT_LINE('Image ' || img.image_id || ' (' || v_location || ') rejected: cloud '
                             || v_cloud || '% exceeds ' || c_limit || '%.');
      WHEN NO_DATA_FOUND THEN
        DBMS_OUTPUT.PUT_LINE('Image ' || img.image_id || ' not found.');
    END;
  END LOOP;
END;
/

-- =====================================================================
-- E6. Standalone procedure with IN and OUT parameters
-- =====================================================================
CREATE OR REPLACE PROCEDURE proc_satellite_stats (
  p_name      IN  satellite.satellite_name%TYPE,
  p_images    OUT NUMBER,
  p_avg_cloud OUT NUMBER,
  p_objects   OUT NUMBER
) IS
  v_dummy satellite.satellite_id%TYPE;
BEGIN
  SELECT satellite_id INTO v_dummy FROM satellite WHERE satellite_name = p_name;

  SELECT COUNT(*), ROUND(AVG(i.cloud_cover), 1)
    INTO p_images, p_avg_cloud
    FROM image_frame i JOIN sensor sn ON sn.sensor_id = i.sensor_id
   WHERE sn.satellite_id = v_dummy;

  SELECT COUNT(*) INTO p_objects
    FROM detected_object o
    JOIN image_frame i ON i.image_id = o.image_id
    JOIN sensor sn     ON sn.sensor_id = i.sensor_id
   WHERE sn.satellite_id = v_dummy;
EXCEPTION
  WHEN NO_DATA_FOUND THEN
    RAISE_APPLICATION_ERROR(-20040, 'No satellite named ' || p_name);
END proc_satellite_stats;
/

DECLARE
  v_img NUMBER; v_cloud NUMBER; v_obj NUMBER;
BEGIN
  DBMS_OUTPUT.PUT_LINE('--- E6: procedure with OUT parameters ---');
  proc_satellite_stats('Sentinel-2A', v_img, v_cloud, v_obj);
  DBMS_OUTPUT.PUT_LINE('Sentinel-2A: ' || v_img || ' images, avg cloud ' || v_cloud
                       || '%, ' || v_obj || ' detected objects');
  proc_satellite_stats('Hubble', v_img, v_cloud, v_obj);   -- raises ORA-20040
EXCEPTION
  WHEN OTHERS THEN DBMS_OUTPUT.PUT_LINE('Expected error caught: ' || SQLERRM);
END;
/

-- =====================================================================
-- E7. Statement-level trigger (fires ONCE per statement, no FOR EACH ROW)
-- =====================================================================
CREATE OR REPLACE TRIGGER trg_alert_stmt_audit
AFTER INSERT OR UPDATE OR DELETE ON alert
DECLARE
  v_op VARCHAR2(10);
BEGIN
  -- INSERTING / UPDATING are PL/SQL booleans, so they are evaluated here, not inside the SQL INSERT
  IF INSERTING THEN
    v_op := 'INSERT';
  ELSIF UPDATING THEN
    v_op := 'UPDATE';
  ELSE
    v_op := 'DELETE';
  END IF;
  INSERT INTO audit_log (table_name, operation, record_id, new_value)
  VALUES ('ALERT', v_op, NULL, 'statement-level trigger: ALERT table modified');
END trg_alert_stmt_audit;
/

DECLARE
  v_before NUMBER; v_after NUMBER; v_rows NUMBER;
BEGIN
  DBMS_OUTPUT.PUT_LINE('--- E7: statement-level trigger ---');
  SELECT COUNT(*) INTO v_before FROM audit_log WHERE table_name = 'ALERT';
  UPDATE alert SET alert_status = 'REVIEWING' WHERE alert_status = 'OPEN';
  v_rows := SQL%ROWCOUNT;
  SELECT COUNT(*) INTO v_after FROM audit_log WHERE table_name = 'ALERT';
  DBMS_OUTPUT.PUT_LINE(v_rows || ' rows updated, but audit rows added by statement trigger = '
                       || (v_after - v_before) || ' (fires once per statement)');
  ROLLBACK;
END;
/

-- =====================================================================
-- E8. DELETE with SAVEPOINT / ROLLBACK (ON DELETE CASCADE in action)
-- =====================================================================
SAVEPOINT before_delete;
SELECT COUNT(*) AS objects_in_image_60 FROM detected_object WHERE image_id = 60;
DELETE FROM image_frame WHERE image_id = 60;               -- cascades to detected_object, spectral_index
SELECT COUNT(*) AS objects_after_delete FROM detected_object WHERE image_id = 60;
ROLLBACK TO before_delete;
SELECT COUNT(*) AS objects_after_rollback FROM detected_object WHERE image_id = 60;

-- =====================================================================
-- E9. Data dictionary: everything this project created
-- =====================================================================
SELECT object_type, COUNT(*) AS how_many
FROM user_objects
WHERE object_type IN ('TABLE', 'VIEW', 'INDEX', 'FUNCTION', 'PROCEDURE', 'PACKAGE', 'PACKAGE BODY', 'TRIGGER')
GROUP BY object_type
ORDER BY object_type;
