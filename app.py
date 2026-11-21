import os
import html
import json
import time
import uuid
import hashlib
import threading
from datetime import datetime
from functools import lru_cache
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # 1 MB max request payload

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

store_lock = threading.Lock()
rate_limit_tracker: dict[str, list[float]] = {}

# O(1) dictionary store indexed by issue ID
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
        "created_at": "2026-11-21 09:15 AM"
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
        "created_at": "2026-11-21 10:30 AM"
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
        "created_at": "2026-11-21 08:45 AM"
    }
}

ALLOWED_STATUSES = {"Reported", "In Progress", "Resolved"}
ALLOWED_PRIORITIES = {"Low", "Medium", "High", "Critical"}


@app.after_request
def apply_security_headers(response):
    """Attach strict HTTP security and caching headers."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self';"
    )
    return response


def is_rate_limited(client_ip: str, max_requests: int = 15, window_sec: int = 60) -> bool:
    """Sliding-window rate limiter to protect API endpoints from abuse."""
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
    """Strip control characters, escape HTML to prevent XSS, and bound length."""
    cleaned = " ".join(str(value or "").strip().split())
    return html.escape(cleaned[:max_len], quote=True)


def validate_image_data(image_data: str) -> str:
    """Allow only safe base64 image data URIs under 500 KB."""
    if not image_data:
        return ""
    allowed_prefixes = ("data:image/jpeg;base64,", "data:image/png;base64,", "data:image/webp;base64,")
    if len(image_data) <= 500_000 and image_data.startswith(allowed_prefixes):
        return image_data
    return ""


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
        "summary": f"{cat} issue reported: {title[:80]}"
    }


@lru_cache(maxsize=128)
def cached_gemini_triage(cache_key: str, title: str, description: str, location: str, user_category: str) -> str:
    """Memoized Google Gemini triage call to eliminate redundant API latency and token cost."""
    if not client:
        return json.dumps(fallback_triage(title, description, user_category))

    prompt = f"""
    You are an AI Civic Triage Assistant for CivicResolve, a municipal grievance management platform.
    Analyze the citizen's complaint below and return ONLY valid JSON matching the requested schema.

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

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
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
    except Exception:
        return json.dumps(fallback_triage(title, description, user_category))


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "issues_count": len(issues_by_id)}), 200


@app.route("/api/issues", methods=["GET"])
def get_issues():
    with store_lock:
        ordered = list(reversed(list(issues_by_id.values())))
    return jsonify(ordered), 200


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
    image_data = validate_image_data(str(payload.get("image_data", "")).strip())

    if not title or not description or not location:
        return jsonify({"error": "Title, description, and location are required."}), 400

    cache_key = hashlib.sha256(f"{title}|{description}|{location}|{user_category}".encode()).hexdigest()
    triage = json.loads(cached_gemini_triage(cache_key, title, description, location, user_category))

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
            "image_data": image_data,
            "created_at": datetime.now().strftime("%Y-%m-%d %I:%M %p"),
        }
        issues_by_id[issue_id] = new_issue

    return jsonify(new_issue), 201


@app.route("/api/issues/<issue_id>/status", methods=["PATCH"])
def update_issue_status(issue_id: str):
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
