// =====================================================================
// 04_crud.cypher  -- DA3 "demonstration of database operations"
// CREATE, READ, UPDATE (SET / REMOVE), MERGE (upsert), DELETE, constraint violation.
// Run each block separately, in order. Leaves the dataset exactly as it was.
// =====================================================================

// ---- C1. CREATE: a new satellite, its sensor, and one new image at Kratie ----
MATCH (l:Location {name: 'Kratie Riverbank'})
CREATE (s:Satellite {satellite_id: 900, name: 'Demo-Sat-1', operator: 'VIT Demo', orbit_type: 'SUN-SYNCHRONOUS', status: 'ACTIVE', revisit_days: 5})
CREATE (sn:Sensor {sensor_id: 900, name: 'DemoCam', sensor_type: 'OPTICAL', resolution_m: 3.0, num_bands: 4})-[:ONBOARD]->(s)
CREATE (i:ImageFrame {image_id: 900, capture_time: datetime('2026-10-15T05:30:00'), cloud_cover: 12.5, sun_elevation: 48.0,
                      file_path: '/archive/demo/20261015.tif', ndvi_mean: 0.61, ndwi_mean: 0.05})
CREATE (i)-[:CAPTURED_BY]->(s), (i)-[:USED_SENSOR]->(sn), (i)-[:LOCATED_AT]->(l)
RETURN s.name AS satellite, sn.name AS sensor, i.image_id AS image, l.name AS location;

// ---- C2. READ: everything connected to the new image (also try the Graph view) ----
MATCH p = (i:ImageFrame {image_id: 900})-[]-(n)
RETURN p;

// ---- C3. UPDATE with SET: correct the cloud cover and add a new property ----
MATCH (i:ImageFrame {image_id: 900})
SET i.cloud_cover = 8.0, i.quality_flag = 'VERIFIED'
RETURN i.image_id, i.cloud_cover, i.quality_flag;

// ---- C4. UPDATE with REMOVE: drop the property and add / remove a label ----
MATCH (i:ImageFrame {image_id: 900})
REMOVE i.quality_flag
SET i:Reviewed
RETURN i.image_id, labels(i) AS labels_after_set, i.quality_flag AS quality_flag_now;

MATCH (i:Reviewed {image_id: 900})
REMOVE i:Reviewed
RETURN labels(i) AS labels_after_remove;

// ---- C5. MERGE (upsert): first run creates, second run matches ----
MERGE (t:ObjectType {object_type_id: 99})
  ON CREATE SET t.type_name = 'Solar Farm', t.category = 'ENERGY', t.created = true
  ON MATCH  SET t.matched_again = true
RETURN t.type_name, t.created, t.matched_again;
// run the same MERGE again: matched_again becomes true, no duplicate node is created
MATCH (t:ObjectType {type_name: 'Solar Farm'}) RETURN count(t) AS solar_farm_nodes;

// ---- C6. Constraint violation: duplicate primary key is rejected (expected ERROR) ----
CREATE (:Satellite {satellite_id: 900, name: 'Duplicate'});
// expected: Node(...) already exists with label `Satellite` and property `satellite_id` = 900

// ---- C7. Aggregation over the update: images per satellite including the demo one ----
MATCH (s:Satellite)<-[:CAPTURED_BY]-(i:ImageFrame)
RETURN s.name AS satellite, count(i) AS images ORDER BY images DESC;

// ---- C8. DELETE: plain DELETE fails while relationships exist; DETACH DELETE removes them ----
MATCH (s:Satellite {satellite_id: 900}) DELETE s;
// expected ERROR: cannot delete node because it still has relationships (referential integrity)

MATCH (n) WHERE (n:Satellite OR n:Sensor OR n:ImageFrame) AND
               (n.satellite_id = 900 OR n.sensor_id = 900 OR n.image_id = 900)
DETACH DELETE n;
MATCH (t:ObjectType {object_type_id: 99}) DETACH DELETE t;

// ---- C9. Verify the dataset is back to normal ----
MATCH (n) RETURN labels(n)[0] AS label, count(*) AS nodes ORDER BY nodes DESC;
// expected again: DetectedObject 247, ImageFrame 60, Location 12, ObjectType 8, Sensor 6, Satellite 5, ...
