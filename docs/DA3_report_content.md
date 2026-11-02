# DA3 Report Content: Neo4j Graph Extension

**Project:** Satellite Imagery Knowledge Graph (GraphRAG for Earth Observation data)
**Technology chosen:** Neo4j graph database (version 2026.10, Cypher 25), from the "Graph Database" option in the DA3 list

---

## 1. Aim of DA3

DA2 stored the project data in Oracle, with full constraints and PL/SQL. DA3 extends the **same mini-project** with a graph database. The aim is to answer the project's core question in a way that is natural for connected data:

> "Find satellite images showing rapid deforestation near river basins in Q3."

This question needs four kinds of reasoning together:
- **Relationships:** object → image → location → basin
- **Space:** distance to a river
- **Time:** within Q3
- **Meaning:** what deforestation "looks like"

Neo4j supports all four natively, through graph traversal, spatial `point` values with point indexes, datetime values, and vector indexes.

---

## 2. Database Schema / Model (Graph Model)

*(Draw the graph model diagram, `graph_model.png`, here.)*

In a graph database there are no tables. Data is stored as:
- **Nodes:** the entities. Each node has a **label**, for example `:Satellite`.
- **Relationships:** directed, named connections between nodes, for example `CAPTURED_BY`. A relationship can carry its own properties.
- **Properties:** key–value pairs stored on nodes and relationships.

### 2.1 Node labels (10)

| Label | Key property | Other properties |
|---|---|---|
| Satellite | satellite_id | name, operator, launch_date, orbit_type, status, revisit_days |
| Sensor | sensor_id | name, sensor_type, resolution_m, num_bands |
| ImageFrame | image_id | capture_time (datetime), cloud_cover, sun_elevation, file_path, ndvi_mean, ndwi_mean, **embedding** (vector) |
| Location | location_id | name, country, region_type, **point** (spatial) |
| RiverBasin | basin_id | name, code, area_sq_km, **river_point** (spatial) |
| ObjectType | object_type_id | type_name, category |
| DetectionModel | model_id | name, version, architecture |
| DetectedObject | object_id | type_name, confidence, area_sq_m, bbox_center (spatial), attributes_json, **embedding** (vector) |
| QueryLog | query_id | text, submitted_at, alpha, beta, gamma, **embedding** (vector) |
| AppUser | user_id | full_name, email, role |

### 2.2 Relationship types (11)

| Relationship | From → To | Properties |
|---|---|---|
| ONBOARD | Sensor → Satellite | none |
| CAPTURED_BY | ImageFrame → Satellite | none |
| USED_SENSOR | ImageFrame → Sensor | none |
| LOCATED_AT | ImageFrame → Location | none |
| BELONGS_TO | Location → RiverBasin | none (optional) |
| DETECTED_IN | DetectedObject → ImageFrame | none |
| HAS_TYPE | DetectedObject → ObjectType | none |
| DETECTED_BY | DetectedObject → DetectionModel | none |
| **EVOLVED_FROM** | DetectedObject → DetectedObject (newer → older) | change_type, change_pct, time_delta_days, rate_pct_per_month |
| **USED_AS_EVIDENCE** | QueryLog → DetectedObject | semantic_score, spatial_score, temporal_score, retrieval_score, rank_position |
| ASKED_BY | QueryLog → AppUser | none |

### 2.3 Mapping from the relational model (DA2) to the graph model (DA3)

| Relational design (Oracle) | Graph design (Neo4j) | Reason |
|---|---|---|
| 10 entity tables | 10 node labels | each row becomes a node |
| Foreign key columns (sensor_id, location_id, …) | Relationships | in a graph, the connection itself replaces the foreign key |
| OBJECT_CHANGE (linking table) | `EVOLVED_FROM` relationship with properties | its only job was to connect two objects, so it becomes an edge |
| QUERY_EVIDENCE (associative entity, M:N) | `USED_AS_EVIDENCE` relationship with properties | M:N needs no bridge table in a graph |
| SPECTRAL_INDEX | properties on ImageFrame (ndvi_mean, ndwi_mean) | small 1:1 data, cheaper as properties |
| latitude / longitude columns | `point()` spatial values | enables native distance and bounding-box search |
| embeddings stored as JSON text | float-list properties + **vector index** | enables native similarity search |
| ALERT, AUDIT_LOG | stay in Oracle; G8 computes alerts on the fly | operational records belong to the system of record |
| type_name copied onto DetectedObject | deliberate **denormalisation** | avoids one hop on frequent filters |

