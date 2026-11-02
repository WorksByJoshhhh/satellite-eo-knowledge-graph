"""
EO Knowledge Graph - web application (Flask + Oracle).

Features (end-to-end, each reads AND/OR writes the Oracle database):
  1. Dashboard ............ live KPIs, forest-loss ranking, open alerts
  2. Image catalogue ...... filter images; image detail with detected objects and spectral indices
  3. Location profile ..... tree-cover timeline (chart), EVOLVED_FROM lineage, alerts
  4. Deforestation finder . "rapid deforestation near river basins" with map + trust score
  5. Change analysis ...... runs PL/SQL lineage builder + alert raiser; assign / resolve alerts
  6. Ask (GraphRAG) ....... hybrid semantic x spatial x temporal retrieval, stored with answer confidence
  7. Add detection ........ validated insert through pkg_eo.add_detection
  8. Satellites ........... full CRUD with validation and constraint-error handling
  9. Audit log ............ rows written automatically by database triggers
Run:  cd src && python app.py   (see README for setup)
"""
import json
import os
from datetime import date, datetime
from decimal import Decimal

from flask import Flask, abort, flash, redirect, render_template, request, url_for

import config
import db
import repo
import services
from services import CONCEPTS, ORBITS, SAT_STATUSES, ALERT_STATUSES, ValidationError


def _plain(v):
    """Convert Decimal numbers (possible inside driver-parsed JSON) to float, recursively."""
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    return v


def as_json(v):
    """JSON columns can arrive as text OR already parsed: python-oracledb returns a dict/list
    for columns that have an IS JSON check constraint. Accept both."""
    if v is None or v == "":
        return {}
    if isinstance(v, (dict, list)):
        return _plain(v)
    if hasattr(v, "read"):          # a LOB object, if LOBs are ever fetched as locators
        v = v.read()
    try:
        return _plain(json.loads(v))
    except (TypeError, ValueError):
        return {}


