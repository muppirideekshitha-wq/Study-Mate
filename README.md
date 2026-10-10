# StudyMate

A web app that syncs with Google Classroom, sends reminders 2 days, 1 day and 2 hours before assignments are due, and lets you chat with Gemini AI about your class files.

## Run locally
1. `python -m venv venv`, then `venv\Scripts\activate`
2. `python -m pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and fill in your keys
4. `python -m uvicorn main:app --reload --port 8000`
5. Open http://localhost:8000