// =====================================================================
// 03_queries.cypher  -- DA3 technology-specific queries (graph + spatial + vector = GraphRAG)
// Run each query separately in Neo4j Browser.
// =====================================================================

// ---- G1. The project's path: Satellite <- Image -> Location, with objects ----
MATCH (s:Satellite)<-[:CAPTURED_BY]-(i:ImageFrame)-[:LOCATED_AT]->(l:Location)
MATCH (o:DetectedObject)-[:DETECTED_IN]->(i)
RETURN s.name AS satellite, l.name AS location, count(o) AS objects, round(avg(i.cloud_cover), 1) AS avg_cloud
ORDER BY objects DESC LIMIT 10;

// ---- G2. Multi-hop: object -> image -> location -> basin (SQL needed 4 joins) ----
MATCH (b:RiverBasin)<-[:BELONGS_TO]-(l:Location)<-[:LOCATED_AT]-(i:ImageFrame)<-[:DETECTED_IN]-(o:DetectedObject)
RETURN b.name AS basin, o.type_name AS type, count(*) AS detections
ORDER BY basin, detections DESC;

// ---- G3. FLAGSHIP: rapid deforestation near river basins in Q3 (graph + spatial + time) ----
MATCH (b:RiverBasin)<-[:BELONGS_TO]-(l:Location)<-[:LOCATED_AT]-(i:ImageFrame)<-[:DETECTED_IN]-(o:DetectedObject)
      -[e:EVOLVED_FROM]->(p:DetectedObject)
WHERE o.type_name = 'Tree Coverage'
  AND e.change_type = 'DEFORESTATION'
  AND i.capture_time >= datetime('2026-07-01T00:00:00')
  AND i.capture_time <  datetime('2026-10-01T00:00:00')
  AND point.distance(l.point, b.river_point) <= 300000          // metres
RETURN l.name AS location, b.name AS basin, date(i.capture_time) AS observed,
       e.change_pct AS loss_pct, e.rate_pct_per_month AS pct_per_month,
       round(point.distance(l.point, b.river_point) / 1000) AS km_to_river
ORDER BY pct_per_month DESC;
// expected: 10 rows (Manaus 2, Kisangani 3, Tonle Sap 3, Bhadrachalam 2) - see expected_results.txt

// ---- G4. Evidence chain: how a forest patch shrank, step by step (explainability) ----
MATCH path = (latest:DetectedObject)-[:EVOLVED_FROM*]->(first:DetectedObject)
WHERE NOT ()-[:EVOLVED_FROM]->(latest) AND NOT (first)-[:EVOLVED_FROM]->()
MATCH (latest)-[:DETECTED_IN]->(:ImageFrame)-[:LOCATED_AT]->(l:Location)
WHERE l.name = 'Kisangani Rainforest'
RETURN [n IN nodes(path) | round(n.area_sq_m / 1000000, 2)] AS area_km2_newest_to_oldest,
       [r IN relationships(path) | r.change_pct] AS loss_pct_per_step,
       reduce(t = 0, r IN relationships(path) | t + r.time_delta_days) AS days_covered;

// ---- G5. Cumulative loss per location along the lineage, ranked ----
MATCH path = (latest:DetectedObject)-[:EVOLVED_FROM*]->(first:DetectedObject)
WHERE NOT ()-[:EVOLVED_FROM]->(latest) AND NOT (first)-[:EVOLVED_FROM]->()
MATCH (latest)-[:DETECTED_IN]->(:ImageFrame)-[:LOCATED_AT]->(l:Location)
RETURN l.name AS location,
       round((first.area_sq_m - latest.area_sq_m) / first.area_sq_m * 100, 1) AS total_loss_pct,
       length(path) AS steps
ORDER BY total_loss_pct DESC;

// ---- G6. Vector search only: "what looks like deforestation?" (query vector stored on QueryLog 1) ----
MATCH (q:QueryLog {query_id: 1})
CALL db.index.vector.queryNodes('object_embedding_idx', 8, q.embedding) YIELD node AS o, score
MATCH (o)-[:DETECTED_IN]->(i:ImageFrame)-[:LOCATED_AT]->(l:Location)
RETURN o.object_id AS object, o.type_name AS type, l.name AS location, date(i.capture_time) AS observed,
       round(score, 4) AS cosine_similarity_normalised
ORDER BY score DESC;
// note: Neo4j normalises cosine to (1+cos)/2, so scores sit in [0,1]

// ---- G7. FULL GraphRAG: semantic x spatial x temporal hybrid score + answer confidence ----
//          retrieval_score = alpha*semantic + beta*spatial + gamma*temporal   (weights live on the QueryLog node)
MATCH (q:QueryLog {query_id: 1})
CALL db.index.vector.queryNodes('object_embedding_idx', 40, q.embedding) YIELD node AS o, score AS semantic
MATCH (o)-[:DETECTED_IN]->(i:ImageFrame)-[:LOCATED_AT]->(l:Location)
CALL (l) {
  MATCH (b:RiverBasin)
  RETURN min(point.distance(l.point, b.river_point)) / 1000.0 AS km_to_nearest_river
}
WITH q, o, i, l, semantic, exp(-km_to_nearest_river / 200.0) AS spatial,
     CASE WHEN i.capture_time >= datetime('2026-07-01T00:00:00')
           AND i.capture_time <  datetime('2026-10-01T00:00:00') THEN 1.0 ELSE 0.3 END AS temporal
