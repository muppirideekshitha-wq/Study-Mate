"""StudyMate - Google Classroom reminders + Gemini study chatbot (FastAPI)."""
import io, os, secrets, time
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from google import genai
from google.genai import types
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from pydantic import BaseModel
from pypdf import PdfReader
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")

# Main Gemini model, plus backups that are tried in order if it is busy.
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
MODELS = [MODEL] + [m.strip() for m in os.getenv("GEMINI_FALLBACKS", "").split(",") if m.strip()]

# Allow plain http for local testing only; Google scope changes shouldn't crash login.
if BASE_URL.startswith("http://"):
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"

# Read-only access: we can look at Classroom and Drive files, never change them.
SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.profile",  # your name + photo
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.coursework.me.readonly",
    "https://www.googleapis.com/auth/classroom.courseworkmaterials.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET", "dev-secret"))

# Give up on a stuck Gemini request after 60 seconds (value is in milliseconds).
gemini = genai.Client(api_key=os.getenv("GEMINI_API_KEY"),
                      http_options=types.HttpOptions(timeout=60000))

# Simple in-memory stores (fine for one user / learning project).
TOKENS: dict[str, Credentials] = {}   # session id -> Google credentials
LIBRARY: dict[str, dict] = {}         # session id -> {file_id: {name, text}}
PROFILES: dict[str, dict] = {}        # session id -> {name, first, picture}


