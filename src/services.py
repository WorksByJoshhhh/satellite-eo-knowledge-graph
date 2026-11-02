"""
Pure application logic (no database access): input validation, the GraphRAG
hybrid scoring model, and translation of Oracle errors into readable messages.
Kept separate from repo.py so it can be unit-tested without a database.
"""
import json
import math
import re
from datetime import date, datetime

ORBITS = ("SUN-SYNCHRONOUS", "GEOSTATIONARY", "POLAR", "OTHER")
SAT_STATUSES = ("ACTIVE", "DECOMMISSIONED")
ALERT_STATUSES = ("OPEN", "REVIEWING", "RESOLVED")
Q3_FROM, Q3_TO = date(2026, 7, 1), date(2026, 9, 30)

# Concepts the GraphRAG search understands. Each concept's query vector is the
# centroid of labelled examples already in the database (see repo.concept_vectors).
CONCEPTS = {
    "deforestation": "Rapid forest loss (tree patches that shrank >= 10%)",
    "intact_forest": "Stable / intact forest (tree patches with < 3% change)",
    "bare_soil": "Bare soil / land clearing",
    "vessel": "Vessel / ship activity",
    "building": "Buildings / urban footprint",
    "water": "Water bodies",
}


class ValidationError(Exception):
    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = errors


# ------------------------------------------------------------------ helpers
def _text(form, key, label, errors, required=True, max_len=None, min_len=1):
    val = (form.get(key) or "").strip()
    if not val:
        if required:
            errors.append(f"{label} is required.")
        return None
    if len(val) < min_len:
        errors.append(f"{label} must be at least {min_len} characters.")
    if max_len and len(val) > max_len:
        errors.append(f"{label} must be at most {max_len} characters.")
    return val


def _number(form, key, label, errors, lo=None, hi=None, integer=False, required=True, lo_exclusive=False):
    raw = (form.get(key) or "").strip()
    if raw == "":
        if required:
            errors.append(f"{label} is required.")
        return None
    try:
        val = int(raw) if integer else float(raw)
    except ValueError:
        errors.append(f"{label} must be a{'n integer' if integer else ' number'}.")
        return None
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        errors.append(f"{label} must be a finite number.")
        return None
    if lo is not None and (val <= lo if lo_exclusive else val < lo):
        errors.append(f"{label} must be {'greater than' if lo_exclusive else 'at least'} {lo}.")
    if hi is not None and val > hi:
        errors.append(f"{label} must be at most {hi}.")
    return val


def _date(form, key, label, errors, default=None):
    raw = (form.get(key) or "").strip()
    if raw == "":
        if default is None:
            errors.append(f"{label} is required.")
        return default
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        errors.append(f"{label} must be a date in YYYY-MM-DD format.")
        return None


def _choice(form, key, label, options, errors, default=None):
    val = (form.get(key) or "").strip() or default
    if val not in options:
        errors.append(f"{label} must be one of: {', '.join(options)}.")
        return None
    return val


# ------------------------------------------------------------------ validators
def validate_satellite(form, today=None):
    today = today or date.today()
    e = []
    clean = {
        "satellite_name": _text(form, "satellite_name", "Satellite name", e, max_len=60, min_len=2),
        "operator_name": _text(form, "operator_name", "Operator", e, max_len=60, min_len=2),
        "launch_date": _date(form, "launch_date", "Launch date", e),
        "orbit_type": _choice(form, "orbit_type", "Orbit type", ORBITS, e),
        "sat_status": _choice(form, "sat_status", "Status", SAT_STATUSES, e, default="ACTIVE"),
        "revisit_days": _number(form, "revisit_days", "Revisit days", e, lo=1, hi=365, integer=True, required=False),
    }
    if clean["launch_date"]:
        if clean["launch_date"] > today:
            e.append("Launch date cannot be in the future.")
        if clean["launch_date"] < date(1957, 10, 4):
            e.append("Launch date cannot be before Sputnik 1 (1957-10-04).")
    if e:
        raise ValidationError(e)
    return clean


def validate_detection(form):
    e = []
    clean = {
        "image_id": _number(form, "image_id", "Image", e, lo=1, integer=True),
        "type_name": _text(form, "type_name", "Object type", e, max_len=40),
        "model_id": _number(form, "model_id", "Detection model", e, lo=1, integer=True),
        "confidence": _number(form, "confidence", "Confidence", e, lo=0, hi=1),
        "lat": _number(form, "lat", "Latitude", e, lo=-90, hi=90),
        "lon": _number(form, "lon", "Longitude", e, lo=-180, hi=180),
        "area": _number(form, "area", "Area (m²)", e, lo=0, hi=1e10, lo_exclusive=True),
    }
    if e:
        raise ValidationError(e)
    return clean


