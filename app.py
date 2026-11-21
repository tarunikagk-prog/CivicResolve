import os
import re
import html
import json
import time
import uuid
import base64
import hashlib
import logging
import threading
from datetime import datetime
from functools import lru_cache
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("civicresolve")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # 1 MB max payload

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
ADMIN_ACCESS_CODE = os.getenv("ADMIN_ACCESS_CODE", "CIVIC-ADMIN-2026")
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

store_lock = threading.Lock()
rate_limit_tracker: dict[str, list[float]] = {}

ALLOWED_STATUSES = {"Reported", "In Progress", "Resolved"}
ALLOWED_PRIORITIES = {"Low", "Medium", "High", "Critical"}
ISSUE_ID_PATTERN = re.compile(r"^CR-[A-Z0-9-]{3,20}$")

issues_by_id: dict[str, dict] = {
    "CR-101": {
        "id": "CR-101",
        "title": "Deep pothole near Peelamedu bus stop",
        "description": "Large waterlogged pothole on the main arterial road causing two-wheelers to swerve dangerously during peak traffic.",
        "location": "Avinashi Road, Peelamedu, Coimbatore",
        "category": "Roads & Potholes",
        "priority": "High",
        "priority_reason": "High traffic density and immediate risk of vehicular accidents.",
        "department": "Roads & Highways Maintenance Department",
        "summary": "Hazardous main-road pothole near Peelamedu bus stop requiring urgent patching.",
        "status": "In Progress",
        "image_data": "",
        "created_at": "2026-11-21 09:15 AM",
    },
    "CR-102": {
        "id": "CR-102",
        "title": "Overflowing garbage bin beside community park",
        "description": "Garbage has not been cleared for three days and is spilling onto the footpath, attracting stray animals.",
        "location": "Cross Cut Road, Gandhipuram",
        "category": "Waste Management",
        "priority": "Medium",
        "priority_reason": "Sanitation and public hygiene concern affecting pedestrians.",
        "department": "Solid Waste Management & Sanitation",
        "summary": "Uncollected garbage overflow obstructing pedestrian walkway near Gandhipuram.",
        "status": "Reported",
        "image_data": "",
        "created_at": "2026-11-21 10:30 AM",
    },
    "CR-103": {
        "id": "CR-103",
        "title": "Broken streetlight pole leaning near school gate",
        "description": "Streetlight is completely dark at night and the pole base looks loose right outside the school entrance.",
        "location": "Saravanampatti Main Road",
        "category": "Streetlights & Electrical",
        "priority": "Critical",
        "priority_reason": "Structural and electrical hazard in a high-footfall school zone.",
        "department": "Municipal Electrical & Street Lighting Division",
        "summary": "Damaged, leaning streetlight pole outside school posing safety and visibility risks.",
        "status": "Resolved",
        "image_data": "",
        "created_at": "2026-11-21 08:45 AM",
    },
}


