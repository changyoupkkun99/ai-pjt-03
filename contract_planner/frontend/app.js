const $ = (selector) => document.querySelector(selector);
let sid = localStorage.getItem("ansim-session") || crypto.randomUUID();
localStorage.setItem("ansim-session", sid);
function rememberSession() {
  const saved = JSON.parse(localStorage.getItem("ansim-sessions") || "[]");
  localStorage.setItem(
    "ansim-sessions",
    JSON.stringify([...new Set([sid, ...saved])]),
  );
}
function chooseSession(id) {
  sid = id;
  localStorage.setItem("ansim-session", sid);
  rememberSession();
  $("#conversation").replaceChildren();
  window.resetReport?.();
}
rememberSession();
let editing = null;
const names = {
  candidates: "법률 후보 검색",
  detail: "원문 조회",
  holidays: "공휴일 확인",
  city: "도시 검색",
  weather: "날씨 확인",
  rag: "법률 색인 검색",
};
const statuses = {
  ready: "점검 일정 제안",
  references: "법률 자료 검토",
  limit: "재계획 상한 도달",
  unverified: "판단 보류",
  clarify: "추가 정보 요청",
  help: "사용 안내",
  no_slot: "가능한 일정 없음",
};
function el(tag, text, cls) {
  const n = document.createElement(tag);
  if (text != null) n.textContent = text;
  if (cls) n.className = cls;
  return n;
}
function action(label, fn) {
  const b = el("button", label);
  b.type = "button";
  b.onclick = async () => {
    b.disabled = true;
    try {
      await fn();
    } catch (e) {
      notice(e.message);
    } finally {
      b.disabled = false;
    }
  };
  return b;
}
function notice(text) {
  $("#notice").textContent = text;
}
async function api(path, method = "GET", body) {
  const r = await fetch("/api/" + path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) {
    let msg;
    try {
      msg = (await r.json()).detail;
    } catch {}
    throw Error(typeof msg === "string" ? msg : "요청을 완료하지 못했습니다.");
  }
  return r.status === 204 ? null : r.json();
}
const plansPath = (id) => "plans" + (id ? "/" + id : "") + "?session_id=" + sid;
async function view(name) {
  for (const s of ["chat", "plans", "logs", "reports"])
    $("#" + s).hidden = s !== name;
  for (const n of document.querySelectorAll("[data-view]")) {
    if (n.dataset.view === name) n.setAttribute("aria-current", "page");
    else n.removeAttribute("aria-current");
  }
  notice("");
  try {
    if (name === "plans") await listPlans();
    if (name === "logs") await listLogs();
    if (name === "reports") await refreshReports();
  } catch (e) {
    notice(e.message);
  }
}
for (const n of document.querySelectorAll("[data-view]"))
  n.onclick = () => view(n.dataset.view);
$(".brand").onclick = () => view("chat");
for (const b of document.querySelectorAll("[data-example]"))
  b.onclick = () => {
    $("#message").value = b.dataset.example;
    $("#message").focus();
  };
