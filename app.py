import os
import json
import uuid
from datetime import datetime
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

app = Flask(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

issues_store = [
    {
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
    {
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
    {
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
]


def fallback_triage(title: str, description: str, user_category: str) -> dict:
    """Rule-based fallback if Gemini API is unavailable."""
    text = f"{title} {description} {user_category}".lower()
    if any(k in text for k in ["pothole", "road", "asphalt", "bridge", "footpath"]):
        cat, dept = "Roads & Potholes", "Roads & Highways Maintenance Department"
    elif any(k in text for k in ["garbage", "trash", "waste", "bin", "dump"]):
        cat, dept = "Waste Management", "Solid Waste Management & Sanitation"
    elif any(k in text for k in ["light", "wire", "electric", "pole", "power"]):
        cat, dept = "Streetlights & Electrical", "Municipal Electrical & Street Lighting Division"
    elif any(k in text for k in ["water", "pipe", "leak", "drain", "sewage", "flood"]):
        cat, dept = "Water Supply & Drainage", "Water Supply & Drainage Board"
    else:
        cat = user_category if user_category and user_category != "Auto-Detect (AI)" else "Public Safety & Infrastructure"
        dept = "Municipal Civic Operations Cell"

    if any(k in text for k in ["live wire", "collapse", "flood", "hospital", "school", "accident", "burst"]):
        prio, reason = "Critical", "Immediate public safety hazard detected from complaint context."
    elif any(k in text for k in ["urgent", "dangerous", "blocked", "overflow", "leak"]):
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


def analyze_with_gemini(title: str, description: str, location: str, user_category: str) -> dict:
    """Uses Google Gemini to classify, prioritize, and route a civic complaint."""
    if not client:
        return fallback_triage(title, description, user_category)

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
        allowed_priorities = {"Low", "Medium", "High", "Critical"}
        if data.get("priority") not in allowed_priorities:
            data["priority"] = "Medium"
        return data
    except Exception:
        return fallback_triage(title, description, user_category)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/issues", methods=["GET"])
def get_issues():
    return jsonify(issues_store)


@app.route("/api/issues", methods=["POST"])
def create_issue():
    payload = request.get_json(silent=True) or {}
    title = str(payload.get("title", "")).strip()
    description = str(payload.get("description", "")).strip()
    location = str(payload.get("location", "")).strip()
    user_category = str(payload.get("category", "")).strip()
    image_data = str(payload.get("image_data", "")).strip()

    if not title or not description or not location:
        return jsonify({"error": "Title, description, and location are required."}), 400

    if len(image_data) > 500_000:
        image_data = ""

    triage = analyze_with_gemini(title, description, location, user_category)

    new_issue = {
        "id": f"CR-{100 + len(issues_store) + 1}-{uuid.uuid4().hex[:3].upper()}",
        "title": title[:140],
        "description": description[:1000],
        "location": location[:160],
        "category": triage["category"],
        "priority": triage["priority"],
        "priority_reason": triage["priority_reason"],
        "department": triage["department"],
        "summary": triage["summary"],
        "status": "Reported",
        "image_data": image_data,
        "created_at": datetime.now().strftime("%Y-%m-%d %I:%M %p"),
    }
    issues_store.insert(0, new_issue)
    return jsonify(new_issue), 201


@app.route("/api/issues/<issue_id>/status", methods=["PATCH"])
def update_issue_status(issue_id):
    payload = request.get_json(silent=True) or {}
    new_status = str(payload.get("status", "")).strip()
    if new_status not in {"Reported", "In Progress", "Resolved"}:
        return jsonify({"error": "Invalid status value."}), 400

    for issue in issues_store:
        if issue["id"] == issue_id:
            issue["status"] = new_status
            return jsonify(issue)

    return jsonify({"error": "Issue not found."}), 404


if __name__ == "__main__":
    app.run(debug=True, port=5000)