@app.after_request
def apply_security_headers(response):
    """Attach strict HTTP security and caching headers to every response."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none';"
    )
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def is_rate_limited(client_ip: str, max_requests: int = 20, window_sec: int = 60) -> bool:
    """Sliding-window rate limiter to protect AI triage endpoints from abuse."""
    now = time.time()
    with store_lock:
        timestamps = [t for t in rate_limit_tracker.get(client_ip, []) if now - t < window_sec]
        if len(timestamps) >= max_requests:
            rate_limit_tracker[client_ip] = timestamps
            return True
        timestamps.append(now)
        rate_limit_tracker[client_ip] = timestamps
        return False


def sanitize_text(value: str, max_len: int) -> str:
    """Normalize whitespace, escape HTML entities against XSS, and cap length."""
    cleaned = " ".join(str(value or "").strip().split())
    return html.escape(cleaned[:max_len], quote=True)


def validate_image_data(image_data: str) -> tuple[str, str, bytes | None]:
    """Validate base64 image data URI under 500 KB and decode raw bytes for multimodal AI."""
    if not image_data:
        return "", "", None
    allowed_prefixes = {
        "data:image/jpeg;base64,": "image/jpeg",
        "data:image/png;base64,": "image/png",
        "data:image/webp;base64,": "image/webp",
    }
    if len(image_data) > 500_000:
        return "", "", None
    for prefix, mime_type in allowed_prefixes.items():
        if image_data.startswith(prefix):
            try:
                raw_bytes = base64.b64decode(image_data[len(prefix):], validate=True)
                return image_data, mime_type, raw_bytes
            except Exception:
                return "", "", None
    return "", "", None


def fallback_triage(title: str, description: str, user_category: str) -> dict:
    """Deterministic keyword-based fallback when Gemini API is unavailable."""
    text = f"{title} {description} {user_category}".lower()
    if any(k in text for k in ("water", "pipe", "leak", "drain", "sewage", "flood")):
        cat, dept = "Water Supply & Drainage", "Water Supply & Drainage Board"
    elif any(k in text for k in ("pothole", "road", "asphalt", "bridge", "footpath")):
        cat, dept = "Roads & Potholes", "Roads & Highways Maintenance Department"
    elif any(k in text for k in ("garbage", "trash", "waste", "bin", "dump")):
        cat, dept = "Waste Management", "Solid Waste Management & Sanitation"
    elif any(k in text for k in ("light", "wire", "electric", "pole", "power")):
        cat, dept = "Streetlights & Electrical", "Municipal Electrical & Street Lighting Division"
    else:
        cat = user_category if user_category and user_category != "Auto-Detect (AI)" else "Public Safety & Infrastructure"
        dept = "Municipal Civic Operations Cell"

    if any(k in text for k in ("live wire", "collapse", "flood", "hospital", "school", "accident", "burst")):
        prio, reason = "Critical", "Immediate public safety hazard detected from complaint context."
    elif any(k in text for k in ("urgent", "dangerous", "blocked", "overflow", "leak")):
        prio, reason = "High", "Significant disruption or safety impact to daily commuters."
    else:
        prio, reason = "Medium", "Standard civic maintenance required to restore public service."

    return {
        "category": cat,
        "priority": prio,
        "priority_reason": reason,
        "department": dept,
        "summary": f"{cat} issue reported: {title[:80]}",
    }


@lru_cache(maxsize=128)
def cached_gemini_triage(
    cache_key: str,
    title: str,
    description: str,
    location: str,
    user_category: str,
    image_mime: str = "",
    image_bytes: bytes | None = None,
) -> str:
    """Memoized multimodal Google Gemini triage for text + optional photo evidence."""
    if not client:
        return json.dumps(fallback_triage(title, description, user_category))

    prompt = f"""
    You are an AI Civic Triage Assistant for CivicResolve, a municipal grievance management platform.
    Analyze the citizen's complaint (and attached photo if provided) and return ONLY valid JSON.

    Complaint Title: {title}
    Description: {description}
    Location: {location}
    Citizen-Selected Category: {user_category or "Not specified"}

    Guidelines:
    - Choose the most accurate category from: "Roads & Potholes", "Waste Management", "Streetlights & Electrical", "Water Supply & Drainage", "Public Safety & Infrastructure".
    - Assign priority strictly as one of: "Low", "Medium", "High", "Critical" based on public safety risk, urgency, and community impact.
    - Recommend the exact municipal department responsible for resolving the issue.
    - Provide a clear 1-sentence reason for the priority level and a concise 1-sentence summary.
    """

    contents: list = [prompt]
    if image_bytes and image_mime:
        contents.append(types.Part.from_bytes(data=image_bytes, mime_type=image_mime))

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema={
                    "type": "OBJECT",
                    "properties": {
                        "category": {"type": "STRING"},
                        "priority": {"type": "STRING"},
                        "priority_reason": {"type": "STRING"},
                        "department": {"type": "STRING"},
                        "summary": {"type": "STRING"},
                    },
                    "required": ["category", "priority", "priority_reason", "department", "summary"],
                },
                temperature=0.2,
            ),
        )
        data = json.loads(response.text)
        if data.get("priority") not in ALLOWED_PRIORITIES:
            data["priority"] = "Medium"
        return json.dumps(data)
    except Exception as exc:
        logger.warning("Gemini triage fallback triggered: %s", exc)
        return json.dumps(fallback_triage(title, description, user_category))


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "issues_count": len(issues_by_id)}), 200


@app.route("/api/issues", methods=["GET"])
def get_issues():
    status_filter = request.args.get("status", "").strip()
    priority_filter = request.args.get("priority", "").strip()
    with store_lock:
        items = list(reversed(list(issues_by_id.values())))
    if status_filter and status_filter != "ALL":
        items = [i for i in items if i["status"] == status_filter]
    if priority_filter and priority_filter != "ALL":
        items = [i for i in items if i["priority"] == priority_filter]
    return jsonify(items), 200


@app.route("/api/issues", methods=["POST"])
def create_issue():
    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()
    if is_rate_limited(client_ip):
        return jsonify({"error": "Rate limit exceeded. Please try again shortly."}), 429

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid JSON request body."}), 400

    title = sanitize_text(payload.get("title", ""), 140)
    description = sanitize_text(payload.get("description", ""), 1000)
    location = sanitize_text(payload.get("location", ""), 160)
    user_category = sanitize_text(payload.get("category", ""), 60)
    safe_image_uri, image_mime, image_bytes = validate_image_data(str(payload.get("image_data", "")).strip())

    if not title or not description or not location:
        return jsonify({"error": "Title, description, and location are required."}), 400

    img_hash = hashlib.sha256(image_bytes).hexdigest() if image_bytes else "no-img"
    cache_key = hashlib.sha256(f"{title}|{description}|{location}|{user_category}|{img_hash}".encode()).hexdigest()
    triage = json.loads(
        cached_gemini_triage(cache_key, title, description, location, user_category, image_mime, image_bytes)
    )

    with store_lock:
        issue_id = f"CR-{100 + len(issues_by_id) + 1}-{uuid.uuid4().hex[:3].upper()}"
        new_issue = {
            "id": issue_id,
            "title": title,
            "description": description,
            "location": location,
            "category": sanitize_text(triage.get("category", "Public Safety & Infrastructure"), 80),
            "priority": triage.get("priority", "Medium") if triage.get("priority") in ALLOWED_PRIORITIES else "Medium",
            "priority_reason": sanitize_text(triage.get("priority_reason", ""), 240),
            "department": sanitize_text(triage.get("department", "Municipal Civic Operations Cell"), 100),
            "summary": sanitize_text(triage.get("summary", ""), 200),
            "status": "Reported",
            "image_data": safe_image_uri,
            "created_at": datetime.now().strftime("%Y-%m-%d %I:%M %p"),
        }
        issues_by_id[issue_id] = new_issue

    return jsonify(new_issue), 201


@app.route("/api/issues/<issue_id>/status", methods=["PATCH"])
def update_issue_status(issue_id: str):
    if not ISSUE_ID_PATTERN.match(issue_id):
        return jsonify({"error": "Invalid issue ID format."}), 400

    admin_token = request.headers.get("X-Admin-Token", "").strip()
    if admin_token and admin_token != ADMIN_ACCESS_CODE:
        return jsonify({"error": "Unauthorized admin token."}), 401

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid JSON request body."}), 400

    new_status = str(payload.get("status", "")).strip()
    if new_status not in ALLOWED_STATUSES:
        return jsonify({"error": "Invalid status value."}), 400

    with store_lock:
        issue = issues_by_id.get(issue_id)
        if not issue:
            return jsonify({"error": "Issue not found."}), 404
        issue["status"] = new_status
        return jsonify(issue), 200


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
