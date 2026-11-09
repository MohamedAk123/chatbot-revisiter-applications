"use strict";

const byId = (id) => document.getElementById(id);
const csrf = document.querySelector('meta[name="csrf-token"]').content;
const conversations = "/api/conversations";

async function request(url, method = "GET", body) {
  const headers = { "X-CSRF-Token": csrf };
  if (body !== undefined && !(body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(body);
  }
  const response = await fetch(url, { method, body, headers });
  if (!response.ok) {
    const result = await response.json().catch(() => ({}));
    throw new Error(result.error || "Le serveur n’a pas pu traiter cette action.");
  }
  return response;
}

async function api(url, method, body) {
  return (await request(url, method, body)).json();
}

function element(tag, css, text) {
  const node = document.createElement(tag);
  if (css) node.className = css;
  if (text !== undefined) node.textContent = text;
  return node;
}

function icon(name) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  const use = document.createElementNS(ns, "use");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  use.setAttribute("href", "#i-" + name);
  svg.append(use);
  return svg;
}

function button(css, label, action, glyph) {
  const node = element("button", css);
  node.type = "button";
  node.append(...(glyph ? [icon(glyph)] : []), element("span", "", label));
  node.addEventListener("click", action);
  return node;
}

async function consume(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let pending = "";
  try {
    while (true) {
      const chunk = await reader.read();
      pending += decoder.decode(chunk.value, { stream: !chunk.done });
      let newline;
      while ((newline = pending.indexOf("\n")) !== -1) {
        const line = pending.slice(0, newline);
        pending = pending.slice(newline + 1);
        if (line) onEvent(JSON.parse(line));
      }
      if (chunk.done) return;
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

function login() {
  byId("login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const submit = form.querySelector("button[type=submit]");
    submit.disabled = true;
    try {
      await api("/api/login", "POST", Object.fromEntries(new FormData(form)));
      location.reload();
    } catch (error) {
      byId("login-error").textContent = error.message;
    } finally {
      submit.disabled = false;
    }
  });
}

function atelier() {
  const question = byId("question");
  const messages = byId("messages");
  const scroll = byId("scroll-area");
  const microphone = byId("mic");
  const voixDisponible = !microphone.disabled;
  let current = null;
  let all = [];
  let busy = false;
  let activeJob = null;
  let opening = 0;
  let recorder = null;
  let recordingPending = false;

  function url(path = "") {
    return conversations + "/" + current.id + path;
  }

  function notice(text = "", error = false) {
    const box = byId("notice");
    box.textContent = text;
    box.hidden = !text;
    box.classList.toggle("error", error);
  }

  function importStatus(text = "") {
    byId("import-status").textContent = text;
    byId("import-status").hidden = !text;
  }

  function atBottom() {
    scroll.scrollTop = scroll.scrollHeight;
  }

  function controls() {
    const readonly = current?.can_edit === false;
    const blocked = busy || recordingPending || recorder !== null;
    byId("send").disabled = blocked || readonly || !question.value.trim();
    byId("send").hidden = !!activeJob;
    byId("stop").hidden = !activeJob;
    byId("attach").disabled = blocked;
    byId("new-chat").disabled = blocked;
    byId("demo").disabled = blocked;
    byId("rename").disabled = blocked;
    microphone.disabled = !voixDisponible || busy || recordingPending || readonly;
    question.readOnly = readonly;
    byId("upload-tools").hidden = readonly;
    for (const control of byId("upload-tools").querySelectorAll("button, input")) {
      control.disabled = blocked;
    }
    for (const item of byId("history").querySelectorAll("button")) {
      item.disabled = blocked;
    }
  }

  function sidebar(open) {
    const mobile = matchMedia("(max-width: 730px)").matches;
    byId("atelier").classList.toggle("sidebar-hidden", !open && !mobile);
    byId("atelier").classList.toggle("sidebar-open", open && mobile);
    byId("sidebar-scrim").hidden = !open || !mobile;
  }

  function renderHistory() {
    const filter = byId("history-search").value.trim().toLocaleLowerCase("fr");
    const filtered = all.filter((item) => item.name.toLocaleLowerCase("fr").includes(filter));
    byId("history-count").textContent = all.length ? String(all.length) : "";
    byId("history-search").hidden = all.length < 3;
    byId("history").replaceChildren(...filtered.map((item) => {
      const entry = button("history-item", item.name, () => openChat(item.id), "chat");
      entry.title = item.name;
      if (item.id === current?.id) entry.setAttribute("aria-current", "true");
      return entry;
    }));
    if (!filtered.length) {
      byId("history").append(element("p", "", all.length ? "Aucun résultat." : "Vos conversations apparaîtront ici."));
    }
    controls();
  }

  async function refreshHistory() {
    all = await api(conversations);
    renderHistory();
  }

  async function openSource(name) {
    if (!current) return;
    const dialog = byId("source-dialog");
    byId("source-title").textContent = name;
    byId("source-code").textContent = "Chargement…";
    dialog.showModal();
    try {
      const source = await api(url("/file?name=" + encodeURIComponent(name)));
      byId("source-code").textContent = source.code;
    } catch (error) {
      byId("source-code").textContent = error.message;
    }
  }

  function download(text, filename) {
    const object = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
    const link = element("a");
    link.href = object;
    link.download = filename;
    link.click();
    setTimeout(() => URL.revokeObjectURL(object), 1000);
  }

  function showSchema(message) {
    const schema = message.schema;
    const card = element("section", "schema-card");
    card.append(element("p", "", schema.titre));
    const list = element("ol");
    schema.noeuds.forEach((node, index) => {
      const numero = index + 1;
      list.append(element("li", "", numero + ". " + node.libelle));
      for (const link of schema.liens.filter((l) => l.de === numero)) {
        const label = "↳ " + link.de + " → " + link.vers + (link.libelle ? " · " + link.libelle : "");
        list.append(element("li", "schema-link", label));
      }
    });
    card.append(list, button("outline-button", "Télécharger le schéma", () => {
      download(message.mermaid, "schema.mmd");
    }, "download"));
    return card;
  }

  function showMessage(message, streaming = false) {
    const user = message.role === "user";
    const article = element("article", "message " + message.role);
    const body = element("div", "message-body");
    article.append(body);
    if (user) {
      body.textContent = message.content;
      return article;
    }
    const mark = element("div", "assistant-mark");
    mark.append(icon("code"));
    article.prepend(mark);
    body.append(element("div", "speaker", "Revisiter"));
    const answer = element("div", streaming ? "answer streaming" : "answer");
    // Seul le rendu Markdown du serveur désactive les balises HTML et médias entrants.
    if (message.html) answer.innerHTML = message.html;
    else answer.textContent = message.content;
    body.append(answer);
    if (streaming) {
      const phase = element("div", "phase", "Préparation de la réponse…");
      phase.setAttribute("role", "status");
      body.append(phase);
      return article;
    }
    if (message.schema?.noeuds?.length) body.append(showSchema(message));
    if (message.sources?.length) {
      const sources = element("div", "source-list");
      sources.setAttribute("aria-label", "Sources");
      for (const source of message.sources) {
        sources.append(button("source-chip", source, () => openSource(source)));
      }
      body.append(sources);
    }
    const actions = element("div", "response-actions");
    const copy = element("button", "icon-button");
    copy.type = "button";
    copy.title = "Copier la réponse";
    copy.setAttribute("aria-label", copy.title);
    copy.append(icon("copy"));
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(message.content);
        notice("Réponse copiée.");
      } catch {
        notice("La copie est bloquée par le navigateur.", true);
      }
    });
    actions.append(copy);
    const elapsed = message.metrics?.total_ms;
    if (Number.isFinite(elapsed)) {
      actions.append(element("span", "details-text", (elapsed / 1000).toFixed(1) + " s"));
    }
    body.append(actions);
    return article;
  }

  function renderFiles() {
    const files = current?.files || [];
    byId("files-count").hidden = !files.length;
    byId("files-count").textContent = String(files.length);
    byId("project-files").replaceChildren(...files.map((name) =>
      button("outline-button", name, () => openSource(name), "file")));
    byId("project-caption").textContent = files.length
      ? files.length + (files.length === 1 ? " fichier joint à la conversation." : " fichiers joints à la conversation.")
      : "Les fichiers joints aident Revisiter à citer le code.";
    byId("export").hidden = !current;
    if (current) byId("export").href = url("/export");
  }

  function render() {
    byId("conversation-title").textContent = current?.name || "Nouvelle conversation";
    byId("rename").hidden = !current?.can_edit;
    const history = current?.messages || [];
    byId("welcome").hidden = !!history.length;
    messages.replaceChildren(...history.map((message) => showMessage(message)));
    renderFiles();
    renderHistory();
    atBottom();
  }

  function selectChat(chat) {
    current = chat;
    history.replaceState(null, "", location.pathname + (chat ? "#" + chat.id : ""));
    render();
  }

  async function openChat(id) {
    if (busy || recorder || recordingPending) return;
    const sequence = ++opening;
    try {
      const chat = await api(conversations + "/" + id);
      if (sequence !== opening || busy) return;
      selectChat(chat);
      notice();
      if (matchMedia("(max-width: 730px)").matches) sidebar(false);
    } catch (error) {
      if (sequence === opening) notice(error.message, true);
    }
  }

  async function ensureChat() {
    if (!current) {
      const chat = await api(conversations, "POST", {});
      selectChat(chat);
    }
  }

  function draft(text) {
    question.value = text;
    controls();
    question.focus();
  }

  async function send(event) {
    event.preventDefault();
    const text = question.value.trim();
    if (!text || busy || recorder || recordingPending || current?.can_edit === false) return;
    busy = true;
    controls();
    notice();
    let completed = false;
    try {
      await ensureChat();
      draft("");
      byId("welcome").hidden = true;
      const preview = showMessage({ role: "assistant", content: "" }, true);
      messages.append(showMessage({ role: "user", content: text }), preview);
      atBottom();
      const response = await request(url("/messages"), "POST", {
        question: text, format: byId("answer-format").value, search: byId("search-mode").value,
      });
      await consume(response, (event) => {
        if (event.type === "start") {
          activeJob = event.id;
          controls();
        } else if (event.type === "state") {
          preview.querySelector(".phase").textContent = event.phase;
        } else if (event.type === "chunk") {
          const pinned = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 100;
          preview.querySelector(".answer").append(document.createTextNode(event.text));
          if (pinned) atBottom();
        } else if (event.type === "error") {
          throw new Error(event.error);
        } else if (event.type === "complete") {
          current.messages.push(...event.messages);
          current.name = event.name;
          completed = true;
        }
      });
      if (!completed) throw new Error("Connexion interrompue. Ouvrez la conversation pour vérifier son état.");
    } catch (error) {
      notice(error.message, true);
      if (!question.value) draft(text);
    } finally {
      busy = false;
      activeJob = null;
      byId("stop").disabled = false;
      render();
      refreshHistory().catch((error) => notice(error.message, true));
    }
  }

  async function upload(data) {
    if (busy || current?.can_edit === false) return;
    busy = true;
    controls();
    importStatus("Import des fichiers…");
    try {
      await ensureChat();
      const result = await api(url("/files"), "POST", data);
      current.files = result.files;
      renderFiles();
      const suffix = result.ignored ? " · " + result.ignored + " écarté(s)" : "";
      const secrets = result.secrets ? " · " + result.secrets + " secret(s) masqué(s)" : "";
      importStatus(result.files.length + " fichier(s) dans la conversation" + suffix + secrets + ".");
      await refreshHistory();
    } catch (error) {
      importStatus(error.message);
    } finally {
      busy = false;
      controls();
    }
  }

  for (const kind of ["files", "folder"]) {
    const input = byId(kind === "files" ? "file-input" : "folder-input");
    byId("pick-" + kind).addEventListener("click", () => input.click());
    input.addEventListener("change", () => {
      const data = new FormData();
      for (const file of input.files) {
        data.append("files", file, file.webkitRelativePath || file.name);
      }
      if (input.files.length) upload(data);
      input.value = "";
    });
  }

  async function record() {
    if (recorder) {
      if (recorder.state === "recording") recorder.stop();
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      notice("Le microphone est indisponible dans ce navigateur.", true);
      return;
    }
    let stream;
    recordingPending = true;
    controls();
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const audio = new MediaRecorder(stream);
      const chunks = [];
      audio.addEventListener("dataavailable", (event) => chunks.push(event.data));
      let timer;
      audio.addEventListener("stop", async () => {
        clearTimeout(timer);
        stream.getTracks().forEach((track) => track.stop());
        recorder = null;
        recordingPending = true;
        microphone.classList.remove("recording");
        microphone.title = "Dicter une question";
        microphone.setAttribute("aria-label", microphone.title);
        controls();
        notice("Transcription locale…");
        try {
          const wav = await wavMono(new Blob(chunks, { type: audio.mimeType }));
          const data = new FormData();
          data.append("audio", wav, "question.wav");
          const result = await api("/api/transcription", "POST", data);
          const text = [question.value.trim(), result.text].filter(Boolean).join(" ");
          if (text.length > question.maxLength) throw new Error("La question transcrite est trop longue.");
          draft(text);
          notice("Transcription prête. Relisez-la avant de l’envoyer.");
        } catch (error) {
          notice(error.message, true);
        } finally {
          recordingPending = false;
          controls();
        }
      });
      audio.start();
      recorder = audio;
      timer = setTimeout(() => {
        if (audio.state === "recording") audio.stop();
      }, 58000);
      microphone.classList.add("recording");
      microphone.title = "Terminer l’enregistrement";
      microphone.setAttribute("aria-label", microphone.title);
      notice("Enregistrement en cours. Cliquez sur le micro pour terminer.");
    } catch {
      stream?.getTracks().forEach((track) => track.stop());
      notice("Le microphone n’a pas pu être ouvert. Vérifiez l’autorisation du navigateur.", true);
    } finally {
      recordingPending = false;
      controls();
    }
  }

  byId("sidebar-open").addEventListener("click", () => sidebar(true));
  for (const id of ["sidebar-close", "sidebar-scrim"]) {
    byId(id).addEventListener("click", () => sidebar(false));
  }
  byId("history-search").addEventListener("input", renderHistory);
  byId("new-chat").addEventListener("click", () => {
    opening++;
    selectChat(null);
    notice();
    draft("");
    if (matchMedia("(max-width: 730px)").matches) sidebar(false);
  });
  byId("demo").addEventListener("click", async () => {
    busy = true;
    controls();
    try {
      selectChat(await api(conversations, "POST", { demo: true }));
      draft("Quel est le résultat de total_panier([10, -3]) ?");
      notice("Atlas est prêt. Envoyez la question proposée ou écrivez la vôtre.");
      await refreshHistory();
    } catch (error) {
      notice(error.message, true);
    } finally {
      busy = false;
      controls();
    }
  });
  for (const suggestion of document.querySelectorAll("[data-prompt]")) {
    suggestion.addEventListener("click", () => draft(suggestion.dataset.prompt));
  }
  for (const id of ["attach", "project-open"]) {
    byId(id).addEventListener("click", () => {
      renderFiles();
      importStatus();
      byId("project-dialog").showModal();
    });
  }
  for (const close of document.querySelectorAll("[data-close]")) {
    close.addEventListener("click", () => close.closest("dialog").close());
  }
  byId("github-form").addEventListener("submit", (event) => {
    event.preventDefault();
    upload({ url: byId("github-url").value, ref: byId("github-ref").value });
  });
  byId("rename").addEventListener("click", () => {
    byId("rename-value").value = current.name;
    byId("rename-dialog").showModal();
  });
  byId("rename-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      current.name = (await api(url(), "PATCH", { name: byId("rename-value").value })).name;
      byId("rename-dialog").close();
      render();
      await refreshHistory();
    } catch (error) {
      byId("rename-value").setCustomValidity(error.message);
      byId("rename-value").reportValidity();
    }
  });
  byId("rename-value").addEventListener("input", (event) => event.target.setCustomValidity(""));
  byId("composer").addEventListener("submit", send);
  question.addEventListener("input", controls);
  question.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      byId("composer").requestSubmit();
    }
  });
  byId("stop").addEventListener("click", async () => {
    byId("stop").disabled = true;
    try {
      await request("/api/executions/" + activeJob, "DELETE");
    } catch (error) {
      notice(error.message, true);
      byId("stop").disabled = false;
    }
  });
  microphone.addEventListener("click", record);
  byId("logout")?.addEventListener("click", async () => {
    try {
      await api("/api/logout", "POST");
      location.reload();
    } catch (error) {
      notice(error.message, true);
    }
  });
  render();
  const id = location.hash.slice(1);
  refreshHistory().then(() => {
    if (all.some((item) => item.id === id)) openChat(id);
  }).catch((error) => notice(error.message, true));
}

async function wavMono(blob) {
  const rate = 16000;
  const context = new OfflineAudioContext(1, 1, rate);
  let audio;
  try {
    audio = await context.decodeAudioData(await blob.arrayBuffer());
  } catch {
    throw new Error("L’enregistrement est illisible. Réessayez avec une question courte.");
  }
  if (audio.duration > 60) throw new Error("L’enregistrement dépasse 60 secondes.");
  const input = audio.getChannelData(0);
  const buffer = new ArrayBuffer(44 + input.length * 2);
  const view = new DataView(buffer);
  const label = (offset, text) => [...text].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)));
  label(0, "RIFF");
  view.setUint32(4, buffer.byteLength - 8, true);
  label(8, "WAVEfmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  label(36, "data");
  view.setUint32(40, input.length * 2, true);
  for (let i = 0; i < input.length; i++) {
    view.setInt16(44 + i * 2, Math.round(Math.max(-1, Math.min(1, input[i])) * 32767), true);
  }
  return new Blob([buffer], { type: "audio/wav" });
}

if (byId("login-form")) login();
else atelier();
