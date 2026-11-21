# CivicResolve — AI-Powered Citizen Grievance Management & Resolution

> **Hackathon Track:** PromptWars × GDG on-Campus PSG iTech  
> **Problem Statement:** AI-Powered Citizen Grievance Management and Resolution  
> **Tech Stack:** Python, Flask, Tailwind CSS, Google Gemini API (`gemini-2.5-flash`)

---

## Overview

**CivicResolve** bridges the gap between citizens and municipal authorities by transforming unstructured public complaints into prioritized, department-routed action items using **Google Gemini**. Instead of grievances stalling in generic inboxes without urgency triage or transparent status updates, CivicResolve automatically categorizes each report, assesses safety/urgency impact, assigns the responsible civic department, and provides real-time status tracking from **Reported → In Progress → Resolved**.

---

## Key Features

- **Citizen Issue Reporting:** Submit civic complaints with title, location/landmark, optional manual category, detailed description, and optional compressed photo evidence.
- **Automated Gemini AI Triage (`/api/issues`):** Uses `gemini-2.5-flash` with structured JSON output (`response_schema`) to classify issues into municipal categories, assign priority (`Low`, `Medium`, `High`, `Critical`) with reasoning, generate an actionable summary, and route to the appropriate department (with a deterministic rule-based fallback).
- **Live Issue Tracking Dashboard:** Filterable community feed by status (`Reported`, `In Progress`, `Resolved`) and priority level so residents can verify progress transparently.
- **Municipal Admin Console:** Real-time KPI summary cards and one-click status updates (`Reported` → `In Progress` → `Resolved`) backed by AI triage explanations.
- **Security & Accessibility:** API keys loaded strictly via environment variables (`GEMINI_API_KEY`), input length validation, semantic HTML landmarks, ARIA live regions, and high-contrast status/priority badges.

---

## Architecture

```text
Citizen / Admin Browser (Tailwind UI)
        │
        ▼  REST JSON (/api/issues, /api/issues/<id>/status)
Flask Application Server (app.py)
        │
        ├──► Google Gemini API (gemini-2.5-flash structured JSON triage)
        └──► In-Memory Issue Store (pre-seeded + live updates)

```

---

## Local Setup & Run Instructions

1. **Clone the repository:**
   ```bash
   git clone https://github.com/tarunikagk-prog/CivicResolve.git
   cd CivicResolve
   ```
2. **Install dependencies:**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. **Configure environment variables:**
   Create a `.env` file in the root directory (excluded by `.gitignore`):
   ```env
   GEMINI_API_KEY=your_google_gemini_api_key
   ```
4. **Start the server:**
   ```bash
   python app.py
   ```
   Open `[http://127.0.0.1:5000](http://127.0.0.1:5000)` in your browser.

---

## Submission Compliance Checklist

- [x] Public GitHub repository with a single `main` branch (`tarunikagk-prog/CivicResolve`)
- [x] Total repository size well under 10 MB
- [x] `README.md` with setup instructions, architecture, and feature documentation
- [x] Google Gemini API integration (`google-genai` SDK) with environment-based secret management