def create_app(init_db=True):
    app = Flask(__name__)
    app.config["SECRET_KEY"] = config.SECRET_KEY
    if init_db and config.DB_USER:
        try:
            db.init_pool(config.DB_USER, config.DB_PASSWORD, config.DB_DSN)
        except Exception as exc:  # app still starts; pages show a clear message
            app.logger.error("Could not create Oracle pool: %s", exc)

    # ---------------- template helpers ----------------
    @app.template_filter("dt")
    def fmt_dt(v, fmt="%d %b %Y"):
        if v is None:
            return "—"
        return v.strftime(fmt) if isinstance(v, (datetime, date)) else str(v)

    @app.template_filter("num")
    def fmt_num(v, digits=0):
        if v is None:
            return "—"
        try:
            return f"{float(v):,.{digits}f}"
        except (TypeError, ValueError):
            return str(v)

    @app.template_filter("pct")
    def fmt_pct(v, digits=1):
        return "—" if v is None else f"{float(v):.{digits}f}%"

    @app.template_filter("loss")
    def fmt_loss(v, digits=1):
        """Forest-loss %: a negative loss is shown as a gain (e.g. -0.2 -> '0.2% gain')."""
        if v is None:
            return "—"
        v = float(v)
        return f"{-v:.{digits}f}% gain" if v < 0 else f"{v:.{digits}f}%"

    @app.template_filter("fromjson")
    def fromjson(v):
        return as_json(v)

    @app.context_processor
    def inject():
        return {"nav": [
            ("dashboard", "Dashboard", {"dashboard"}),
            ("images", "Images", {"images", "image_detail"}),
            ("locations", "Locations", {"locations", "location_detail"}),
            ("finder", "Deforestation finder", {"finder"}),
            ("ask", "Ask (GraphRAG)", {"ask", "ask_result"}),
            ("alerts", "Alerts", {"alerts"}),
            ("new_detection", "Add detection", {"new_detection"}),
            ("satellites", "Satellites", {"satellites", "new_satellite", "edit_satellite"}),
            ("audit", "Audit log", {"audit"})],
            "today": date.today()}

    @app.errorhandler(db.DBError)
    def handle_db_error(exc):
        return render_template("error.html", message=exc.friendly, raw=exc.raw), 500

    @app.errorhandler(404)
    def not_found(_):
        return render_template("error.html", message="Record not found.", raw=None), 404

    def flash_errors(errors):
        for e in errors:
            flash(e, "error")

    # ---------------- 1. dashboard ----------------
    @app.route("/")
    def dashboard():
        return render_template("dashboard.html", stats=repo.dashboard_stats(),
                               losses=repo.forest_loss_by_location(limit=6),
                               alerts=[a for a in repo.alerts() if a["alert_status"] != "RESOLVED"][:5],
                               questions=repo.recent_questions(limit=5))

    # ---------------- 2. images ----------------
    @app.route("/images")
    def images():
        f = request.args
        errors = []
        max_cloud = services._number({"v": f.get("max_cloud") or "100"}, "v", "Max cloud %", errors, lo=0, hi=100)
        d_from = services._date(f, "date_from", "From date", errors, default=date(2000, 1, 1))
        d_to = services._date(f, "date_to", "To date", errors, default=date(2100, 1, 1))
        rows = []
        if errors:
            flash_errors(errors)
        else:
            rows = repo.search_images(f.get("satellite") or None, f.get("location") or None, max_cloud, d_from, d_to)
        return render_template("images.html", rows=rows, f=f, satellites=repo.satellites_lookup(),
                               locations=repo.locations_lookup())

    @app.route("/images/<int:image_id>")
    def image_detail(image_id):
        img = repo.get_image(image_id) or abort(404)
        return render_template("image_detail.html", img=img, objects=repo.image_objects(image_id),
                               indices=repo.image_indices(image_id))

    # ---------------- 3. locations ----------------
    @app.route("/locations")
    def locations():
        rows = repo.forest_loss_by_location()
        points = [{"lat": float(r["latitude"]), "lon": float(r["longitude"]), "name": r["location_name"],
                   "loss": None if r["loss_pct"] is None else float(r["loss_pct"])} for r in rows]
        return render_template("locations.html", rows=rows, points=points)

    @app.route("/locations/<int:location_id>")
    def location_detail(location_id):
        loc = repo.get_location(location_id) or abort(404)
        timeline = repo.location_tree_timeline(location_id)
        chart = {"labels": [r["capture_time"].strftime("%d %b") for r in timeline],
                 "area": [round(float(r["area_sq_m"]) / 1e6, 3) for r in timeline],
                 "ndvi": [float(r["ndvi"]) if r["ndvi"] is not None else None for r in timeline]}
        return render_template("location_detail.html", loc=loc, timeline=timeline, chart=chart,
                               changes=repo.location_changes(location_id),
                               images=repo.location_images(location_id))

    # ---------------- 4. deforestation finder ----------------
    @app.route("/finder")
    def finder():
        try:
            p = services.validate_finder(request.args)
        except ValidationError as ve:
            flash_errors(ve.errors)
            p = services.validate_finder({})
        rows = repo.deforestation(p["date_from"], p["date_to"], p["max_km"], p["min_loss"])
        points = [{"lat": float(r["latitude"]), "lon": float(r["longitude"]), "name": r["location_name"],
                   "loss": float(r["change_pct"]), "date": r["curr_time"].strftime("%d %b %Y")} for r in rows]
        return render_template("finder.html", rows=rows, p=p, points=points)

    # ---------------- 5. alerts / change analysis ----------------
    @app.route("/alerts")
    def alerts():
        return render_template("alerts.html", rows=repo.alerts(), users=repo.users(), statuses=ALERT_STATUSES,
                               output=request.args.getlist("out"))

    @app.route("/alerts/run", methods=["POST"])
    def run_analysis():
        try:
            t = services.validate_thresholds(request.form)
        except ValidationError as ve:
            flash_errors(ve.errors)
            return redirect(url_for("alerts"))
        lines = repo.run_analysis(t["min_loss"], t["min_conf"], t["max_cloud"])
        flash("Change analysis completed.", "success")
        return redirect(url_for("alerts", out=lines))

    @app.route("/alerts/<int:alert_id>", methods=["POST"])
    def update_alert(alert_id):
        try:
            c = services.validate_alert_update(request.form)
            repo.update_alert(alert_id, c["alert_status"], c["assigned_user_id"])
            flash(f"Alert {alert_id} updated to {c['alert_status']}.", "success")
        except ValidationError as ve:
            flash_errors(ve.errors)
        except db.DBError as de:
            flash(de.friendly, "error")
        return redirect(url_for("alerts"))

    # ---------------- 6. ask (GraphRAG) ----------------
    @app.route("/ask", methods=["GET", "POST"])
    def ask():
        form = request.form if request.method == "POST" else {
            "question": "Find images showing rapid deforestation near river basins in Q3",
            "concept": "deforestation", "alpha": "0.5", "beta": "0.3", "gamma": "0.2", "top_k": "5",
            "date_from": "2026-07-01", "date_to": "2026-09-30"}
        if request.method == "POST":
            try:
                c = services.validate_ask(request.form)
                qvec = repo.concept_vector(c["concept"])
                weights = (c["alpha"], c["beta"], c["gamma"])
                evidence = services.rank_evidence(qvec, repo.retrieval_candidates(), repo.basins(), weights,
                                                  c["date_from"], c["date_to"], c["top_k"])
                if not evidence:
                    raise ValidationError(["No evidence found."])
                qid, _ = repo.save_question(c["user_id"], c["question"], qvec, weights, evidence)
                return redirect(url_for("ask_result", query_id=qid))
            except ValidationError as ve:
                flash_errors(ve.errors)
            except db.DBError as de:
                flash(de.friendly, "error")
        return render_template("ask.html", form=form, concepts=CONCEPTS, users=repo.users(),
                               recent=repo.recent_questions())

    @app.route("/ask/<int:query_id>")
    def ask_result(query_id):
        q = repo.get_question(query_id) or abort(404)
        ev = repo.question_evidence(query_id)
        points = [{"lat": float(r["latitude"]), "lon": float(r["longitude"]), "name": r["location_name"],
                   "rank": r["rank_position"], "type": r["type_name"]} for r in ev]
        return render_template("ask_result.html", q=q, ev=ev, points=points,
                               weights={"alpha": 0, "beta": 0, "gamma": 0, **as_json(q["retrieval_weights_json"])})

    # ---------------- 7. add detection ----------------
    @app.route("/detections/new", methods=["GET", "POST"])
    def new_detection():
        form = request.form if request.method == "POST" else {"confidence": "0.85", "area": "1000"}
        if request.method == "POST":
            try:
                c = services.validate_detection(request.form)
                new_id = repo.add_detection(c)
                flash(f"Detection {new_id} saved (and written to the audit log by trigger).", "success")
                return redirect(url_for("image_detail", image_id=c["image_id"]))
            except ValidationError as ve:
                flash_errors(ve.errors)
            except db.DBError as de:
                flash(de.friendly, "error")
        return render_template("detection_form.html", form=form, images=repo.images_for_select(),
                               types=repo.object_types(), models=repo.models())

    # ---------------- 8. satellites CRUD ----------------
    @app.route("/satellites")
    def satellites():
        return render_template("satellites.html", rows=repo.list_satellites())

    @app.route("/satellites/new", methods=["GET", "POST"])
    def new_satellite():
        form = request.form if request.method == "POST" else {"sat_status": "ACTIVE", "orbit_type": "SUN-SYNCHRONOUS"}
        if request.method == "POST":
            try:
                c = services.validate_satellite(request.form)
                new_id = repo.insert_satellite(c)
                flash(f"Satellite '{c['satellite_name']}' created (id {new_id}).", "success")
                return redirect(url_for("satellites"))
            except ValidationError as ve:
                flash_errors(ve.errors)
            except db.DBError as de:
                flash(de.friendly, "error")
        return render_template("satellite_form.html", form=form, orbits=ORBITS, statuses=SAT_STATUSES, editing=None)

    @app.route("/satellites/<int:satellite_id>/edit", methods=["GET", "POST"])
    def edit_satellite(satellite_id):
        sat = repo.get_satellite(satellite_id) or abort(404)
        form = request.form if request.method == "POST" else {
            k: (v.strftime("%Y-%m-%d") if isinstance(v, (date, datetime)) else ("" if v is None else str(v)))
            for k, v in sat.items()}
        if request.method == "POST":
            try:
                c = services.validate_satellite(request.form)
                repo.update_satellite(satellite_id, c)
                flash(f"Satellite '{c['satellite_name']}' updated.", "success")
                return redirect(url_for("satellites"))
            except ValidationError as ve:
                flash_errors(ve.errors)
            except db.DBError as de:
                flash(de.friendly, "error")
        return render_template("satellite_form.html", form=form, orbits=ORBITS, statuses=SAT_STATUSES, editing=sat)

    @app.route("/satellites/<int:satellite_id>/delete", methods=["POST"])
    def delete_satellite(satellite_id):
        try:
            repo.delete_satellite(satellite_id)
            flash(f"Satellite {satellite_id} deleted.", "success")
        except ValidationError as ve:
            flash_errors(ve.errors)
        except db.DBError as de:
            flash(de.friendly, "error")
        return redirect(url_for("satellites"))

    # ---------------- 9. audit log ----------------
    @app.route("/audit")
    def audit():
        t = request.args.get("table") or None
        return render_template("audit.html", rows=repo.audit_log(t), table=t)

    @app.route("/health")
    def health():
        try:
            n = db.scalar("SELECT COUNT(*) FROM detected_object")
            return {"status": "ok", "detected_objects": n}
        except db.DBError as de:
            return {"status": "error", "message": de.friendly}, 500

    return app


if __name__ == "__main__":
    create_app().run(debug=os.getenv("FLASK_DEBUG", "1") == "1", port=int(os.getenv("PORT", "5000")))
