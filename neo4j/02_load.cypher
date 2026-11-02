// =====================================================================
// 02_load.cypher  -- load the CSVs from neo4j/import/ into the graph
// Copy all *.csv into the Neo4j "import" folder first (Neo4j Desktop: ... > Open folder > Import).
// Run statements one by one (or all together in Neo4j Browser).
// =====================================================================

// ---------- nodes ----------
LOAD CSV WITH HEADERS FROM 'file:///satellites.csv' AS row
MERGE (s:Satellite {satellite_id: toInteger(row.satellite_id)})
SET s.name = row.name, s.operator = row.operator, s.launch_date = date(row.launch_date),
    s.orbit_type = row.orbit_type, s.status = row.status, s.revisit_days = toInteger(row.revisit_days);

LOAD CSV WITH HEADERS FROM 'file:///sensors.csv' AS row
MERGE (n:Sensor {sensor_id: toInteger(row.sensor_id)})
SET n.name = row.name, n.sensor_type = row.sensor_type,
    n.resolution_m = toFloat(row.resolution_m), n.num_bands = toInteger(row.num_bands)
WITH n, row
MATCH (s:Satellite {satellite_id: toInteger(row.satellite_id)})
MERGE (n)-[:ONBOARD]->(s);

LOAD CSV WITH HEADERS FROM 'file:///basins.csv' AS row
MERGE (b:RiverBasin {basin_id: toInteger(row.basin_id)})
SET b.name = row.name, b.code = row.code, b.area_sq_km = toInteger(row.area_sq_km),
    b.river_point = point({latitude: toFloat(row.river_ref_lat), longitude: toFloat(row.river_ref_lon)});

LOAD CSV WITH HEADERS FROM 'file:///locations.csv' AS row
MERGE (l:Location {location_id: toInteger(row.location_id)})
SET l.name = row.name, l.country = row.country, l.region_type = row.region_type,
    l.point = point({latitude: toFloat(row.lat), longitude: toFloat(row.lon)});

// Location -> RiverBasin (only for locations that belong to a basin)
LOAD CSV WITH HEADERS FROM 'file:///locations.csv' AS row
WITH row WHERE row.basin_id IS NOT NULL AND row.basin_id <> ''
MATCH (l:Location {location_id: toInteger(row.location_id)})
MATCH (b:RiverBasin {basin_id: toInteger(row.basin_id)})
MERGE (l)-[:BELONGS_TO]->(b);

LOAD CSV WITH HEADERS FROM 'file:///object_types.csv' AS row
MERGE (t:ObjectType {object_type_id: toInteger(row.object_type_id)})
SET t.type_name = row.type_name, t.category = row.category;

LOAD CSV WITH HEADERS FROM 'file:///models.csv' AS row
MERGE (m:DetectionModel {model_id: toInteger(row.model_id)})
SET m.name = row.name, m.version = row.version, m.architecture = row.architecture;

LOAD CSV WITH HEADERS FROM 'file:///users.csv' AS row
MERGE (u:AppUser {user_id: toInteger(row.user_id)})
SET u.full_name = row.full_name, u.email = row.email, u.role = row.role;

// ---------- ImageFrame + CAPTURED_BY / USED_SENSOR / LOCATED_AT ----------
LOAD CSV WITH HEADERS FROM 'file:///images.csv' AS row
MERGE (i:ImageFrame {image_id: toInteger(row.image_id)})
SET i.capture_time = datetime(row.capture_time), i.cloud_cover = toFloat(row.cloud_cover),
    i.sun_elevation = toFloat(row.sun_elevation), i.file_path = row.file_path,
    i.ndvi_mean = toFloat(row.ndvi_mean), i.ndwi_mean = toFloat(row.ndwi_mean),
    i.embedding = [x IN split(row.embedding, ';') | toFloat(x)]
