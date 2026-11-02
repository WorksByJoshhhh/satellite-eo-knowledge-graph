// =====================================================================
// 05_graph_views.cypher  -- graph-picture versions of every query (G1-G14)
// Each query RETURNS nodes / paths, so the result card can show the Graph view.
// Run one at a time, then click "Graph" on the result card and screenshot it.
// Tip: click a label in the legend to choose which property is shown as the caption (e.g. name).
// =====================================================================

// ---- V0. Schema / model of the whole database ----
CALL db.schema.visualization();

// ---- V1 (G1). One satellite, its sensor, its images and where they were taken ----
MATCH p = (s:Satellite {name: 'Sentinel-2A'})<-[:CAPTURED_BY]-(i:ImageFrame)-[:LOCATED_AT]->(l:Location)
OPTIONAL MATCH q = (i)-[:USED_SENSOR]->(:Sensor)-[:ONBOARD]->(s)
RETURN p, q;

// ---- V2 (G2). Multi-hop: basin <- location <- image <- detected object (Mekong basin) ----
MATCH p = (b:RiverBasin {code: 'MKG'})<-[:BELONGS_TO]-(l:Location)<-[:LOCATED_AT]-(i:ImageFrame)<-[:DETECTED_IN]-(o:DetectedObject)
RETURN p;

// ---- V3 (G3). FLAGSHIP: rapid deforestation near river basins in Q3 ----
MATCH p = (b:RiverBasin)<-[:BELONGS_TO]-(l:Location)<-[:LOCATED_AT]-(i:ImageFrame)
          <-[:DETECTED_IN]-(o:DetectedObject)-[e:EVOLVED_FROM]->(prev:DetectedObject)
WHERE o.type_name = 'Tree Coverage' AND e.change_type = 'DEFORESTATION'
  AND i.capture_time >= datetime('2026-07-01T00:00:00')
  AND i.capture_time <  datetime('2026-10-01T00:00:00')
  AND point.distance(l.point, b.river_point) <= 300000
RETURN p;

// ---- V4 (G4). Lineage chain of one forest patch (Kisangani), with its images ----
MATCH chain = (latest:DetectedObject)-[:EVOLVED_FROM*]->(first:DetectedObject)
WHERE NOT EXISTS { ()-[:EVOLVED_FROM]->(latest) }
  AND NOT EXISTS { (first)-[:EVOLVED_FROM]->() }
MATCH (latest)-[:DETECTED_IN]->(:ImageFrame)-[:LOCATED_AT]->(l:Location {name: 'Kisangani Rainforest'})
UNWIND nodes(chain) AS n
MATCH ctx = (n)-[:DETECTED_IN]->(:ImageFrame)-[:LOCATED_AT]->(l)
RETURN chain, ctx;

// ---- V5 (G5). Every forest patch's full history, attached to its location ----
MATCH chain = (latest:DetectedObject)-[:EVOLVED_FROM*]->(first:DetectedObject)
WHERE NOT EXISTS { ()-[:EVOLVED_FROM]->(latest) }
  AND NOT EXISTS { (first)-[:EVOLVED_FROM]->() }
MATCH ctx = (latest)-[:DETECTED_IN]->(:ImageFrame)-[:LOCATED_AT]->(:Location)
RETURN chain, ctx;

// ---- V6 (G6). Vector search: objects that "look like deforestation", and where they are ----
MATCH (q:QueryLog {query_id: 1})
CALL db.index.vector.queryNodes('object_embedding_idx', 8, q.embedding) YIELD node AS o, score
MATCH p = (o)-[:DETECTED_IN]->(:ImageFrame)-[:LOCATED_AT]->(:Location)
RETURN p;

// ---- V7 (G7). GraphRAG: question -> top-5 hybrid-ranked evidence -> image -> location -> basin ----
MATCH (q:QueryLog {query_id: 1})
CALL db.index.vector.queryNodes('object_embedding_idx', 40, q.embedding) YIELD node AS o, score AS semantic
MATCH (o)-[:DETECTED_IN]->(i:ImageFrame)-[:LOCATED_AT]->(l:Location)
CALL (l) {
  MATCH (b:RiverBasin)
  RETURN min(point.distance(l.point, b.river_point)) / 1000.0 AS km
}
WITH q, o, i, l,
     q.alpha * semantic + q.beta * exp(-km / 200.0)
     + q.gamma * CASE WHEN i.capture_time >= datetime('2026-07-01T00:00:00')
                       AND i.capture_time <  datetime('2026-10-01T00:00:00') THEN 1.0 ELSE 0.3 END AS rs