def make_flow() -> Flow:
    cfg = {"web": {
        "client_id": os.getenv("GOOGLE_CLIENT_ID"),
        "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }}
    return Flow.from_client_config(cfg, scopes=SCOPES, redirect_uri=f"{BASE_URL}/callback")


def creds(request: Request) -> Credentials:
    c = TOKENS.get(request.session.get("sid", ""))
    if not c:
        raise HTTPException(401, "Not signed in")
    return c


# ---------- Login ----------
@app.get("/login")
def login(request: Request):
    flow = make_flow()
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    request.session["state"] = state
    request.session["cv"] = flow.code_verifier  # needed to finish the login in /callback
    return RedirectResponse(url)


@app.get("/callback")
def callback(request: Request, code: str, state: str):
    if state != request.session.get("state"):
        raise HTTPException(400, "Login state mismatch")
    flow = make_flow()
    flow.code_verifier = request.session.get("cv")
    flow.fetch_token(code=code)
    sid = secrets.token_hex(16)
    TOKENS[sid] = flow.credentials
    try:  # fetch the Google account name and photo for the header
        info = build("oauth2", "v2", credentials=flow.credentials).userinfo().get().execute()
        PROFILES[sid] = {"name": info.get("name", ""), "picture": info.get("picture", ""),
                         "first": info.get("given_name") or info.get("name", "Student")}
    except Exception:
        PROFILES[sid] = {"name": "Student", "first": "Student", "picture": ""}
    request.session["sid"] = sid
    return RedirectResponse("/")


@app.get("/logout")
def logout(request: Request):
    sid = request.session.pop("sid", None)
    TOKENS.pop(sid, None); LIBRARY.pop(sid, None); PROFILES.pop(sid, None)
    return RedirectResponse("/")


@app.get("/api/me")
def me(request: Request):
    sid = request.session.get("sid", "")
    return {"signed_in": sid in TOKENS, "profile": PROFILES.get(sid, {}),
            "library": [{"id": k, "name": v["name"]} for k, v in LIBRARY.get(sid, {}).items()]}


# ---------- Classroom data ----------
def courses(svc):
    return svc.courses().list(courseStates=["ACTIVE"]).execute().get("courses", [])


@app.get("/api/assignments")
def assignments(request: Request):
    svc = build("classroom", "v1", credentials=creds(request))
    out = []
    for c in courses(svc):
        # Which assignments have I already handed in?
        subs = svc.courses().courseWork().studentSubmissions().list(
            courseId=c["id"], courseWorkId="-", userId="me").execute().get("studentSubmissions", [])
        done = {s["courseWorkId"] for s in subs if s.get("state") in ("TURNED_IN", "RETURNED")}
        for w in svc.courses().courseWork().list(courseId=c["id"]).execute().get("courseWork", []):
            d, t = w.get("dueDate"), w.get("dueTime", {})
            if not d:
                continue  # no due date = nothing to remind about
            due = datetime(d["year"], d["month"], d["day"], t.get("hours", 0),
                           t.get("minutes", 0), tzinfo=timezone.utc)  # Classroom stores UTC
            out.append({"id": w["id"], "title": w["title"], "course": c["name"],
                        "due": due.isoformat(), "done": w["id"] in done,
                        "link": w.get("alternateLink")})
    return sorted(out, key=lambda a: a["due"])


def classroom_files(c: Credentials) -> list[dict]:
    """Every Drive file attached to Classroom work or materials."""
    svc = build("classroom", "v1", credentials=c)
    found = {}
    def grab(items, course):
        for it in items:
            for m in it.get("materials", []):
                f = m.get("driveFile", {}).get("driveFile")
                if f:
                    found[f["id"]] = {"id": f["id"], "name": f.get("title", "Untitled"), "course": course}
    for co in courses(svc):
        grab(svc.courses().courseWork().list(courseId=co["id"]).execute().get("courseWork", []), co["name"])
        grab(svc.courses().courseWorkMaterials().list(courseId=co["id"]).execute().get("courseWorkMaterial", []), co["name"])
    return list(found.values())


@app.get("/api/files")
def files(request: Request):
    return classroom_files(creds(request))


@app.post("/api/import/{file_id}")
def import_file(file_id: str, request: Request):
    c = creds(request)
    # Safety rule: only files that really belong to Classroom can be imported.
    match = next((f for f in classroom_files(c) if f["id"] == file_id), None)
    if not match:
        raise HTTPException(403, "That file is not part of your Classroom")
    drive = build("drive", "v3", credentials=c)
    mime = drive.files().get(fileId=file_id, fields="mimeType").execute()["mimeType"]
    if mime == "application/vnd.google-apps.document":
        text = drive.files().export_media(fileId=file_id, mimeType="text/plain").execute().decode("utf-8", "ignore")
    else:
        buf, done = io.BytesIO(), False
        dl = MediaIoBaseDownload(buf, drive.files().get_media(fileId=file_id))
        while not done:
            _, done = dl.next_chunk()
        if mime == "application/pdf":
            text = "\n".join(p.extract_text() or "" for p in PdfReader(buf).pages)
        else:
            text = buf.getvalue().decode("utf-8", "ignore")
    # Keep only the first 12,000 characters so Gemini answers quickly.
    LIBRARY.setdefault(request.session["sid"], {})[file_id] = {"name": match["name"], "text": text[:12000]}
    return {"ok": True, "name": match["name"]}


@app.delete("/api/import/{file_id}")
def remove_file(file_id: str, request: Request):
    """Un-import a file so Gemini no longer reads it."""
    creds(request)
    LIBRARY.get(request.session["sid"], {}).pop(file_id, None)
    return {"ok": True}


# ---------- Gemini chatbot ----------
class Chat(BaseModel):
    message: str


@app.post("/api/chat")
def chat(body: Chat, request: Request):
    creds(request)
    docs = LIBRARY.get(request.session["sid"], {})
    material = "\n\n".join(f"[{d['name']}]\n{d['text']}" for d in docs.values()) or "(none imported yet)"
    prompt = ("You are StudyMate, a friendly study tutor. Answer clearly and briefly. "
              "Use the imported class material when it helps.\n\n"
              f"Class material:\n{material}\n\nStudent question: {body.message}")
    print(f"[chat] prompt size: {len(prompt)} characters")  # shows in the VS Code terminal

    last_error = None
    for model in MODELS:  # try each model in order until one answers
        start = time.time()
        try:
            reply = gemini.models.generate_content(model=model, contents=prompt).text
            print(f"[chat] {model} answered in {time.time() - start:.1f}s")
            return {"reply": reply}
        except Exception as e:
            last_error = e
            print(f"[chat] {model} failed after {time.time() - start:.1f}s: {str(e)[:100]}")
    raise HTTPException(500, f"Gemini is busy right now, try again in a minute. ({last_error})")


@app.get("/")
def home():
    return FileResponse("static/index.html")


app.mount("/static", StaticFiles(directory="static"), name="static")