function trace(events) {
  const box = el("div");
  for (const e of events) {
    let t = {
      "stage.start": e.label + " 시작",
      "stage.end": e.label + " 완료",
      "tool.request": (names[e.tool] || e.tool) + " 호출",
      "tool.result":
        (names[e.tool] || e.tool) +
        " · " +
        (e.ok ? e.count + "건" : "실패 " + e.code) +
        " · " +
        e.elapsed_ms +
        "ms",
      "tool.reused": names[e.tool] + " · 이전 조회 재사용",
      replan: "재계획 " + e.attempt + "회 · " + e.day,
      constraint: e.reason,
      decision: e.reason,
      "llm.decision": e.actor + " · " + e.decision,
      "llm.unavailable": e.code,
      "plan.selected": (e.tools || []).map((x) => names[x]).join(" → "),
    }[e.event];
    if (t)
      box.append(
        el(
          "div",
          e.seq + ". " + t,
          "trace" + (e.event === "replan" || e.ok === false ? " warn" : ""),
        ),
      );
  }
  return box;
}
function details(title, content) {
  const d = el("details");
  d.append(el("summary", title), content);
  return d;
}
function render(r) {
  const a = el("article");
  a.append(
    el("h2", statuses[r.status] || r.status),
    el("p", r.answer, "answer"),
  );
  if (r.draft.title) {
    const d = r.draft,
      b = el("div", null, "slot");
    b.append(
      el("strong", d.day + " · " + d.start + "–" + d.end),
      el("p", d.title),
    );
    const save = action("이 일정 저장", async () => {
      const result = await api(plansPath(), "POST", d);
      save.remove();
      b.append(el("p", savedText(result)));
      if (!$("#plans").hidden) await listPlans();
    });
    b.append(save);
    a.append(b);
  }
  for (const e of r.errors)
    a.append(
      el(
        "p",
        (names[e.tool] || e.tool) + " · " + e.message + " (" + e.code + ")",
        "warn",
      ),
    );
  for (const x of r.rejected)
    a.append(el("p", x.day + " 제외 · " + x.reason, "minor"));
  if (r.sources.length) {
    const box = el("div");
    for (const d of r.sources) {
      const p = el("p"),
        link = el(
          "a",
          (d.citation ? "[근거 " + d.citation + "] " : "") + d.title,
        );
      if (/^https:\/\//.test(d.url)) {
        link.href = d.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
      }
      p.append(link, el("small", " · " + d.basis));
      if (d.text) p.append(el("div", d.text, "minor"));
      box.append(p);
    }
    a.append(details("근거 자료 " + r.sources.length + "건", box));
  }
  a.append(
    details("실행 과정 · 재계획 " + r.attempts + "회", trace(r.events)),
    el("small", r.disclaimer),
  );
  $("#conversation").append(a);
}
$("#ask").onsubmit = async (e) => {
  e.preventDefault();
  const input = $("#message"),
    text = input.value.trim();
  if (!text || $("#send").disabled) return;
  $("#conversation").append(el("p", text, "user"));
  input.value = "";
  $("#send").disabled = true;
  $("#reset").disabled = true;
  $("#send").textContent = "점검 중…";
  notice("");
  try {
    render(await api("chat", "POST", { session_id: sid, message: text }));
  } catch (err) {
    notice(err.message);
    input.value = text;
  } finally {
    $("#send").disabled = false;
    $("#reset").disabled = false;
    $("#send").textContent = "계획 요청 ↗";
  }
};
$("#reset").onclick = () => {
  chooseSession(crypto.randomUUID());
  notice("새 대화를 시작했습니다.");
};
function savedText(r) {
  return r.conflicts.length
    ? "저장했습니다. 시간이 겹치는 일정 · " +
        r.conflicts.map((x) => x.title).join(", ")
    : "일정을 저장했습니다.";
}
async function listPlans() {
  const rows = await api(plansPath()),
    box = $("#planList");
  box.replaceChildren();
  const saved = JSON.parse(localStorage.getItem("ansim-sessions") || "[]");
  if (saved.length > 1) {
    const label = el("label", "대화 선택"),
      select = el("select");
    for (const id of saved) {
      const option = el("option", "대화 " + id.slice(0, 8));
      option.value = id;
      option.selected = id === sid;
      select.append(option);
    }
    select.onchange = async () => {
      chooseSession(select.value);
      await listPlans();
    };
    label.append(select);
    box.append(label);
  }
  if (!rows.length)
    box.append(el("p", "이 대화에서 저장한 일정이 없습니다.", "empty"));
  for (const row of rows) {
    const a = el("article"),
      buttons = el("div", null, "plan-buttons");
    a.append(
      el("small", row.day + " · " + row.start + "–" + row.end),
      el("h2", row.title),
      el("p", row.notes),
    );
    buttons.append(
      action("수정", async () => {
        const d = await api(plansPath(row.id));
        editing = row.id;
        $("#edit h2").textContent = "일정 수정";
        $("#edit .primary").textContent = "수정 저장";
        for (const k of ["title", "day", "start", "end", "notes"])
          $("#edit").elements[k].value = d[k];
        $("#editor").showModal();
      }),
      action("삭제", async () => {
        if (confirm("이 일정을 삭제할까요?")) {
          await api(plansPath(row.id), "DELETE");
          await listPlans();
          notice("일정을 삭제했습니다.");
        }
      }),
    );
    a.append(buttons);
    box.append(a);
  }
}
$("#cancel").onclick = () => $("#editor").close();
$("#edit").onsubmit = async (e) => {
  e.preventDefault();
  try {
    const result = await api(
      plansPath(editing),
      editing === null ? "POST" : "PUT",
      Object.fromEntries(new FormData(e.target)),
    );
    $("#editor").close();
    await view("plans");
    notice(savedText(result));
  } catch (err) {
    alert(err.message);
  }
};
async function listLogs() {
  const rows = await api("logs"),
    box = $("#logList");
  box.replaceChildren();
  if (!rows.length)
    box.append(el("p", "저장된 실행 기록이 없습니다.", "empty"));
  for (const r of rows) {
    const a = el("article");
    a.append(
      el("h2", "대화 " + r.session_id.slice(0, 8)),
      el("p", r.events + "개 이벤트"),
      action("기록 열기", async () => {
        const events = await api("logs/" + r.session_id);
        a.replaceChildren(
          el("h2", "대화 " + r.session_id.slice(0, 8)),
          trace(events),
        );
      }),
    );
    box.append(a);
  }
}
$("#loadLogs").onclick = () => view("logs");
$("#clearLogs").onclick = async () => {
  if (confirm("현재 대화의 실행 기록을 삭제할까요?"))
    try {
      await api("logs/" + sid, "DELETE");
      await listLogs();
    } catch (e) {
      notice(e.message);
    }
};
api("status")
  .then((s) => {
    $("#mode").textContent =
      (s.data_mode === "snapshot"
        ? "저장 자료 모드"
        : s.data_mode === "mixed"
          ? "법률 수집본 · 일정 실시간"
          : "실시간 조회") +
      " · " +
      (s.provider === "gms" ? "SSAFY GMS · " + s.model : s.provider);
    const reportAi = $("#report-ai");
    reportAi.defaultChecked = s.provider === "gms";
  })
  .catch(() => notice("서버 연결을 확인하세요."));
