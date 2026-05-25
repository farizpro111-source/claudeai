import os
import uuid
import mimetypes
import tempfile
from typing import Any
from io import BytesIO

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Cookie, Response
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from starlette.background import BackgroundTask

from anthropic import Anthropic

load_dotenv()

MODEL = "claude-opus-4-7"
CODE_EXECUTION_TOOL_TYPE = "code_execution_20250825"
FILES_BETA = "files-api-2025-04-14"
CODE_EXEC_BETA = "code-execution-2025-08-25"

SYSTEM_PROMPT = """Ты — помощник по Excel. Пользователь описывает задачу на русском, иногда прикладывает Excel-файл (.xlsx, .xls, .csv).

Правила:
- Если пользователь приложил файл — обязательно запусти Python через инструмент code_execution: открой файл (pandas / openpyxl), посмотри структуру, сделай нужное преобразование, сохрани результат в новый .xlsx в рабочей директории.
- Используй pandas, openpyxl, xlsxwriter — они уже установлены. Имя итогового файла — короткое и осмысленное (например result.xlsx, отчет.xlsx, sales_pivot.xlsx).
- После выполнения кода кратко по-русски объясни, что сделал и где смотреть результат. Если заметил проблемы в данных (пустые ячейки, дубликаты, странные типы) — скажи.
- Если файла нет, а пользователь просит совет / формулу / макрос — ответь текстом, без запуска кода. Давай чёткие шаги и готовые формулы.
- Будь краток и по делу, без извинений и дисклеймеров."""

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

client = Anthropic()

# session_id -> {"messages": [...], "container_id": str | None}
SESSIONS: dict[str, dict[str, Any]] = {}


def get_session(session_id: str | None) -> tuple[str, dict[str, Any]]:
    if not session_id or session_id not in SESSIONS:
        session_id = uuid.uuid4().hex
        SESSIONS[session_id] = {"messages": [], "container_id": None}
    return session_id, SESSIONS[session_id]


def _attr(obj: Any, key: str, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def extract_generated_files(content_blocks: list[Any]) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    seen: set[str] = set()
    for block in content_blocks:
        btype = _attr(block, "type")
        # Result block types from code_execution_20250825 (bash sub-tool)
        if btype not in ("bash_code_execution_tool_result", "code_execution_tool_result"):
            continue
        result = _attr(block, "content")
        if result is None:
            continue
        inner = _attr(result, "content")
        if not inner:
            continue
        for item in inner:
            fid = _attr(item, "file_id")
            if fid and fid not in seen:
                seen.add(fid)
                files.append({"file_id": fid})
    return files


def extract_text(content_blocks: list[Any]) -> str:
    out = []
    for block in content_blocks:
        if _attr(block, "type") == "text":
            t = _attr(block, "text", "")
            if t:
                out.append(t)
    return "\n".join(out).strip()


def lookup_filename(file_id: str) -> tuple[str, str]:
    try:
        meta = client.beta.files.retrieve_metadata(file_id)
        return (
            _attr(meta, "filename") or f"{file_id}.xlsx",
            _attr(meta, "mime_type") or "application/octet-stream",
        )
    except Exception:
        return (f"{file_id}.xlsx", "application/octet-stream")


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
    if not message.strip() and file is None:
        raise HTTPException(status_code=400, detail="Пустое сообщение")

    sid, session = get_session(session_id)
    response.set_cookie("session_id", sid, httponly=True, samesite="lax")

    user_content: list[dict[str, Any]] = []

    if file is not None:
        raw = await file.read()
        if not raw:
            raise HTTPException(status_code=400, detail="Файл пуст")
        mime = file.content_type or mimetypes.guess_type(file.filename or "")[0] or "application/octet-stream"
        uploaded = client.beta.files.upload(
            file=(file.filename or "upload.xlsx", BytesIO(raw), mime),
        )
        user_content.append({"type": "container_upload", "file_id": uploaded.id})

    user_content.append({"type": "text", "text": message or "Обработай прикреплённый файл."})

    session["messages"].append({"role": "user", "content": user_content})

    create_kwargs: dict[str, Any] = {
        "model": MODEL,
        "max_tokens": 8192,
        "system": SYSTEM_PROMPT,
        "tools": [{"type": CODE_EXECUTION_TOOL_TYPE, "name": "code_execution"}],
        "messages": session["messages"],
        "thinking": {"type": "adaptive"},
        "betas": [FILES_BETA, CODE_EXEC_BETA],
    }
    if session["container_id"]:
        create_kwargs["container"] = session["container_id"]

    try:
        with client.beta.messages.stream(**create_kwargs) as stream:
            final = stream.get_final_message()
    except Exception as e:
        session["messages"].pop()
        raise HTTPException(status_code=500, detail=f"Ошибка API: {e}")

    container = getattr(final, "container", None)
    cid = _attr(container, "id")
    if cid:
        session["container_id"] = cid

    assistant_blocks = final.content
    session["messages"].append({"role": "assistant", "content": assistant_blocks})

    text = extract_text(assistant_blocks)
    generated = extract_generated_files(assistant_blocks)
    for g in generated:
        fname, _ = lookup_filename(g["file_id"])
        g["filename"] = fname

    return JSONResponse({
        "text": text or "(пустой ответ)",
        "files": generated,
        "session_id": sid,
    })


@app.get("/download/{file_id}")
def download(file_id: str):
    try:
        filename, mime = lookup_filename(file_id)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(filename)[1])
        tmp.close()
        client.beta.files.download(file_id).write_to_file(tmp.name)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Файл не найден: {e}")

    return FileResponse(
        tmp.name,
        media_type=mime,
        filename=filename,
        background=BackgroundTask(os.unlink, tmp.name),
    )


@app.post("/reset")
def reset(response: Response, session_id: str | None = Cookie(default=None)):
    if session_id and session_id in SESSIONS:
        del SESSIONS[session_id]
    response.delete_cookie("session_id")
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False)