ORDER BY rs DESC LIMIT 5
MATCH p = (o)-[:DETECTED_IN]->(i)-[:LOCATED_AT]->(l)
OPTIONAL MATCH bp = (l)-[:BELONGS_TO]->(:RiverBasin)
OPTIONAL MATCH ep = (q)-[:USED_AS_EVIDENCE]->(o)
OPTIONAL MATCH up = (q)-[:ASKED_BY]->(:AppUser)
RETURN q, p, bp, ep, up;

// ---- V8 (G8). Trust filter: confident, clear-sky deforestation changes (the 4 alerts) ----
MATCH p = (l:Location)<-[:LOCATED_AT]-(i1:ImageFrame)<-[:DETECTED_IN]-(o:DetectedObject)
          -[e:EVOLVED_FROM]->(prev:DetectedObject)-[:DETECTED_IN]->(i2:ImageFrame)
WHERE e.change_pct >= 10 AND o.confidence >= 0.7 AND prev.confidence >= 0.7
  AND i1.cloud_cover <= 40 AND i2.cloud_cover <= 40
RETURN p;

// ---- V9 (G9). Spatial: locations within 1500 km of Phnom Penh, with their basins ----
WITH point({latitude: 11.5564, longitude: 104.9282}) AS centre
MATCH (l:Location)
WHERE point.distance(l.point, centre) <= 1500000
OPTIONAL MATCH p = (l)-[:BELONGS_TO]->(:RiverBasin)
RETURN l, p;

// ---- V10 (G10). Spatial bounding box: South-East Asia locations, basins and images ----
MATCH (l:Location)
WHERE point.withinBBox(l.point, point({latitude: 5, longitude: 95}), point({latitude: 25, longitude: 120}))
OPTIONAL MATCH bp = (l)-[:BELONGS_TO]->(:RiverBasin)
OPTIONAL MATCH ip = (l)<-[:LOCATED_AT]-(:ImageFrame)
RETURN l, bp, ip;

// ---- V11 (G11). Worst deforestation patch and the patches most similar to it ----
MATCH (worst:DetectedObject)-[e:EVOLVED_FROM]->(:DetectedObject)
WITH worst, e ORDER BY e.change_pct DESC LIMIT 1
CALL db.index.vector.queryNodes('object_embedding_idx', 6, worst.embedding) YIELD node, score
MATCH p = (node)-[:DETECTED_IN]->(:ImageFrame)-[:LOCATED_AT]->(:Location)
RETURN worst, p;

// ---- V12 (G12). Shortest path from a forest patch to its satellite ----
MATCH (o:DetectedObject {object_id: 41}), (s:Satellite)
MATCH p = shortestPath((o)-[*..4]-(s))
RETURN p
ORDER BY length(p) LIMIT 1;

// ---- V13 (G13). Coverage: every satellite and the images it captured ----
MATCH p = (s:Satellite)<-[:CAPTURED_BY]-(:ImageFrame)
RETURN p;

// ---- V14 (G14). Write demo: add a new observation into Manaus's lineage, view it, then clean up ----
MATCH (last:DetectedObject {type_name: 'Tree Coverage'})-[:DETECTED_IN]->(:ImageFrame)-[:LOCATED_AT]->(:Location {name: 'Manaus Forest Edge'})
WITH last ORDER BY last.area_sq_m ASC LIMIT 1
CREATE (n:DetectedObject {object_id: 9001, type_name: 'Tree Coverage', confidence: 0.9,
                          area_sq_m: last.area_sq_m * 0.8, embedding: last.embedding})
CREATE (n)-[:EVOLVED_FROM {change_type: 'DEFORESTATION', change_pct: 20.0, time_delta_days: 30, rate_pct_per_month: 20.0}]->(last)
WITH n
MATCH p = (n)-[:EVOLVED_FROM*]->(:DetectedObject)
RETURN p;
// cleanup (run right after):
MATCH (n:DetectedObject {object_id: 9001}) DETACH DELETE n;

// ---- V15. Extra: detections by model and type at one image (Rotterdam Port) ----
MATCH (i:ImageFrame)-[:LOCATED_AT]->(:Location {name: 'Rotterdam Port'})
WITH i ORDER BY i.capture_time DESC LIMIT 1
MATCH p = (m:DetectionModel)<-[:DETECTED_BY]-(o:DetectedObject)-[:DETECTED_IN]->(i)
MATCH t = (o)-[:HAS_TYPE]->(:ObjectType)
RETURN p, t;