def validate_thresholds(form):
    e = []
    clean = {
        "min_loss": _number(form, "min_loss", "Minimum loss %", e, lo=0, hi=100),
        "min_conf": _number(form, "min_conf", "Minimum confidence", e, lo=0, hi=1),
        "max_cloud": _number(form, "max_cloud", "Maximum cloud %", e, lo=0, hi=100),
    }
    if e:
        raise ValidationError(e)
    return clean


def validate_alert_update(form):
    e = []
    clean = {
        "alert_status": _choice(form, "alert_status", "Status", ALERT_STATUSES, e),
        "assigned_user_id": _number(form, "assigned_user_id", "Assignee", e, lo=1, integer=True, required=False),
    }
    if e:
        raise ValidationError(e)
    return clean


def validate_finder(form):
    e = []
    clean = {
        "date_from": _date(form, "date_from", "From date", e, default=Q3_FROM),
        "date_to": _date(form, "date_to", "To date", e, default=Q3_TO),
        "max_km": _number({"v": form.get("max_km") or "300"}, "v", "Max distance to river (km)", e, lo=1, hi=5000),
        "min_loss": _number({"v": form.get("min_loss") or "10"}, "v", "Minimum loss %", e, lo=0, hi=100),
    }
    if clean["date_from"] and clean["date_to"] and clean["date_from"] > clean["date_to"]:
        e.append("From date must be on or before To date.")
    if e:
        raise ValidationError(e)
    return clean


def validate_ask(form):
    e = []
    clean = {
        "question": _text(form, "question", "Question", e, max_len=500, min_len=5),
        "concept": _choice(form, "concept", "Concept", tuple(CONCEPTS), e),
        "alpha": _number(form, "alpha", "Semantic weight (α)", e, lo=0, hi=1),
        "beta": _number(form, "beta", "Spatial weight (β)", e, lo=0, hi=1),
        "gamma": _number(form, "gamma", "Temporal weight (γ)", e, lo=0, hi=1),
        "top_k": _number(form, "top_k", "Number of results", e, lo=3, hi=10, integer=True),
        "date_from": _date(form, "date_from", "From date", e, default=Q3_FROM),
        "date_to": _date(form, "date_to", "To date", e, default=Q3_TO),
        "user_id": _number(form, "user_id", "User", e, lo=1, integer=True, required=False),
    }
    w = [clean["alpha"], clean["beta"], clean["gamma"]]
    if None not in w:
        total = sum(w)
        if total <= 0:
            e.append("At least one weight must be greater than 0.")
        else:  # normalise so the retrieval score stays in [0, 1]
            clean["alpha"], clean["beta"], clean["gamma"] = (round(x / total, 4) for x in w)
    if clean["date_from"] and clean["date_to"] and clean["date_from"] > clean["date_to"]:
        e.append("From date must be on or before To date.")
    if e:
        raise ValidationError(e)
    return clean


# ------------------------------------------------------------------ GraphRAG scoring
def parse_vector(text):
    if text is None:
        return None
    if hasattr(text, "read"):        # LOB locator
        text = text.read()
    vec = json.loads(text) if isinstance(text, (str, bytes)) else list(text)
    return [float(x) for x in vec]


def normalise(vec):
    n = math.sqrt(sum(x * x for x in vec))
    return [x / n for x in vec] if n else list(vec)


def centroid(vectors):
    vectors = [v for v in vectors if v]
    if not vectors:
        raise ValueError("No example vectors available for this concept.")
    dim = len(vectors[0])
    return normalise([sum(v[i] for v in vectors) / len(vectors) for i in range(dim)])


def cosine(a, b):
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def spatial_score(km, scale_km=200.0):
    return math.exp(-km / scale_km)


def temporal_score(when, date_from, date_to):
    d = when.date() if isinstance(when, datetime) else when
    return 1.0 if date_from <= d <= date_to else 0.3


