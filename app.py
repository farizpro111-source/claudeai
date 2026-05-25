import os
import json
import uuid
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Cookie, Response
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from google import genai
from google.genai import types

load_dotenv()

MODEL = "gemini-2.5-flash"
WORKROOT = Path("/tmp/excel_app_sessions")
WORKROOT.mkdir(exist_ok=True)
EXEC_TIMEOUT = 60  # секунд на выполнение скрипта

SYSTEM_PROMPT = """Ты — помощник по Excel. Пользователь пишет задачу по-русски, иногда прикладывает файл (.xlsx, .xls, .csv).

Если файл приложен, тебе показывают его превью (имена листов, столбцы, первые строки) и сообщают путь к нему в рабочей папке. Сам файл ты не видишь — только превью.

Отвечай ВСЕГДА только JSON по схеме:
{
  "explanation": "Краткое объяснение по-русски: что ты сделал или какой даёшь совет.",
  "python_code": "Python-код, который читает входной файл и сохраняет результат. Пустая строка — если код не нужен.",
  "output_filename": "Имя итогового файла, например result.xlsx. Пустая строка — если нет выходного файла."
}

Правила для python_code:
- Доступны: pandas, openpyxl, xlsxwriter, numpy, стандартная библиотека.
- Текущая директория уже выставлена в рабочую папку. Читай входной файл по относительному пути (имя файла дано в превью).
- Сохраняй итог в текущую директорию под коротким именем (result.xlsx, отчет.xlsx, pivot.xlsx).
- НЕ используй input(), sys.argv, сетевые запросы.
- В конце скрипта напечатай print('OK') — для логов.

Если файла нет, а пользователь просто спрашивает совет / формулу / макрос — дай ответ в "explanation", python_code оставь пустым."""

API_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=API_KEY) if API_KEY else None

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# session_id -> {"workdir": Path, "history": list, "input_file": str | None}
SESSIONS: dict[str, dict[str, Any]] = {}


def get_session(session_id: str | None) -> tuple[str, dict[str, Any]]:
    if not session_id or session_id not in SESSIONS:
        session_id = uuid.uuid4().hex
        workdir = WORKROOT / session_id
        workdir.mkdir(exist_ok=True)
        SESSIONS[session_id] = {"workdir": workdir, "history": [], "input_file": None}
    return session_id, SESSIONS[session_id]


def excel_preview(path: Path) -> str:
    suffix = path.suffix.lower()
    try:
        if suffix == ".csv":
            df = pd.read_csv(path, nrows=20)
            return (
                f"CSV. Столбцы: {list(df.columns)}\n"
                f"Первые строки:\n{df.head(20).to_string()}"
            )
        sheets = pd.read_excel(path, sheet_name=None)
        parts = []
        for name, df in sheets.items():
            parts.append(
                f"=== Лист '{name}' ({len(df)} строк) ===\n"
                f"Столбцы: {list(df.columns)}\n"
                f"Первые строки:\n{df.head(15).to_string()}"
            )
        return "\n\n".join(parts)
    except Exception as e:
        return f"Не удалось прочитать превью: {e}"


def execute_python(code: str, workdir: Path) -> tuple[bool, str, str, list[str]]:
    """Запустить код в workdir. Вернёт (success, stdout, stderr, новые_файлы)."""
    before = {p.name for p in workdir.iterdir()}
    script = workdir / "_run.py"
    script.write_text(code, encoding="utf-8")
    success = False
    stdout = stderr = ""
    try:
        result = subprocess.run(
            ["python", str(script)],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=EXEC_TIMEOUT,
        )
        stdout = result.stdout
        stderr = result.stderr
        success = result.returncode == 0
    except subprocess.TimeoutExpired:
        stderr = f"Скрипт работал дольше {EXEC_TIMEOUT}с и был прерван."
    finally:
        if script.exists():
            script.unlink()
    after = {p.name for p in workdir.iterdir()}
    new_files = sorted(after - before)
    return success, stdout, stderr, new_files


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/chat")
async def chat(
    response: Response,
    message: str = Form(""),
    file: UploadFile | None = File(None),
    session_id: str | None = Cookie(default=None),
):
    if client is None:
        raise HTTPException(
            status_code=500,
            detail="GEMINI_API_KEY не задан. Получите бесплатный ключ на https://aistudio.google.com/apikey и впишите в .env",
        )
    if not message.strip() and file is None:
        raise HTTPException(status_code=400, detail="Пустое сообщение")

    sid, session = get_session(session_id)
    response.set_cookie("session_id", sid, httponly=True, samesite="lax")
    workdir: Path = session["workdir"]

    if file is not None:
        raw = await file.read()
        if not raw:
            raise HTTPException(status_code=400, detail="Файл пуст")
        safe_name = Path(file.filename or "input.xlsx").name
        (workdir / safe_name).write_bytes(raw)
        session["input_file"] = safe_name

    # Сформировать сообщение пользователю для модели
    if session["input_file"]:
        preview = excel_preview(workdir / session["input_file"])
        user_text = (
            f"Входной файл: {session['input_file']}\n\n"
            f"Превью:\n{preview}\n\n"
            f"Задача: {message or 'Опиши файл и предложи что с ним сделать.'}"
        )
    else:
        user_text = message

    contents = list(session["history"]) + [
        {"role": "user", "parts": [{"text": user_text}]}
    ]

    try:
        resp = client.models.generate_content(
            model=MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                temperature=0.2,
            ),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка Gemini: {e}")

    raw_text = (resp.text or "").strip()
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        parsed = {"explanation": raw_text, "python_code": "", "output_filename": ""}

    explanation = (parsed.get("explanation") or "").strip() or "(пустой ответ)"
    code = (parsed.get("python_code") or "").strip()

    session["history"].append({"role": "user", "parts": [{"text": user_text}]})
    session["history"].append({"role": "model", "parts": [{"text": raw_text}]})

    files_out: list[dict[str, str]] = []
    exec_log = ""
    if code:
        success, stdout, stderr, new_files = execute_python(code, workdir)
        files_out = [
            {"filename": f, "session_id": sid}
            for f in new_files
            if not f.startswith("_") and f != session.get("input_file")
        ]
        if not success:
            exec_log = f"\n\n⚠️ Ошибка выполнения:\n{stderr[-2000:]}"
        elif stderr.strip():
            exec_log = f"\n\n(stderr: {stderr.strip()[-300:]})"

    return JSONResponse({
        "text": explanation + exec_log,
        "files": files_out,
        "session_id": sid,
    })


@app.get("/download/{session_id}/{filename}")
def download(session_id: str, filename: str):
    session = SESSIONS.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Сессия не найдена")
    safe = Path(filename).name
    path: Path = session["workdir"] / safe
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Файл не найден")
    return FileResponse(str(path), filename=safe)


@app.post("/reset")
def reset(response: Response, session_id: str | None = Cookie(default=None)):
    if session_id and session_id in SESSIONS:
        sess = SESSIONS.pop(session_id)
        try:
            shutil.rmtree(sess["workdir"])
        except Exception:
            pass
    response.delete_cookie("session_id")
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False)
