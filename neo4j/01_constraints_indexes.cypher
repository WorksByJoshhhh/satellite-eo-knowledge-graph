// =====================================================================
// 01_constraints_indexes.cypher   (Neo4j 5.18+; Neo4j Desktop / Docker / Community)
// Run first, in Neo4j Browser. Each statement ends with ;
// =====================================================================

// ---- uniqueness constraints (graph equivalent of primary keys) ----
CREATE CONSTRAINT satellite_pk IF NOT EXISTS FOR (n:Satellite)      REQUIRE n.satellite_id IS UNIQUE;
CREATE CONSTRAINT sensor_pk    IF NOT EXISTS FOR (n:Sensor)         REQUIRE n.sensor_id IS UNIQUE;
CREATE CONSTRAINT basin_pk     IF NOT EXISTS FOR (n:RiverBasin)     REQUIRE n.basin_id IS UNIQUE;
CREATE CONSTRAINT location_pk  IF NOT EXISTS FOR (n:Location)       REQUIRE n.location_id IS UNIQUE;
CREATE CONSTRAINT image_pk     IF NOT EXISTS FOR (n:ImageFrame)     REQUIRE n.image_id IS UNIQUE;
CREATE CONSTRAINT otype_pk     IF NOT EXISTS FOR (n:ObjectType)     REQUIRE n.object_type_id IS UNIQUE;
CREATE CONSTRAINT model_pk     IF NOT EXISTS FOR (n:DetectionModel) REQUIRE n.model_id IS UNIQUE;
CREATE CONSTRAINT object_pk    IF NOT EXISTS FOR (n:DetectedObject) REQUIRE n.object_id IS UNIQUE;
CREATE CONSTRAINT query_pk     IF NOT EXISTS FOR (n:QueryLog)       REQUIRE n.query_id IS UNIQUE;
CREATE CONSTRAINT user_pk      IF NOT EXISTS FOR (n:AppUser)        REQUIRE n.user_id IS UNIQUE;
CREATE CONSTRAINT basin_code_uq IF NOT EXISTS FOR (n:RiverBasin)    REQUIRE n.code IS UNIQUE;

// ---- property indexes ----
CREATE INDEX image_time_idx   IF NOT EXISTS FOR (n:ImageFrame)     ON (n.capture_time);
CREATE INDEX object_type_idx  IF NOT EXISTS FOR (n:DetectedObject) ON (n.type_name);

// ---- spatial (POINT) indexes ----
CREATE POINT INDEX location_point_idx IF NOT EXISTS FOR (n:Location)   ON (n.point);
CREATE POINT INDEX basin_point_idx    IF NOT EXISTS FOR (n:RiverBasin) ON (n.river_point);

// ---- vector indexes (the semantic half of GraphRAG). 8-dim demo vectors; use 512/768 in production ----
CREATE VECTOR INDEX object_embedding_idx IF NOT EXISTS
FOR (n:DetectedObject) ON (n.embedding)
OPTIONS {indexConfig: {`vector.dimensions`: 8, `vector.similarity_function`: 'cosine'}};

CREATE VECTOR INDEX image_embedding_idx IF NOT EXISTS
FOR (n:ImageFrame) ON (n.embedding)
OPTIONS {indexConfig: {`vector.dimensions`: 8, `vector.similarity_function`: 'cosine'}};