WITH q, o, i, l, semantic, spatial, temporal,
     q.alpha * semantic + q.beta * spatial + q.gamma * temporal AS retrieval_score
ORDER BY retrieval_score DESC LIMIT 5
WITH collect({obj: o.object_id, loc: l.name, observed: date(i.capture_time), score: round(retrieval_score, 4),
              trust: o.confidence * (1 - i.cloud_cover / 100.0), w: retrieval_score}) AS top5
RETURN top5,
       round(reduce(n = 0.0, x IN top5 | n + x.w * x.trust) / reduce(d = 0.0, x IN top5 | d + x.w), 4)
         AS answer_confidence;
// answer_confidence uses the same formula as pkg_eo.fn_query_confidence in DA2

// ---- G8. Trust filter: only evidence with clear sky and confident detections ----
MATCH (o:DetectedObject)-[e:EVOLVED_FROM]->(p:DetectedObject)
MATCH (o)-[:DETECTED_IN]->(i1:ImageFrame), (p)-[:DETECTED_IN]->(i2:ImageFrame)
WHERE e.change_pct >= 10 AND o.confidence >= 0.7 AND p.confidence >= 0.7
  AND i1.cloud_cover <= 40 AND i2.cloud_cover <= 40
MATCH (i1)-[:LOCATED_AT]->(l:Location)
RETURN l.name AS location, date(i1.capture_time) AS observed, e.change_pct AS loss_pct,
       CASE WHEN e.change_pct >= 25 THEN 'CRITICAL' WHEN e.change_pct >= 15 THEN 'HIGH' ELSE 'MEDIUM' END AS severity
ORDER BY loss_pct DESC;
// expected: 4 rows, identical to the 4 alerts raised by pkg_eo.raise_alerts

// ---- G9. Spatial: locations within 1500 km of Phnom Penh, nearest first ----
WITH point({latitude: 11.5564, longitude: 104.9282}) AS centre
MATCH (l:Location)
WHERE point.distance(l.point, centre) <= 1500000
RETURN l.name, l.country, round(point.distance(l.point, centre) / 1000) AS km
ORDER BY km;

// ---- G10. Spatial bounding box: everything inside a lat/lon window (South-East Asia) ----
MATCH (l:Location)
WHERE point.withinBBox(l.point, point({latitude: 5, longitude: 95}), point({latitude: 25, longitude: 120}))
RETURN l.name, l.country;

// ---- G11. Similar-patch recommendation: seed vector search from the worst deforestation patch ----
MATCH (o:DetectedObject)-[e:EVOLVED_FROM]->(:DetectedObject)
WITH o, e ORDER BY e.change_pct DESC LIMIT 1
CALL db.index.vector.queryNodes('object_embedding_idx', 6, o.embedding) YIELD node, score
MATCH (node)-[:DETECTED_IN]->(i:ImageFrame)-[:LOCATED_AT]->(l:Location)
RETURN node.object_id AS similar_object, node.type_name AS type, l.name AS location,
       date(i.capture_time) AS observed, round(score, 4) AS similarity
ORDER BY score DESC;

// ---- G12. Graph traversal: shortest path from a patch to its satellite ----
MATCH (o:DetectedObject {object_id: 41}), (s:Satellite)
MATCH p = shortestPath((o)-[*..4]-(s))
RETURN [n IN nodes(p) | coalesce(n.name, n.type_name, toString(n.image_id))] AS hops, length(p) AS length
ORDER BY length LIMIT 1;

// ---- G13. Satellite coverage statistics (aggregation) ----
MATCH (s:Satellite)<-[:CAPTURED_BY]-(i:ImageFrame)
RETURN s.name AS satellite, count(i) AS images, round(avg(i.cloud_cover), 1) AS avg_cloud, round(avg(i.ndvi_mean), 3) AS avg_ndvi
ORDER BY images DESC;

// ---- G14. Data-modification demo: create a new observation + lineage edge, then remove it ----
MATCH (last:DetectedObject {type_name: 'Tree Coverage'})-[:DETECTED_IN]->(i:ImageFrame)-[:LOCATED_AT]->(l:Location {name: 'Manaus Forest Edge'})
WITH last, l ORDER BY last.area_sq_m ASC LIMIT 1
CREATE (n:DetectedObject {object_id: 9001, type_name: 'Tree Coverage', confidence: 0.9,
                          area_sq_m: last.area_sq_m * 0.8, embedding: last.embedding})
CREATE (n)-[:EVOLVED_FROM {change_type: 'DEFORESTATION', change_pct: 20.0, time_delta_days: 30, rate_pct_per_month: 20.0}]->(last)
RETURN n.object_id, n.area_sq_m;
// cleanup:
MATCH (n:DetectedObject {object_id: 9001}) DETACH DELETE n;
