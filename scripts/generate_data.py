#!/usr/bin/env python3
"""
Generates ONE consistent synthetic Earth-Observation dataset and writes it as
  - database/sample_data.sql (Oracle INSERT statements)
  - neo4j/import/*.csv       (CSV files for DA3 / Neo4j LOAD CSV)
  - tests/expected_results.txt (what the PL/SQL + queries should produce, for self-checking)

Run:  python scripts/generate_data.py
Deterministic (fixed seed) so SQL and Neo4j always match.
"""
import csv, json, math, os, random
from datetime import date, datetime

random.seed(2026)
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root
SQL_OUT = os.path.join(BASE, "database", "sample_data.sql")
CSV_DIR = os.path.join(BASE, "neo4j", "import")
os.makedirs(os.path.dirname(SQL_OUT), exist_ok=True)
os.makedirs(CSV_DIR, exist_ok=True)
DIM = 8

# ----------------------------------------------------------------- helpers
def unit(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]

def rvec():
    return unit([random.gauss(0, 1) for _ in range(DIM)])

def add(*vs):
    return [sum(c) for c in zip(*vs)]

def scale(v, k):
    return [x * k for x in v]

def cos(a, b):
    return sum(x * y for x, y in zip(a, b)) / (
        math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))

def haversine(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def lit(v):
    if v is None:
        return "NULL"
    if isinstance(v, datetime):
        return f"TIMESTAMP '{v:%Y-%m-%d %H:%M:%S}'"
    if isinstance(v, date):
        return f"DATE '{v:%Y-%m-%d}'"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"

def jv(v):                       # compact JSON for a vector
    return json.dumps([round(x, 4) for x in v], separators=(",", ":"))

sql_lines = []
def emit_inserts(table, cols, rows):
    sql_lines.append(f"-- {table}: {len(rows)} rows")
    for r in rows:
        sql_lines.append(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(lit(x) for x in r)});")
    sql_lines.append("")

