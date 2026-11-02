-- =====================================================================
-- queries.sql  -- SQL queries (run after run_procedures.sql)
-- =====================================================================
SET LINESIZE 220;
SET PAGESIZE 100;
COLUMN location_name FORMAT A24;
COLUMN basin_name    FORMAT A16;
COLUMN satellite_name FORMAT A14;

-- ===== A. BASIC / JOINS ==============================================
-- Q1. Images with satellite, sensor, location (4-table join, via view)
SELECT image_id, capture_time, satellite_name, sensor_name, location_name, cloud_cover
FROM v_image_summary
ORDER BY capture_time, image_id
FETCH FIRST 10 ROWS ONLY;

-- Q2. Locations that do NOT belong to any river basin (LEFT JOIN anti-pattern)
SELECT l.location_name, l.country, l.region_type
FROM location l LEFT JOIN river_basin b ON b.basin_id = l.basin_id
WHERE b.basin_id IS NULL;

-- Q3. Images captured in Q3 2026 with cloud cover under 20% (clear-sky usable images)
SELECT image_id, location_name, capture_time, cloud_cover
FROM v_image_summary
WHERE capture_time >= TIMESTAMP '2026-07-01 00:00:00'
  AND capture_time <  TIMESTAMP '2026-10-01 00:00:00'
  AND cloud_cover < 20
ORDER BY cloud_cover;

-- ===== B. AGGREGATES / GROUP BY / HAVING ==============================
-- Q4. Images and average cloud cover per satellite
SELECT s.satellite_name, COUNT(*) AS images, ROUND(AVG(i.cloud_cover), 1) AS avg_cloud
FROM image_frame i JOIN sensor sn ON sn.sensor_id = i.sensor_id
JOIN satellite s ON s.satellite_id = sn.satellite_id
GROUP BY s.satellite_name
ORDER BY images DESC;

-- Q5. Detected objects per type with average confidence; keep types with > 5 detections
SELECT t.type_name, t.category, COUNT(*) AS detections, ROUND(AVG(o.confidence_score), 3) AS avg_conf
FROM detected_object o JOIN object_type t ON t.object_type_id = o.object_type_id
GROUP BY t.type_name, t.category
HAVING COUNT(*) > 5
ORDER BY detections DESC;

-- Q6. Object count per basin (object -> image -> location -> basin, 4-hop join)
SELECT b.basin_name, COUNT(o.object_id) AS objects
FROM river_basin b
LEFT JOIN location l       ON l.basin_id = b.basin_id
LEFT JOIN image_frame i    ON i.location_id = l.location_id
LEFT JOIN detected_object o ON o.image_id = i.image_id
GROUP BY b.basin_name
ORDER BY objects DESC;

-- ===== C. SUBQUERIES =================================================
-- Q7. Images cloudier than the overall average (non-correlated subquery)
SELECT image_id, location_name, cloud_cover
FROM v_image_summary
WHERE cloud_cover > (SELECT AVG(cloud_cover) FROM image_frame)
ORDER BY cloud_cover DESC
FETCH FIRST 8 ROWS ONLY;

-- Q8. Latest tree-coverage area per location (correlated subquery)
SELECT l.location_name, o.area_sq_m AS latest_tree_area_m2, i.capture_time
FROM detected_object o
JOIN image_frame i ON i.image_id = o.image_id
JOIN location l   ON l.location_id = i.location_id
JOIN object_type t ON t.object_type_id = o.object_type_id
WHERE t.type_name = 'Tree Coverage'
  AND i.capture_time = (SELECT MAX(i2.capture_time)
                        FROM image_frame i2
                        JOIN detected_object o2 ON o2.image_id = i2.image_id
                        WHERE i2.location_id = i.location_id
                          AND o2.object_type_id = o.object_type_id)
ORDER BY l.location_name;

-- Q9. Locations that have deforestation but NO alert (MINUS set operation)
SELECT DISTINCT location_name FROM v_forest_change WHERE change_type = 'DEFORESTATION'
MINUS
SELECT DISTINCT fc.location_name
FROM v_forest_change fc JOIN alert a ON a.change_id = fc.change_id;

-- ===== D. THE PROJECT'S TARGET QUERY IN PLAIN SQL ====================
-- Q10. "Rapid deforestation near river basins in Q3"
--      semantic part is approximated by tree-coverage loss (graph/vector version is in DA3)
SELECT fc.location_name, fc.basin_name, fc.curr_time, fc.change_pct, fc.rate_pct_per_month,
       ROUND(fn_haversine_km(l.latitude, l.longitude, b.river_ref_lat, b.river_ref_lon), 0) AS km_to_river
FROM v_forest_change fc
JOIN location l     ON l.location_id = fc.location_id
JOIN river_basin b  ON b.basin_id = l.basin_id
WHERE fc.change_type = 'DEFORESTATION'
  AND fc.curr_time >= TIMESTAMP '2026-07-01 00:00:00'
  AND fc.curr_time <  TIMESTAMP '2026-10-01 00:00:00'
  AND fn_haversine_km(l.latitude, l.longitude, b.river_ref_lat, b.river_ref_lon) <= 300
ORDER BY fc.rate_pct_per_month DESC;

-- Q11. Same question but trust-aware: only changes backed by clear, confident evidence
SELECT location_name, curr_time, change_pct,
       ROUND(LEAST(prev_conf, curr_conf) * (1 - GREATEST(prev_cloud, curr_cloud) / 100), 3) AS evidence_trust
