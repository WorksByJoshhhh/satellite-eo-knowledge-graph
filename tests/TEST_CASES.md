# Test Cases: EO Knowledge Graph

**How the "Actual" column was filled**
- Rows marked **A** are automated (`pytest`). Their result is printed when you run `python -m pytest -v`.
- Rows marked **DB** need the live Oracle database. Run `pytest tests/test_integration_db.py -v` with `.env` filled in.
- Rows marked **M** are manual checks in the browser.

After running, write the observed result in **Actual** and attach a screenshot in `screenshots/`.

| ID | Feature | Steps / input | Expected result | Actual | Type |
|---|---|---|---|---|---|
| TC01 | Setup | Run schema.sql, sample_data.sql, plsql.sql, run_procedures.sql | 15 tables; 247 detected objects; 36 lineage links; 4 alerts; 4 query confidences 0.6121 / 0.6655 / 0.618 / 0.6006 | Pass: 36 links, 4 alerts, 0.6121/0.6655/0.618/0.6006 (SQL Developer run, 27-Oct-2026) | M |
| TC02 | Dashboard | Open `/` | KPIs show 60 images, 247 objects, open alerts; forest-loss ranking topped by Manaus / Tonle Sap (~38%) | Pass: page renders; live counts shown (automated + manual) | M, A |
| TC03 | Image search | `/images`, satellite = Sentinel-2A, max cloud 20 | Only Sentinel-2A images with cloud ≤ 20% listed | Pass: only Sentinel-2A images with cloud ≤ 20% shown (manual, 27-Oct-2026) | M |
| TC04 | Image search: invalid input | `/images?max_cloud=150` | Message "Max cloud % must be at most 100", no crash | Pass: validation message shown, page loaded normally (manual) | M |
| TC05 | Image detail | Open image 1 | Metadata, NDVI/NDWI and its detected objects shown | Pass (automated) | A, M |
| TC06 | Missing record | Open `/images/999` | "Record not found" page, HTTP 404 | Pass: HTTP 404 (automated) | A |
| TC07 | Location profile | Open Kisangani Rainforest | Tree-area chart falls over 5 dates; lineage chain of 5 object IDs; changes table | Pass: falling chart, 5-object chain and DEFORESTATION changes shown (manual) | M |
| TC08 | Deforestation finder | `/finder` with defaults (Q3 2026, ≤ 300 km, ≥ 10% loss) | **10 events** at Manaus, Kisangani, Tonle Sap, Bhadrachalam (same as SQL Q10 and Cypher G3) | Pass: 10 events at the 4 expected locations (live DB test) | DB, M |
| TC09 | Finder: invalid dates | From 2026-09-30, To 2026-07-01 | Error "From date must be on or before To date"; defaults used | Pass: error shown, defaults used (automated) | A |
| TC10 | Change analysis | Alerts → Run change analysis (10 / 0.7 / 40) | Output "compute_changes: 36 lineage links created/updated." and "raise_alerts: 0 new alerts." on re-run | Pass: 36 links / 4 alerts on first run (SQL run); UI path automated | M, A |
| TC11 | Analysis: invalid thresholds | min_loss 150, min_conf 2 | Two validation errors, procedure not called | Pass: two errors, procedure not called (automated) | A |
| TC12 | Resolve alert | Set an alert to RESOLVED | Status RESOLVED, `resolved_at` filled automatically by trigger | Pass: resolved_at set, then cleared on reopen (live DB test) | DB, A |
| TC13 | GraphRAG ask | Ask defaults (deforestation, α .5 β .3 γ .2, top 5) | Redirect to result; 5 evidence rows saved; confidence between 0 and 1 computed by `pkg_eo.fn_query_confidence` | Pass: 5 evidence rows, confidence in [0,1], matches Python formula (live DB test) | DB, A |
| TC14 | Ask: invalid | Question "hi", all weights 0 | Errors "at least 5 characters" and "At least one weight"; nothing saved | Pass: both errors, nothing saved (automated) | A |
| TC15 | Add detection | Image 57, Building, YOLO-EO, conf 0.9, area 900 | Saved with new ID; one new DETECTED_OBJECT INSERT row in audit log (trigger) | Pass: new ID returned, audit rows +1 (live DB test) | DB, A |
| TC16 | Detection: invalid values | Confidence 1.5, area -1 | "Confidence must be at most 1", "Area (m²) must be greater than 0"; nothing saved | Pass: both errors, nothing saved (automated) | A |
| TC17 | Detection: duplicate | Second Tree Coverage on image 1 | "Duplicate: this image already has a Tree Coverage patch" | Pass: duplicate rejected (live DB test) | DB |
| TC18 | Detection: unknown type (DB-level) | Call `pkg_eo.add_detection` with type "Spaceship" | ORA-20020 shown as "Unknown object type: Spaceship" | Pass: ORA-20020 'Unknown object type: Spaceship' (live DB test) | DB |
| TC19 | Satellite create | Name Demo-Sat, VIT, 2024-01-15, POLAR | "Satellite 'Demo-Sat' created" and it appears in the list | Pass: insert round-trip (live DB test + automated) | DB, A |
| TC20 | Satellite: validation | Empty name, launch 2099-01-01, orbit LUNAR | Three validation messages; nothing inserted | Pass: three messages, no insert (automated) | A |
| TC21 | Satellite: duplicate | Create "Sentinel-2A" again | ORA-00001 shown as "A satellite with this name already exists." | Pass: ORA-00001 → friendly message (live DB test) | DB, A |
| TC22 | Satellite update | Edit Demo-Sat → DECOMMISSIONED | "updated" message; list shows new status | Pass: status changed to DECOMMISSIONED (live DB test) | DB |
| TC23 | Satellite delete (allowed) | Delete Demo-Sat (no images) | "Satellite … deleted" | Pass: deleted, get returns nothing (live DB test) | DB |
| TC24 | Satellite delete (blocked) | Delete Sentinel-2A | ORA-02292 shown as "Cannot delete: other records still depend on this one" | Pass: ORA-02292 (live DB test) | DB, A |
| TC25 | CHECK constraint | `UPDATE image_frame SET cloud_cover = 140` | ORA-02290 → "Cloud cover must be between 0 and 100%." | Pass: ORA-02290 → 'between 0 and 100%' (live DB test) | DB |
| TC26 | Audit log | Open `/audit` after TC15 | Newest row: DETECTED_OBJECT INSERT with conf/area | Pass: trigger adds audit row (live DB test) | M |
| TC27 | DB unavailable | Stop Oracle, open `/` | Clear "Cannot connect to the database…" page instead of a stack trace | Pass: clear message, HTTP 500 page (automated) | A, M |
| TC28 | SQL injection attempt | Open `/images?location=x' OR '1'='1` in the browser address bar | Treated as text (bind variable): no rows, no error | Pass: "No images match these filters", no error (manual) | M |
| TC29 | Scoring unit tests | `pytest tests/test_services.py` | Cosine, haversine (Chennai–Delhi ≈ 1,757 km), ranking and confidence formula pass | Pass (automated) | A |
| TC30 | Error translation | `pytest -k friendly` | ORA-00001/02290/02291/02292/01400/20020/12541 mapped to readable messages | Pass (automated) | A |

**Automated test runs on the project laptop (Oracle 21c XE):** 27-Oct-2026 → **70 passed in 2.79 s**; 1-Nov-2026, after adding `test_integration_app.py` → **91 passed in 3.32 s** (32 unit, 29 UI, 18 live UI, 12 live database).

**Result: all 30 test cases passed** (91 automated tests + manual browser checks).