def rank_evidence(qvec, candidates, basins, weights, date_from, date_to, top_k):
    """candidates: dicts with embedding, latitude, longitude, capture_time (+ any extra keys).
    Returns the top_k candidates with semantic/spatial/temporal/retrieval scores and rank."""
    a, b, g = weights
    scored = []
    for c in candidates:
        if not c.get("embedding"):
            continue
        km = min(haversine_km(c["latitude"], c["longitude"], bs["river_ref_lat"], bs["river_ref_lon"])
                 for bs in basins) if basins else 10_000.0
        sem = max(0.0, cosine(qvec, c["embedding"]))
        spa = spatial_score(km)
        tmp = temporal_score(c["capture_time"], date_from, date_to)
        rs = a * sem + b * spa + g * tmp
        row = dict(c)
        row.update(semantic_score=round(sem, 4), spatial_score=round(spa, 4),
                   temporal_score=round(tmp, 4), retrieval_score=round(min(rs, 1.0), 4),
                   km_to_river=round(km))
        scored.append(row)
    scored.sort(key=lambda r: (-r["retrieval_score"], r.get("object_id", 0)))
    for i, r in enumerate(scored[:top_k], start=1):
        r["rank_position"] = i
    return scored[:top_k]


def answer_confidence(evidence):
    """Same formula as pkg_eo.fn_query_confidence: retrieval-weighted mean of
    detection confidence x clear-sky fraction."""
    den = sum(r["retrieval_score"] for r in evidence)
    if den == 0:
        return None
    num = sum(r["retrieval_score"] * r["confidence_score"] * (1 - r["cloud_cover"] / 100) for r in evidence)
    return round(num / den, 4)


# ------------------------------------------------------------------ Oracle error translation
CONSTRAINT_MESSAGES = {
    "UQ_SATELLITE_NAME": "A satellite with this name already exists.",
    "UQ_TYPE_NAME": "This object type already exists.",
    "UQ_BASIN_CODE": "This basin code already exists.",
    "UQ_USER_EMAIL": "This email is already registered.",
    "UQ_IMG_PATH": "An image with this file path already exists.",
    "UQ_ALERT_CHANGE": "An alert already exists for this change.",
    "CK_IMG_CLOUD": "Cloud cover must be between 0 and 100%.",
    "CK_OBJ_CONF": "Confidence must be between 0 and 1.",
    "CK_OBJ_AREA": "Area must be greater than 0.",
    "CK_SAT_ORBIT": "Orbit type is not one of the allowed values.",
    "CK_SAT_STATUS": "Status must be ACTIVE or DECOMMISSIONED.",
    "CK_SAT_REVISIT": "Revisit days must be greater than 0.",
    "CK_ALERT_RES": "resolved_at can only be set when the alert is RESOLVED.",
    "FK_OBJ_IMAGE": "The selected image does not exist.",
    "FK_OBJ_MODEL": "The selected detection model does not exist.",
    "FK_OBJ_TYPE": "The selected object type does not exist.",
    "FK_ALERT_USER": "The selected user does not exist.",
    "FK_IMG_SENSOR": "Images still reference this record.",
}


def friendly_db_error(code, message):
    """Turn an Oracle error (code + message) into a sentence a user can act on."""
    message = message or ""
    m = re.search(r"\((?:[A-Z0-9_$#]+\.)?([A-Z0-9_$#]+)\)", message)
    cname = m.group(1) if m else None
    known = CONSTRAINT_MESSAGES.get(cname) if cname else None
    if code == 1:
        return known or f"Duplicate value: this record already exists ({cname or 'unique constraint'})."
    if code == 2290:
        return known or f"Value not allowed ({cname or 'check constraint'})."
    if code == 2291:
        return known or f"Referenced record not found ({cname or 'foreign key'})."
    if code == 2292:
        return "Cannot delete: other records still depend on this one (e.g. sensors or images)." \
            if not known else f"Cannot delete: {known}"
    if code == 1400:
        col = re.search(r'\."([A-Z0-9_]+)"\)', message)
        return f"Required field missing: {col.group(1).lower() if col else 'a mandatory column'}."
    if code in (12899, 1438):
        return "A value is too large for its column."
    if 20000 <= code <= 20999:
        first = message.splitlines()[0]
        return re.sub(r"^ORA-\d{5}:\s*", "", first)
    if code in (12541, 12514, 12505, 1017):
        return "Cannot connect to the database. Check that Oracle XE is running and the .env settings are correct."
    return f"Database error ORA-{code:05d}: {message.splitlines()[0] if message else ''}"
