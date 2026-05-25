const chat = document.getElementById("chat");
const form = document.getElementById("composer");
const messageEl = document.getElementById("message");
const fileInput = document.getElementById("file-input");
const fileLabel = document.getElementById("file-label");
const sendBtn = document.getElementById("send-btn");
const resetBtn = document.getElementById("reset-btn");

function addMessage(role, text, opts = {}) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  if (opts.html) {
    div.innerHTML = text;
  } else {
    div.textContent = text;
  }
  if (opts.attachment) {
    const a = document.createElement("div");
    a.className = "attachment";
    a.textContent = `📎 ${opts.attachment}`;
    div.appendChild(a);
  }
  if (opts.files && opts.files.length) {
    const wrap = document.createElement("div");
    wrap.className = "files";
    for (const f of opts.files) {
      const link = document.createElement("a");
      link.className = "file-link";
      link.href = `/download/${encodeURIComponent(f.session_id)}/${encodeURIComponent(f.filename)}`;
      link.textContent = `⬇ ${f.filename}`;
      link.download = f.filename || "";
      wrap.appendChild(link);
    }
    div.appendChild(wrap);
  }
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
  return div;
}

fileInput.addEventListener("change", () => {
  if (fileInput.files.length) {
    fileLabel.textContent = `📎 ${fileInput.files[0].name}`;
    fileLabel.classList.add("has-file");
  } else {
    fileLabel.textContent = "+ Файл";
    fileLabel.classList.remove("has-file");
  }
});

messageEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    form.requestSubmit();
  }
});

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = messageEl.value.trim();
  const file = fileInput.files[0];
  if (!text && !file) return;

  addMessage("user", text || "(без текста)", { attachment: file ? file.name : null });
  messageEl.value = "";
  fileInput.value = "";
  fileLabel.textContent = "+ Файл";
  fileLabel.classList.remove("has-file");

  const pending = addMessage("assistant", "", { html: true });
  pending.innerHTML = '<span class="spinner"></span>Считаю…';

  sendBtn.disabled = true;
  try {
    const fd = new FormData();
    fd.append("message", text);
    if (file) fd.append("file", file);

    const res = await fetch("/chat", { method: "POST", body: fd });
    const data = await res.json();
    pending.remove();

    if (!res.ok) {
      addMessage("error", data.detail || "Ошибка запроса");
    } else {
      addMessage("assistant", data.text, { files: data.files });
    }
  } catch (err) {
    pending.remove();
    addMessage("error", String(err));
  } finally {
    sendBtn.disabled = false;
    messageEl.focus();
  }
});

resetBtn.addEventListener("click", async () => {
  await fetch("/reset", { method: "POST" });
  chat.innerHTML = "";
  addMessage("assistant", "Диалог очищен. Опишите новую задачу или приложите файл.");
});

addMessage("assistant", "Привет! Опишите задачу по Excel или приложите файл — я его обработаю и пришлю готовый результат.");
