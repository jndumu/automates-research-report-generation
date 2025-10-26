from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
import os
from fastapi.responses import FileResponse

from research_and_analyst.database.db_config import (
    SessionLocal, User, hash_password, verify_password
)
from research_and_analyst.utils.model_loader import ModelLoader
from research_and_analyst.workflows.report_generator_workflow import AutonomousReportGenerator

app = FastAPI(title="Autonomous Report Generator UI")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="research_and_analyst/api/templates")

def basename_filter(path: str):
    return os.path.basename(path)

templates.env.filters["basename"] = basename_filter

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
    
@app.get("/", response_class=HTMLResponse)
async def show_login(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

SESSIONS = {}

@app.get("/", response_class=HTMLResponse)
async def show_login(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login", response_class=HTMLResponse)
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    db = next(get_db())
    user = db.query(User).filter(User.username == username).first()

    if user and verify_password(password, user.password):
        session_id = f"{username}_session"
        SESSIONS[session_id] = username
        response = RedirectResponse(url="/dashboard", status_code=302)
        response.set_cookie(key="session_id", value=session_id)
        return response

    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": "Invalid username or password"},
    )
    
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    session_id = request.cookies.get("session_id")
    if session_id not in SESSIONS:
        return RedirectResponse(url="/")
    return templates.TemplateResponse("dashboard.html", {"request": request, "user": SESSIONS[session_id]})

@app.post("/generate_report", response_class=HTMLResponse)
async def generate_report(request: Request, topic: str = Form(...)):
    llm = ModelLoader().load_llm()
    generator = AutonomousReportGenerator(llm)
    graph = generator.build_graph()

    thread = {"configurable": {"thread_id": "1"}}
    for _ in graph.stream({"topic": topic, "max_analysts": 3}, thread, stream_mode="values"):
        pass

    state = graph.get_state(thread)
    feedback = ""
    return templates.TemplateResponse(
        "report_progress.html",
        {"request": request, "topic": topic, "feedback": feedback},
    )

@app.post("/submit_feedback", response_class=HTMLResponse)
async def submit_feedback(request: Request, topic: str = Form(...), feedback: str = Form(...)):
    llm = ModelLoader().load_llm()
    generator = AutonomousReportGenerator(llm)
    graph = generator.build_graph()

    thread = {"configurable": {"thread_id": "1"}}
    graph.update_state(
    thread,
    {
        "human_analyst_feedback": feedback,
        "topic": topic  # ensure topic is preserved
    },
    as_node="human_feedback"
    )

    for _ in graph.stream(None, thread, stream_mode="values"):
        pass

    final_state = graph.get_state(thread)
    final_report = final_state.values.get("final_report")
    
    if not final_report:
        generator.logger.warning("Final report content is None — generating fallback report.")
        final_report = f"Report on '{topic}' was generated successfully, but no text output was returned.\nPlease re-run the workflow or verify analyst responses."


    doc_path = generator.save_report(final_report, topic, "docx")
    pdf_path = generator.save_report(final_report, topic, "pdf")

    return templates.TemplateResponse(
        "report_progress.html",
        {
            "request": request,
            "topic": topic,
            "feedback": feedback,
            "doc_path": doc_path,
            "pdf_path": pdf_path,
        },
    )

@app.get("/signup", response_class=HTMLResponse)
async def show_signup(request: Request):
    return templates.TemplateResponse("signup.html", {"request": request})

@app.post("/signup", response_class=HTMLResponse)
async def signup(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    db = next(get_db())
    existing_user = db.query(User).filter(User.username == username).first()
    if existing_user:
        return templates.TemplateResponse(
            "signup.html",
            {"request": request, "error": "Username already exists"},
        )

    hashed_pw = hash_password(password)
    new_user = User(username=username, password=hashed_pw)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return RedirectResponse(url="/", status_code=302)

@app.get("/download/{file_name}", response_class=FileResponse)
async def download_report(file_name: str):
    report_dir = os.path.join(os.getcwd(), "generated_report")

    for root, dirs, files in os.walk(report_dir):
        if file_name in files:
            return FileResponse(
                path=os.path.join(root, file_name),
                filename=file_name,
                media_type="application/octet-stream"
            )
    return {"error": f"File {file_name} not found"}