### 2.4 Constraints and indexes created

- **11 uniqueness constraints** (the graph equivalent of primary and unique keys), for example `REQUIRE n.object_id IS UNIQUE`
- **2 property indexes:** `ImageFrame.capture_time` and `DetectedObject.type_name`
- **2 spatial point indexes:** `Location.point` and `RiverBasin.river_point`
- **2 vector indexes (cosine, 8 dimensions):** `DetectedObject.embedding` and `ImageFrame.embedding`

---

## 3. Implementation

1. **Software:** Neo4j Desktop 2.3 with a local instance `eo` (Neo4j 2026.10.0, Cypher 25). No plugins were needed.
2. **Sample dataset:** the same synthetic dataset as DA2, exported as 12 CSV files by `generate_data.py`. It contains 354 nodes and about 1,200 relationships: 5 satellites, 6 sensors, 5 basins, 12 locations, 60 images, 247 detected objects, 36 lineage links, 4 queries and 20 evidence links.
3. **Load steps:**
   - copy the CSVs into the instance's `import` folder
   - run `01_constraints_indexes.cypher`
   - run `02_load.cypher`, which uses `LOAD CSV WITH HEADERS` and `MERGE`, so it is safe to re-run
   - run `03_queries.cypher` and `04_crud.cypher`
4. **Verification:** the node counts matched the DA2 row counts exactly (for example, 247 DetectedObject = 247 rows in DETECTED_OBJECT). The flagship query returned the same 10 results in both systems.

Sample load statement (explain this in the report):
```
LOAD CSV WITH HEADERS FROM 'file:///images.csv' AS row
MERGE (i:ImageFrame {image_id: toInteger(row.image_id)})
SET i.capture_time = datetime(row.capture_time), i.cloud_cover = toFloat(row.cloud_cover),
    i.embedding = [x IN split(row.embedding, ';') | toFloat(x)]
WITH i, row
MATCH (loc:Location {location_id: toInteger(row.location_id)})
MERGE (i)-[:LOCATED_AT]->(loc);
```

---

## 4. Demonstration of Database Operations (`04_crud.cypher`)

| Operation | Cypher used | What it shows |
|---|---|---|
| Create | `CREATE` a satellite, sensor and image, with relationships | inserting connected data in one statement |
| Read | `MATCH p = (i)-[]-(n) RETURN p` | pattern matching, shown in the graph view |
| Update | `SET` (change a property, add a label), `REMOVE` (drop a property or label) | the schema-flexible update |
| Upsert | `MERGE … ON CREATE SET … ON MATCH SET` | no duplicates on re-run |
| Integrity | duplicate `satellite_id` is rejected | uniqueness constraint works |
| Delete | plain `DELETE` fails while relationships exist; `DETACH DELETE` succeeds | referential safety |

---

## 5. Technology-Specific Queries (`03_queries.cypher`)

| Query | Neo4j feature | What it answers |
|---|---|---|
| G1, G2 | multi-hop pattern matching | object → image → location → basin in one `MATCH` (SQL needed 4 joins) |
| **G3** | graph + spatial + time | **flagship:** rapid deforestation within 300 km of a basin river in Q3, 10 results |
| G4, G5 | variable-length paths `-[:EVOLVED_FROM*]->` | the full history of a forest patch and its total loss, of any length |
| G6 | vector index `db.index.vector.queryNodes` | "what looks like deforestation", search by meaning |
| **G7** | **GraphRAG:** vector + spatial + temporal hybrid score | ranks evidence by α·semantic + β·spatial + γ·temporal and returns an answer confidence |
| G8 | relationship-property filters | trustworthy changes only, giving the same 4 alerts as Oracle |
| G9, G10 | `point.distance`, `point.withinBBox` | nearest locations, and locations inside a region |
| G11 | vector search seeded from a node | "find patches similar to the worst one" |
| G12 | `shortestPath` | how a detection connects back to its satellite |
| G13 | aggregation | coverage statistics per satellite |
| G14 | write query | adding a new observation into the lineage, then cleaning up |