def write_csv(name, header, rows):
    with open(os.path.join(CSV_DIR, name), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(["" if x is None else x for x in r])

# ----------------------------------------------------------------- reference data
satellites = [
    (1, "Sentinel-2A", "ESA/Copernicus", date(2015, 6, 23), "SUN-SYNCHRONOUS", "ACTIVE", 10),
    (2, "Sentinel-2B", "ESA/Copernicus", date(2017, 3, 7), "SUN-SYNCHRONOUS", "ACTIVE", 10),
    (3, "Landsat 8", "NASA/USGS", date(2013, 2, 11), "SUN-SYNCHRONOUS", "ACTIVE", 16),
    (4, "Landsat 9", "NASA/USGS", date(2021, 9, 27), "SUN-SYNCHRONOUS", "ACTIVE", 16),
    (5, "Sentinel-1A", "ESA/Copernicus", date(2014, 4, 3), "SUN-SYNCHRONOUS", "ACTIVE", 12),
]
sensors = [  # id, satellite, name, type, resolution, bands
    (1, 1, "MSI", "OPTICAL", 10, 13),
    (2, 2, "MSI", "OPTICAL", 10, 13),
    (3, 3, "OLI", "OPTICAL", 30, 9),
    (4, 4, "OLI-2", "OPTICAL", 30, 9),
    (5, 3, "TIRS", "THERMAL", 100, 2),
    (6, 5, "C-SAR", "SAR", 5, 1),
]
OPTICAL = [1, 2, 3, 4]
sensor_sat = {s[0]: s[1] for s in sensors}

basins = [  # id, name, code, ref_lat, ref_lon, area_km2, desc
    (1, "Amazon Basin", "AMZ", -3.1190, -60.0217, 6150000, "Largest rainforest basin; reference point near Manaus"),
    (2, "Congo Basin", "CNG", 0.5167, 25.1833, 3700000, "Central African rainforest basin; reference point at Kisangani"),
    (3, "Mekong Basin", "MKG", 11.5564, 104.9282, 795000, "Southeast Asian river basin; reference point at Phnom Penh"),
    (4, "Godavari Basin", "GDV", 17.0005, 81.8040, 312812, "Peninsular India basin; reference point at Rajahmundry"),
    (5, "Danube Basin", "DNB", 44.8176, 20.4633, 801093, "European basin; reference point at Belgrade"),
]

locations = [  # id, basin, name, country, lat, lon, region_type
    (1, 1, "Manaus Forest Edge", "Brazil", -2.60, -60.30, "FOREST"),
    (2, 1, "Rondonia Frontier", "Brazil", -8.76, -63.90, "FOREST"),
    (3, 2, "Kisangani Rainforest", "DR Congo", 0.80, 25.00, "FOREST"),
    (4, 2, "Mbandaka Wetland", "DR Congo", 0.05, 18.26, "WETLAND"),
    (5, 3, "Tonle Sap Fringe", "Cambodia", 12.90, 104.10, "FOREST"),
    (6, 3, "Kratie Riverbank", "Cambodia", 12.48, 106.02, "FOREST"),
    (7, 4, "Rajahmundry Delta", "India", 16.95, 82.20, "AGRICULTURE"),
    (8, 4, "Bhadrachalam Forest", "India", 17.67, 80.88, "FOREST"),
    (9, 5, "Belgrade Floodplain", "Serbia", 44.90, 20.40, "FLOODPLAIN"),
    (10, None, "Chennai Coast", "India", 13.08, 80.29, "COASTAL"),
    (11, None, "Rotterdam Port", "Netherlands", 51.95, 4.14, "PORT"),
    (12, None, "Dubai Marina", "UAE", 25.08, 55.14, "URBAN"),
]
loc_by_id = {l[0]: l for l in locations}

object_types = [
    (1, "Tree Coverage", "VEGETATION", "Contiguous tree canopy patch"),
    (2, "Building", "URBAN", "Built-up structure footprint"),
    (3, "Vessel", "MARITIME", "Ship or boat on water"),
    (4, "Road Network", "INFRASTRUCTURE", "Paved or unpaved road segment"),
    (5, "Water Body", "HYDROLOGY", "River, lake or reservoir surface"),
    (6, "Cropland", "AGRICULTURE", "Cultivated agricultural parcel"),
    (7, "Bare Soil", "LAND_COVER", "Exposed soil, often after clearing"),
    (8, "Wildfire Scar", "DISTURBANCE", "Burned area"),
]
models = [
    (1, "ForestNet", "1.2", "U-Net", "Hansen Global Forest Change", date(2025, 3, 1)),
    (2, "YOLO-EO", "8.0", "YOLOv8-OBB", "xView + DOTA", date(2025, 8, 15)),
    (3, "NDVI-Threshold", "2.0", "Rule-based", "Sentinel-2 L2A indices", date(2024, 11, 20)),
]
MODEL_BY_TYPE = {1: 1, 2: 2, 3: 2, 4: 2, 5: 3, 6: 3, 7: 3, 8: 1}
AREA_RANGE = {2: (300, 6000), 3: (800, 9000), 4: (2e4, 2e5), 5: (1e5, 3e6), 6: (5e5, 4e6), 8: (2e5, 9e5)}

users = [
    (1, "Aarav Menon", "aarav.menon@eo-lab.example", "ANALYST"),
    (2, "Priya Nair", "priya.nair@eo-lab.example", "ANALYST"),
    (3, "Dev Kapoor", "dev.kapoor@eo-lab.example", "ADMIN"),
    (4, "Sana Iqbal", "sana.iqbal@eo-lab.example", "VIEWER"),
]

CAPTURE_DATES = [date(2026, 4, 10), date(2026, 6, 18), date(2026, 7, 22), date(2026, 8, 27), date(2026, 9, 29)]
def is_q3(d):
    return date(2026, 7, 1) <= d <= date(2026, 9, 30)

# forest dynamics: D = deforesting, S = slow loss, T = stable
profile = {1: "D", 2: "S", 3: "D", 4: "T", 5: "D", 6: "S", 7: "S", 8: "D", 9: "T"}
LOSS = {"D": [0.03, 0.10, 0.14, 0.18], "S": [0.01, 0.02, 0.02, 0.03], "T": [0.0, -0.005, 0.004, -0.003]}
JIT = {"D": 0.008, "S": 0.003, "T": 0.002}

extras = {1: [(5, 1)], 2: [(4, 1), (6, 1)], 3: [(5, 1)], 4: [(5, 2)], 5: [(5, 1), (6, 1)],
          6: [(5, 1), (6, 1)], 7: [(6, 2), (5, 1), (2, 2)], 8: [(4, 1)], 9: [(5, 1), (2, 2)],
          10: [(2, 3), (3, 2), (4, 1)], 11: [(3, 3), (4, 1), (2, 1)], 12: [(2, 3), (3, 2), (4, 1)]}

# embedding prototypes
base = {t[0]: rvec() for t in object_types}
loss_dir = rvec()

# ----------------------------------------------------------------- images
images = []        # dicts
img_id = 0
for loc in locations:
    for k, d in enumerate(CAPTURE_DATES):
        img_id += 1
        sensor = OPTICAL[(loc[0] + k) % 4]
        images.append(dict(
            image_id=img_id, sensor_id=sensor, location_id=loc[0], k=k, d=d,
            capture_time=datetime(d.year, d.month, d.day, 5, 30, 0),
            cloud=round(random.uniform(3, 55), 1), sun=round(random.uniform(35, 75), 1),
            path=f"/archive/loc{loc[0]:02d}/{d:%Y%m%d}_S{sensor}.tif"))
img_lookup = {(i["location_id"], i["k"]): i for i in images}

# ----------------------------------------------------------------- objects
objects = []       # dicts
obj_id = 0

def new_obj(img, type_id, area, conf, emb, attrs, loc):
    global obj_id
    obj_id += 1
    h = max(0.0005, math.sqrt(area) / 111000.0 / 2)
    clat = loc[4] + random.uniform(-0.05, 0.05)
    clon = loc[5] + random.uniform(-0.05, 0.05)
    o = dict(object_id=obj_id, image_id=img["image_id"], type_id=type_id, model_id=MODEL_BY_TYPE[type_id],
             conf=round(conf, 3), bbox=(round(clat - h, 5), round(clon - h, 5), round(clat + h, 5), round(clon + h, 5)),
             area=round(area, 2), emb=unit(emb), attrs=attrs, location_id=loc[0], k=img["k"], d=img["d"],
             cloud=img["cloud"], type_name=object_types[type_id - 1][1])
    objects.append(o)
    return o

def conf_for(low_prob=0.1):
    if random.random() < low_prob:
        return random.uniform(0.55, 0.69)
    return random.uniform(0.72, 0.97)

tree_obj = {}                      # (loc,k) -> object
for loc in locations:
    lid = loc[0]
    prof = profile.get(lid)
    if prof:
        base_area = random.uniform(4.0e6, 9.0e6)
        area = base_area
        soil_area = 0.05 * base_area
        for k in range(len(CAPTURE_DATES)):
            img = img_lookup[(lid, k)]
            if k > 0:
                area = area * (1 - (LOSS[prof][k - 1] + random.uniform(-JIT[prof], JIT[prof])))
            cum = 1 - area / base_area
            emb = add(base[1], scale(loss_dir, 6 * max(cum, 0)), [random.gauss(0, 0.04) for _ in range(DIM)])
            t = new_obj(img, 1, area, conf_for(), emb,
                        {"canopy_density": round(random.uniform(0.6, 0.95), 2), "dominant_class": "broadleaf"}, loc)
            tree_obj[(lid, k)] = t
            if prof == "D":
                soil_area = 0.05 * base_area + (base_area - area)
                semb = add(base[7], scale(loss_dir, 0.5), [random.gauss(0, 0.05) for _ in range(DIM)])
                new_obj(img, 7, soil_area, conf_for(0.05), semb, {"cause": "clearing", "recent": True}, loc)
    for k in range(len(CAPTURE_DATES)):
        img = img_lookup[(lid, k)]
        for type_id, cnt in extras.get(lid, []):
            for _ in range(cnt):
                lo, hi = AREA_RANGE[type_id]
                a = random.uniform(lo, hi)
                if type_id == 3:
                    attrs = {"length_m": round(random.uniform(30, 300), 1),
                             "vessel_class": random.choice(["cargo", "tanker", "fishing", "passenger"])}
                elif type_id == 2:
                    attrs = {"floors": random.randint(1, 30), "material": random.choice(["concrete", "brick", "steel"])}
                elif type_id == 5:
                    attrs = {"turbidity": round(random.uniform(0.1, 0.9), 2)}
                elif type_id == 6:
                    attrs = {"crop": random.choice(["rice", "maize", "soy", "cassava"])}
                else:
                    attrs = {"surface": random.choice(["paved", "unpaved"])}
                emb = add(base[type_id], [random.gauss(0, 0.15) for _ in range(DIM)])
                new_obj(img, type_id, a, conf_for(), emb, attrs, loc_by_id[lid])
        if lid == 2 and k >= 3:
            emb = add(base[8], [random.gauss(0, 0.1) for _ in range(DIM)])
            new_obj(img, 8, random.uniform(*AREA_RANGE[8]), conf_for(0.05), emb, {"severity": "moderate"}, loc)

# image embeddings = normalised mean of the objects detected in that image
by_img = {}
for o in objects:
    by_img.setdefault(o["image_id"], []).append(o)
for im in images:
    im["emb"] = unit(add(*[o["emb"] for o in by_img[im["image_id"]]]))

# spectral indices
tree_base = {}
for lid in profile:
    tree_base[lid] = tree_obj[(lid, 0)]["area"]
spectral = []
si_id = 0
for im in images:
    lid = im["location_id"]
    if lid in profile:
        ratio = tree_obj[(lid, im["k"])]["area"] / tree_base[lid]
        ndvi = 0.25 + 0.55 * ratio + random.uniform(-0.03, 0.03)
    else:
        ndvi = random.uniform(0.08, 0.30)
    ndwi = random.uniform(-0.3, 0.4)
    for name, mean in (("NDVI", ndvi), ("NDWI", ndwi)):
        mean = max(-0.84, min(0.84, mean))
        si_id += 1
        spectral.append((si_id, im["image_id"], name, round(mean, 4), round(mean - 0.15, 4), round(mean + 0.15, 4)))

# ----------------------------------------------------------------- changes (mirror of pkg_eo.compute_changes)
def classify(pct):
    if pct >= 10: return "DEFORESTATION"
    if pct <= -10: return "REGROWTH"
    if pct >= 3: return "GRADUAL_LOSS"
    return "STABLE"

changes = []
chg_id = 0
for lid in profile:
    for k in range(1, len(CAPTURE_DATES)):
        p, c = tree_obj[(lid, k - 1)], tree_obj[(lid, k)]
        pct = round((p["area"] - c["area"]) / p["area"] * 100, 2)
        days = (c["d"] - p["d"]).days
        rate = round(pct / days * 30, 2)
        chg_id += 1
        changes.append(dict(change_id=chg_id, prev=p, curr=c, pct=pct, days=days, rate=rate, ctype=classify(pct)))

# ----------------------------------------------------------------- queries + evidence
Q = [
    (1, 1, "Find images showing rapid deforestation near river basins in Q3",
     unit(add(base[1], scale(loss_dir, 2.4))), (0.5, 0.3, 0.2)),
    (2, 2, "Show stable intact forest along the Danube floodplain", unit(base[1]), (0.6, 0.3, 0.1)),
    (3, 3, "Locate vessel activity at major ports", unit(base[3]), (0.6, 0.1, 0.3)),
    (4, 1, "Detect bare soil expansion in recent months", unit(add(base[7], scale(loss_dir, 0.5))), (0.5, 0.2, 0.3)),
]
def nearest_basin_km(lat, lon):
    return min(haversine(lat, lon, b[3], b[4]) for b in basins)

evidence = []
ev_id = 0
for qid, uid, text, qemb, (a, b_, g) in Q:
    scored = []
    for o in objects:
        loc = loc_by_id[o["location_id"]]
        sem = max(0.0, cos(qemb, o["emb"]))
        spa = math.exp(-nearest_basin_km(loc[4], loc[5]) / 200.0)
        tmp = 1.0 if is_q3(o["d"]) else 0.3
        scored.append((a * sem + b_ * spa + g * tmp, sem, spa, tmp, o))
    scored.sort(key=lambda x: -x[0])
    for rank, (rs, sem, spa, tmp, o) in enumerate(scored[:5], start=1):
        ev_id += 1
        evidence.append((ev_id, qid, o["object_id"], round(sem, 4), round(spa, 4), round(tmp, 4), round(rs, 4), rank, o))

def agg_conf(qid):
    rows = [e for e in evidence if e[1] == qid]
    num = sum(e[6] * e[8]["conf"] * (1 - e[8]["cloud"] / 100) for e in rows)
    den = sum(e[6] for e in rows)
    return round(num / den, 4)

# ----------------------------------------------------------------- integrity checks
assert len({o["object_id"] for o in objects}) == len(objects)
img_ids = {i["image_id"] for i in images}
assert all(o["image_id"] in img_ids for o in objects)
assert all(o["bbox"][0] < o["bbox"][2] and o["bbox"][1] < o["bbox"][3] for o in objects)
assert all(0 <= o["conf"] <= 1 for o in objects)
assert all(e[1] in {q[0] for q in Q} for e in evidence)
assert max(o["object_id"] for o in objects) < 10000 and len(images) < 10000
assert all(-0.84 <= s[3] <= 0.84 for s in spectral)

# ----------------------------------------------------------------- write SQL
sql_lines.append("-- Generated by generate_data.py (deterministic).  Run AFTER schema.sql")
sql_lines.append("SET DEFINE OFF;\n")
emit_inserts("satellite", ["satellite_id", "satellite_name", "operator_name", "launch_date", "orbit_type", "sat_status", "revisit_days"], satellites)
emit_inserts("sensor", ["sensor_id", "satellite_id", "sensor_name", "sensor_type", "spatial_resolution_m", "num_bands"], sensors)
emit_inserts("river_basin", ["basin_id", "basin_name", "basin_code", "river_ref_lat", "river_ref_lon", "area_sq_km", "basin_description"], basins)
emit_inserts("location", ["location_id", "basin_id", "location_name", "country", "latitude", "longitude", "region_type"],
             [(l[0], l[1], l[2], l[3], l[4], l[5], l[6]) for l in locations])
emit_inserts("object_type", ["object_type_id", "type_name", "category", "object_type_description"], object_types)
emit_inserts("detection_model", ["model_id", "model_name", "model_version", "architecture", "trained_on", "released_date"], models)
emit_inserts("app_user", ["user_id", "full_name", "email", "user_role"], users)
emit_inserts("image_frame", ["image_id", "sensor_id", "location_id", "capture_time", "cloud_cover", "sun_elevation", "file_path", "image_embedding"],
             [(i["image_id"], i["sensor_id"], i["location_id"], i["capture_time"], i["cloud"], i["sun"], i["path"], jv(i["emb"])) for i in images])
emit_inserts("detected_object",
             ["object_id", "image_id", "object_type_id", "model_id", "confidence_score", "bbox_min_lat", "bbox_min_lon",
              "bbox_max_lat", "bbox_max_lon", "area_sq_m", "object_embedding", "attributes_json"],
             [(o["object_id"], o["image_id"], o["type_id"], o["model_id"], o["conf"], *o["bbox"], o["area"], jv(o["emb"]),
               json.dumps(o["attrs"], separators=(",", ":"))) for o in objects])
emit_inserts("spectral_index", ["index_id", "image_id", "index_name", "mean_value", "min_value", "max_value"], spectral)
emit_inserts("query_log", ["query_id", "user_id", "query_text", "query_embedding", "submitted_at", "retrieval_weights_json"],
             [(q[0], q[1], q[2], jv(q[3]), datetime(2026, 10, 5 + q[0], 11, 0, 0),
               json.dumps({"alpha": q[4][0], "beta": q[4][1], "gamma": q[4][2]}, separators=(",", ":"))) for q in Q])
emit_inserts("query_evidence", ["evidence_id", "query_id", "object_id", "semantic_score", "spatial_score", "temporal_score", "retrieval_score", "rank_position"],
             [e[:8] for e in evidence])
sql_lines.append("COMMIT;")
sql_lines.append("-- NOTE: object_change, alert, audit_log and query_log.aggregated_confidence are filled by PL/SQL (see run_procedures.sql)")
with open(SQL_OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(sql_lines) + "\n")

# ----------------------------------------------------------------- write CSVs for Neo4j
write_csv("satellites.csv", ["satellite_id", "name", "operator", "launch_date", "orbit_type", "status", "revisit_days"],
          [(s[0], s[1], s[2], s[3].isoformat(), s[4], s[5], s[6]) for s in satellites])
write_csv("sensors.csv", ["sensor_id", "satellite_id", "name", "sensor_type", "resolution_m", "num_bands"], sensors)
write_csv("basins.csv", ["basin_id", "name", "code", "river_ref_lat", "river_ref_lon", "area_sq_km"],
          [(b[0], b[1], b[2], b[3], b[4], b[5]) for b in basins])
write_csv("locations.csv", ["location_id", "basin_id", "name", "country", "lat", "lon", "region_type"], locations)
ndvi_of = {s[1]: s[3] for s in spectral if s[2] == "NDVI"}
ndwi_of = {s[1]: s[3] for s in spectral if s[2] == "NDWI"}
write_csv("images.csv", ["image_id", "sensor_id", "satellite_id", "location_id", "capture_time", "cloud_cover", "sun_elevation",
                         "file_path", "ndvi_mean", "ndwi_mean", "embedding"],
          [(i["image_id"], i["sensor_id"], sensor_sat[i["sensor_id"]], i["location_id"], i["capture_time"].isoformat(), i["cloud"],
            i["sun"], i["path"], ndvi_of[i["image_id"]], ndwi_of[i["image_id"]], ";".join(f"{x:.4f}" for x in i["emb"])) for i in images])
write_csv("object_types.csv", ["object_type_id", "type_name", "category"], [(t[0], t[1], t[2]) for t in object_types])
write_csv("models.csv", ["model_id", "name", "version", "architecture"], [(m[0], m[1], m[2], m[3]) for m in models])
write_csv("objects.csv", ["object_id", "image_id", "object_type_id", "type_name", "model_id", "confidence", "area_sq_m",
                          "bbox_min_lat", "bbox_min_lon", "bbox_max_lat", "bbox_max_lon", "embedding", "attributes_json"],
          [(o["object_id"], o["image_id"], o["type_id"], o["type_name"], o["model_id"], o["conf"], o["area"], *o["bbox"],
            ";".join(f"{x:.4f}" for x in o["emb"]), json.dumps(o["attrs"], separators=(",", ":"))) for o in objects])
write_csv("changes.csv", ["change_id", "prev_object_id", "curr_object_id", "change_type", "change_pct", "time_delta_days", "rate_pct_per_month"],
          [(c["change_id"], c["prev"]["object_id"], c["curr"]["object_id"], c["ctype"], c["pct"], c["days"], c["rate"]) for c in changes])
write_csv("users.csv", ["user_id", "full_name", "email", "role"], users)
write_csv("queries.csv", ["query_id", "user_id", "text", "submitted_at", "alpha", "beta", "gamma", "embedding"],
          [(q[0], q[1], q[2], datetime(2026, 10, 5 + q[0], 11, 0, 0).isoformat(), *q[4], ";".join(f"{x:.4f}" for x in q[3])) for q in Q])
write_csv("evidence.csv", ["evidence_id", "query_id", "object_id", "semantic_score", "spatial_score", "temporal_score", "retrieval_score", "rank_position"],
          [e[:8] for e in evidence])

# ----------------------------------------------------------------- expected results
alerts = [c for c in changes if c["pct"] >= 10 and c["prev"]["conf"] >= 0.7 and c["curr"]["conf"] >= 0.7
          and c["prev"]["cloud"] <= 40 and c["curr"]["cloud"] <= 40]
sev = lambda p: "CRITICAL" if p >= 25 else ("HIGH" if p >= 15 else "MEDIUM")
lines = []
lines.append("ROW COUNTS (for DA2 self-check)")
for name, n in [("satellite", len(satellites)), ("sensor", len(sensors)), ("river_basin", len(basins)), ("location", len(locations)),
                ("object_type", len(object_types)), ("detection_model", len(models)), ("app_user", len(users)),
                ("image_frame", len(images)), ("detected_object", len(objects)), ("spectral_index", len(spectral)),
                ("query_log", len(Q)), ("query_evidence", len(evidence))]:
    lines.append(f"  {name:16s} {n}")
lines.append(f"  object_change (after pkg_eo.compute_changes)  {len(changes)}")
lines.append(f"  alert (after pkg_eo.raise_alerts, defaults)   {len(alerts)}")
lines.append("")
lines.append("DEFORESTATION changes (change_pct >= 10):")
for c in changes:
    if c["ctype"] == "DEFORESTATION":
        lines.append(f"  {loc_by_id[c['curr']['location_id']][2]:24s} -> {c['curr']['d']}  loss {c['pct']:6.2f}%  "
                     f"{c['days']}d  {c['rate']:5.2f}%/month  conf {c['prev']['conf']}/{c['curr']['conf']}  cloud {c['prev']['cloud']}/{c['curr']['cloud']}")
lines.append("")
lines.append("ALERTS expected (conf>=0.7 both, cloud<=40 both, loss>=10):")
for c in alerts:
    lines.append(f"  {loc_by_id[c['curr']['location_id']][2]:24s} -> {c['curr']['d']}  {sev(c['pct'])}  loss {c['pct']}%")
lines.append("")
lines.append("Q3 DEFORESTATION within 300 km of a basin river reference point (flagship query):")
for c in changes:
    if c["ctype"] == "DEFORESTATION" and is_q3(c["curr"]["d"]):
        loc = loc_by_id[c["curr"]["location_id"]]
        b = [x for x in basins if x[0] == loc[1]]
        if b and haversine(loc[4], loc[5], b[0][3], b[0][4]) <= 300:
            lines.append(f"  {loc[2]:24s} {c['curr']['d']}  loss {c['pct']}%  dist {haversine(loc[4], loc[5], b[0][3], b[0][4]):.0f} km")
lines.append("")
lines.append("Expected aggregated_confidence per query (pkg_eo.fn_query_confidence):")
for q in Q:
    lines.append(f"  query {q[0]}: {agg_conf(q[0])}   ({q[2]})")
lines.append("")
lines.append("Pre-seeded top evidence for query 1:")
for e in evidence:
    if e[1] == 1:
        lines.append(f"  rank {e[7]}  object {e[2]:4d}  {e[8]['type_name']:14s} {loc_by_id[e[8]['location_id']][2]:22s} {e[8]['d']}  score {e[6]}")
with open(os.path.join(BASE, "tests", "expected_results.txt"), "w") as f:
    f.write("\n".join(lines) + "\n")
print("\n".join(lines))
print(f"\nWrote {SQL_OUT} and CSVs in {CSV_DIR}")