WITH i, row
MATCH (sat:Satellite {satellite_id: toInteger(row.satellite_id)})
MATCH (sen:Sensor    {sensor_id:    toInteger(row.sensor_id)})
MATCH (loc:Location  {location_id:  toInteger(row.location_id)})
MERGE (i)-[:CAPTURED_BY]->(sat)
MERGE (i)-[:USED_SENSOR]->(sen)
MERGE (i)-[:LOCATED_AT]->(loc);

// ---------- DetectedObject + DETECTED_IN / HAS_TYPE / DETECTED_BY ----------
// type_name is duplicated on the node on purpose: a graph-style denormalisation that makes filters cheaper.
LOAD CSV WITH HEADERS FROM 'file:///objects.csv' AS row
MERGE (o:DetectedObject {object_id: toInteger(row.object_id)})
SET o.type_name = row.type_name, o.confidence = toFloat(row.confidence), o.area_sq_m = toFloat(row.area_sq_m),
    o.bbox_center = point({latitude:  (toFloat(row.bbox_min_lat) + toFloat(row.bbox_max_lat)) / 2,
                           longitude: (toFloat(row.bbox_min_lon) + toFloat(row.bbox_max_lon)) / 2}),
    o.attributes_json = row.attributes_json,
    o.embedding = [x IN split(row.embedding, ';') | toFloat(x)]
WITH o, row
MATCH (i:ImageFrame     {image_id:       toInteger(row.image_id)})
MATCH (t:ObjectType     {object_type_id: toInteger(row.object_type_id)})
MATCH (m:DetectionModel {model_id:       toInteger(row.model_id)})
MERGE (o)-[:DETECTED_IN]->(i)
MERGE (o)-[:HAS_TYPE]->(t)
MERGE (o)-[:DETECTED_BY]->(m);

// ---------- EVOLVED_FROM : the temporal-lineage novelty ----------
// direction: newer observation -> its predecessor
LOAD CSV WITH HEADERS FROM 'file:///changes.csv' AS row
MATCH (curr:DetectedObject {object_id: toInteger(row.curr_object_id)})
MATCH (prev:DetectedObject {object_id: toInteger(row.prev_object_id)})
MERGE (curr)-[e:EVOLVED_FROM]->(prev)
SET e.change_type = row.change_type, e.change_pct = toFloat(row.change_pct),
    e.time_delta_days = toInteger(row.time_delta_days), e.rate_pct_per_month = toFloat(row.rate_pct_per_month);

// ---------- GraphRAG query log + evidence ----------
LOAD CSV WITH HEADERS FROM 'file:///queries.csv' AS row
MERGE (q:QueryLog {query_id: toInteger(row.query_id)})
SET q.text = row.text, q.submitted_at = datetime(row.submitted_at),
    q.alpha = toFloat(row.alpha), q.beta = toFloat(row.beta), q.gamma = toFloat(row.gamma),
    q.embedding = [x IN split(row.embedding, ';') | toFloat(x)]
WITH q, row
MATCH (u:AppUser {user_id: toInteger(row.user_id)})
MERGE (q)-[:ASKED_BY]->(u);

LOAD CSV WITH HEADERS FROM 'file:///evidence.csv' AS row
MATCH (q:QueryLog {query_id: toInteger(row.query_id)})
MATCH (o:DetectedObject {object_id: toInteger(row.object_id)})
MERGE (q)-[e:USED_AS_EVIDENCE]->(o)
SET e.semantic_score = toFloat(row.semantic_score), e.spatial_score = toFloat(row.spatial_score),
    e.temporal_score = toFloat(row.temporal_score), e.retrieval_score = toFloat(row.retrieval_score),
    e.rank_position = toInteger(row.rank_position);

// ---------- sanity check ----------
MATCH (n) RETURN labels(n)[0] AS label, count(*) AS nodes ORDER BY nodes DESC;
// expected: DetectedObject 247, ImageFrame 60, Location 12, ObjectType 8, Sensor 6, Satellite 5, RiverBasin 5, QueryLog 4, AppUser 4, DetectionModel 3
MATCH ()-[r]->() RETURN type(r) AS rel, count(*) AS n ORDER BY n DESC;