**Flagship query (G3), to explain in the viva:**
```
MATCH (b:RiverBasin)<-[:BELONGS_TO]-(l:Location)<-[:LOCATED_AT]-(i:ImageFrame)
      <-[:DETECTED_IN]-(o:DetectedObject)-[e:EVOLVED_FROM]->(p:DetectedObject)
WHERE o.type_name = 'Tree Coverage' AND e.change_type = 'DEFORESTATION'
  AND i.capture_time >= datetime('2026-07-01T00:00:00')
  AND i.capture_time <  datetime('2026-10-01T00:00:00')
  AND point.distance(l.point, b.river_point) <= 300000
RETURN l.name, b.name, date(i.capture_time), e.change_pct, e.rate_pct_per_month
ORDER BY e.rate_pct_per_month DESC;
```
- The **path pattern** replaces 6 SQL joins.
- `point.distance` replaces a hand-written haversine function.
- The change data lives **on the relationship** `EVOLVED_FROM`.

---

## 6. The Novelty, Implemented in the Graph

1. **Temporal lineage:** `EVOLVED_FROM` links the same real-world forest patch across dates. `-[:EVOLVED_FROM*]->` walks the whole history, so the answer is an explainable chain rather than a similarity guess.
2. **Tunable hybrid retrieval:** the weights α, β and γ are stored on each QueryLog node. G7 computes `retrieval_score = α·semantic + β·spatial + γ·temporal` live, from the vector index, `point.distance` and `datetime`.
3. **Confidence propagation:** G7 returns `answer_confidence`, the retrieval-weighted mean of (detection confidence × clear-sky fraction). This is the same formula as `pkg_eo.fn_query_confidence` in DA2.

---

## 7. Comparison: why Neo4j suits this application

| Criterion | Oracle SQL (DA2) | Neo4j (DA3) |
|---|---|---|
| Data integrity (CHECK, FK, triggers) | **Strong:** full constraint support and PL/SQL | weaker: uniqueness and existence constraints only |
| Multi-hop relationships (object → image → location → basin) | 4 to 6 joins, getting slower as joins are added | **one path pattern**, cost depends on the path, not the table size |
| History of unknown length (lineage) | `CONNECT BY` / recursive CTE, harder to read | **`-[:EVOLVED_FROM*]->`**, built in |
| Many-to-many (query ↔ evidence) | bridge table needed | **direct relationship** with properties |
| Spatial ("near a river") | hand-written haversine function, no spatial index in XE | **native `point.distance`** + point index |
| Semantic search ("looks like deforestation") | not practical (embeddings are just text) | **native vector index**, cosine similarity |
| Schema changes | `ALTER TABLE` required | flexible: add properties and labels at any time |
| Aggregation and reporting | **Excellent** (GROUP BY, window functions) | good, but less mature |
| Transactions / ACID | yes | yes |

**Conclusion:** this application is about **connections** (satellite → image → place → object → its own history) and about **space and meaning**. A graph database stores connections directly and has native spatial and vector indexes, so the GraphRAG question becomes one readable query. Oracle remains the better **system of record**, because of its strict constraints, PL/SQL, auditing and alerts. The best design is **hybrid**: Oracle keeps the data correct, and Neo4j makes it searchable.

---

## 8. Limitations

- The data is synthetic. The embeddings are 8-dimensional demo vectors, not real CLIP or SatCLIP embeddings (512+ dimensions).
- "Near a river" uses a single reference point per basin, not the full river polygon.
- Neo4j's cosine score is normalised to (1 + cos)/2, so its values differ slightly from the Python/SQL scores.
- The two databases are loaded separately from the same generator. A production system would sync them, for example with change-data-capture.

## 9. Future Work

- Replace the demo vectors with real satellite embeddings, from Sentinel-2 imagery and a model such as SatCLIP.
- Store basin boundaries as polygons.
- Connect an LLM to G7's evidence to generate the natural-language answer (full GraphRAG).
- Keep Oracle and Neo4j in sync automatically.