FROM v_forest_change
WHERE change_type = 'DEFORESTATION'
ORDER BY evidence_trust DESC;

-- ===== E. ANALYTIC / WINDOW FUNCTIONS =================================
-- Q12. Cumulative loss and rank per location over time
SELECT location_name, curr_time, change_pct,
       SUM(change_pct) OVER (PARTITION BY location_id ORDER BY curr_time) AS cumulative_loss_pct,
       RANK() OVER (PARTITION BY curr_time ORDER BY change_pct DESC)      AS rank_in_period
FROM v_forest_change
ORDER BY curr_time, rank_in_period;

-- Q13. Recursive lineage: full EVOLVED_FROM chain of every tree patch (CONNECT BY)
SELECT LEVEL AS step,
       SYS_CONNECT_BY_PATH(TO_CHAR(oc.prev_object_id), ' -> ') || ' -> ' || oc.curr_object_id AS chain,
       oc.change_pct AS last_step_loss_pct
FROM object_change oc
START WITH oc.prev_object_id NOT IN (SELECT curr_object_id FROM object_change)
CONNECT BY PRIOR oc.curr_object_id = oc.prev_object_id
ORDER SIBLINGS BY oc.prev_object_id;

-- ===== F. SPECTRAL / STATISTICS / JSON ================================
-- Q14. Correlation between NDVI and detected tree area (sanity check of the detector)
SELECT ROUND(CORR(si.mean_value, o.area_sq_m), 3) AS ndvi_vs_tree_area_corr
FROM spectral_index si
JOIN detected_object o ON o.image_id = si.image_id
JOIN object_type t ON t.object_type_id = o.object_type_id
WHERE si.index_name = 'NDVI' AND t.type_name = 'Tree Coverage';

-- Q15. JSON attribute query: vessels by class
SELECT JSON_VALUE(attributes_json, '$.vessel_class') AS vessel_class,
       COUNT(*) AS n, ROUND(AVG(JSON_VALUE(attributes_json, '$.length_m' RETURNING NUMBER)), 1) AS avg_length_m
FROM detected_object o JOIN object_type t ON t.object_type_id = o.object_type_id
WHERE t.type_name = 'Vessel'
GROUP BY JSON_VALUE(attributes_json, '$.vessel_class')
ORDER BY n DESC;

-- Q16. Resolution bucket analysis with CASE
SELECT CASE WHEN sn.spatial_resolution_m <= 10 THEN 'High (<=10 m)'
            WHEN sn.spatial_resolution_m <= 30 THEN 'Medium (<=30 m)'
            ELSE 'Coarse' END AS res_class,
       COUNT(*) AS images, ROUND(AVG(i.cloud_cover), 1) AS avg_cloud
FROM image_frame i JOIN sensor sn ON sn.sensor_id = i.sensor_id
GROUP BY CASE WHEN sn.spatial_resolution_m <= 10 THEN 'High (<=10 m)'
              WHEN sn.spatial_resolution_m <= 30 THEN 'Medium (<=30 m)'
              ELSE 'Coarse' END;

-- ===== G. GRAPHRAG-STYLE EVIDENCE + ALERTS ============================
-- Q17. Evidence behind each logged question, with answer-level confidence
SELECT q.query_id, SUBSTR(q.query_text, 1, 45) AS question, qe.rank_position,
       t.type_name, l.location_name, qe.retrieval_score, q.aggregated_confidence
FROM query_log q
JOIN query_evidence qe ON qe.query_id = q.query_id
JOIN detected_object o ON o.object_id = qe.object_id
JOIN object_type t     ON t.object_type_id = o.object_type_id
JOIN image_frame i     ON i.image_id = o.image_id
JOIN location l        ON l.location_id = i.location_id
WHERE qe.rank_position <= 3
ORDER BY q.query_id, qe.rank_position;

-- Q18. Open alerts dashboard
SELECT a.alert_id, a.severity, l.location_name, b.basin_name, fc.change_pct, a.alert_status, u.full_name AS assignee
FROM alert a
JOIN v_forest_change fc ON fc.change_id = a.change_id
JOIN location l ON l.location_id = fc.location_id
LEFT JOIN river_basin b ON b.basin_id = l.basin_id
LEFT JOIN app_user u ON u.user_id = a.assigned_user_id
ORDER BY CASE a.severity WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 ELSE 3 END, fc.change_pct DESC;

-- ===== H. DML + TRANSACTION CONTROL ===================================
-- Q19. UPDATE with subquery, SAVEPOINT, ROLLBACK
SAVEPOINT s1;
UPDATE image_frame SET sun_elevation = sun_elevation + 1 WHERE location_id = 1;
SELECT COUNT(*) AS rows_touched FROM image_frame WHERE location_id = 1;
ROLLBACK TO s1;

-- Q20. Expected integrity failures (run one at a time; each should error)
INSERT INTO image_frame (sensor_id, location_id, capture_time, cloud_cover, file_path)
VALUES (1, 1, TIMESTAMP '2026-05-01 05:30:00', 140, '/archive/bad.tif');          -- ORA-02290 cloud_cover CHECK
INSERT INTO detected_object (image_id, object_type_id, model_id, confidence_score, bbox_min_lat, bbox_min_lon,
bbox_max_lat, bbox_max_lon, area_sq_m) VALUES (99999, 1, 1, 0.9, 0, 0, 1, 1, 10);  -- ORA-02291 FK parent missing
DELETE FROM satellite WHERE satellite_id = 1;   -- ORA-02292 child images exist (sensor->image_frame has no cascade)
