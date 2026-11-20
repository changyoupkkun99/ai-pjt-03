let currentReport = null;
let reportGeneration = 0;
let photoRequest = null;
let previewUrl = "";
const reportLevels = ["확인 불가", "낮음", "보통", "주의", "높음"];
const reportPath = (id) =>
  "reports/" + id + "?session_id=" + encodeURIComponent(sid);
const reportExample = [
  "임차인은 모든 수선비를 부담한다.",
  "잔금일: 2026-11-30",
  "대출 실행 예정일: 2026-12-02",
  "근저당 설정 내역은 등기 확인 후 첨부한다.",
].join("\n");
function showPhotoHelp() {
  const local = $("#photo-engine").value === "local";
  $("#photo-help").textContent = (local ? "이 PC에서 한국어 글자를 읽습니다." : "사진을 선택하면 SSAFY GMS로 전송합니다. 개인정보는 먼저 가려 주세요.") + " JPG · PNG · WebP, 최대 12MB.";
}
$("#photo-engine").onchange = () => {
  clearPhoto();
  showPhotoHelp();
  $("#photo-status").textContent = "선택한 방식으로 판독할 사진을 골라 주세요.";
};

function reportNotice(message) {
  $("#report-status").textContent = message;
}
function reportDirty() {
  if (!currentReport) return false;
  return (
    $("#report-title").value !== currentReport.title ||
    [...$("#report-requests").querySelectorAll("textarea")].some(
      (input, i) => input.value !== currentReport.findings[i].request,
    )
  );
}
function risk(item) {
  return el(
    "span",
    item.level,
    "risk-tag risk-" + reportLevels.indexOf(item.level),
  );
}
function clearResults() {
  currentReport = null;
  $("#report-edit").hidden = true;
  $("#report-empty").hidden = false;
  $("#report-findings").replaceChildren();
  $("#report-requests").replaceChildren();
  $("#print-report").replaceChildren();
  reportNotice("");
}
function clearPhoto(keepSelection = false) {
  photoRequest?.abort();
  photoRequest = null;
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = "";
  $("#photo-preview").removeAttribute("src");
  $("#photo-preview").hidden = true;
  if (!keepSelection) $("#report-photo").value = "";
  $("#photo-status").textContent = "";
  $("#report-clauses").readOnly = false;
  $("#report-analyze").disabled = false;
}
window.resetReport = () => {
  reportGeneration++;
  clearResults();
  clearPhoto();
  $("#report-input").reset();
  showPhotoHelp();
  $("#report-clauses").setCustomValidity("");
};
$("#report-photo").onchange = async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  const field = $("#report-clauses");
  if (file.size > 12 * 1024 * 1024) {
    event.target.value = "";
    $("#photo-status").textContent = "사진은 12MB까지 올릴 수 있습니다.";
    return;
  }
  if (field.value.trim() && !confirm("사진이 읽히면 현재 입력란을 인식한 문구로 교체할까요?")) {
    event.target.value = "";
    return;
  }
  ++reportGeneration;
  clearPhoto(true);
  previewUrl = URL.createObjectURL(file);
  $("#photo-preview").src = previewUrl;
  $("#photo-preview").hidden = false;
  const request = new AbortController();
  photoRequest = request;
  field.readOnly = true;
  $("#report-analyze").disabled = true;
  const status = $("#photo-status");
  const provider = $("#photo-engine").value;
  status.textContent = (provider === "gms" ? "SSAFY GMS" : "Windows OCR") + "에서 계약 문구를 읽고 있습니다…";
  try {
    const response = await fetch("/api/reports/ocr?provider=" + provider, { method: "POST", body: file, signal: request.signal });
    const data = await response.json();
    if (photoRequest !== request) return;
    if (!response.ok) throw new Error(data.detail || "사진을 읽을 수 없습니다.");
    clearResults();
    $("#report-picker").value = "";
    field.value = data.text;
    field.readOnly = false;
    field.dispatchEvent(new Event("input"));
    status.textContent = (data.provider === "gms" ? "SSAFY GMS · " + data.model : "Windows OCR") + "에서 문구를 읽었습니다. 금액·날짜·판독 불가 부분을 원본과 대조한 뒤 분석하세요.";
    if (!field.checkValidity()) status.textContent += " " + field.validationMessage;
  } catch (error) {
    if (photoRequest === request) {
      $("#report-photo").value = "";
      status.textContent = error.message + (field.value.trim() ? " 기존 입력 문구를 유지했습니다." : "");
    }
  } finally {
    if (photoRequest === request) {
      photoRequest = null;
      field.readOnly = false;
      $("#report-analyze").disabled = false;
    }
  }
};
async function refreshReports() {
  const session = sid;
  const rows = await api("reports?session_id=" + session);
  if (session !== sid) return;
  const picker = $("#report-picker");
  picker.replaceChildren(new Option("새 보고서", ""));
  for (const row of rows) {
    const when = new Date(row.updated_at).toLocaleString("ko-KR", {
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
    picker.add(
      new Option(
        (row.revision ? "" : "[초안] ") + row.title + " · " + when,
        row.id,
      ),
    );
  }
  picker.value = currentReport?.id || "";
}
function renderReport(report) {
  currentReport = report;
  $("#report-clauses").value = report.original;
  $("#report-clauses").setCustomValidity("");
  $("#report-title").value = report.title;
  $("#report-empty").hidden = true;
  $("#report-edit").hidden = false;
  const findings = $("#report-findings"),
    requests = $("#report-requests");
  findings.replaceChildren();
  requests.replaceChildren();
  const highest = Math.max(
    ...report.findings.map((f) => reportLevels.indexOf(f.level)),
  );
  const unverified = $("#report-unverified");
  unverified.textContent = "일부 항목은 원본 판독 또는 추가 자료가 필요해 검증하지 못했습니다.";
  unverified.hidden = !report.unverified;
  findings.append(
    el("h3", "항목별 점검 결과"),
    el(
      "p",
      report.findings.length
        ? "검증된 항목 " + report.findings.length + "건 · 최고 단계 " + reportLevels[highest]
        : "입력 문구에서 검증 가능한 항목을 찾지 못했습니다.",
      "report-summary",
    ),
  );
  report.findings.forEach((item, i) => {
    const card = el("div", null, "finding");
    const heading = el("div", null, "finding-head");
    heading.append(risk(item), el("strong", item.title));
    card.append(heading, el("blockquote", item.quote), el("p", item.reason));
    if (item.source) {
      const s = item.source,
        inner = el("div");
      const link = el("a", "국가법령정보센터 원문 ↗");
      link.href = s.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      inner.append(
        el("p", s.text),
        el("small", s.collected + " 수집 · "),
        link,
      );
      card.append(details(s.title, inner));
    }
    findings.append(card);
    const wrapper = el("div", null, "request-item");
    const label = el("label", item.title);
    label.htmlFor = "request-" + i;
    const input = el("textarea");
    input.id = label.htmlFor;
    input.value = item.request;
    input.maxLength = 2000;
    input.required = true;
    wrapper.append(
      label,
      risk(item),
      el("p", item.reason, "minor"),
      el(
        "small",
        item.level === "낮음"
            ? "확인 요청"
            : "확인·수정 요청",
      ),
      input,
    );
    requests.append(wrapper);
  });
  if (report.explanation)
    findings.append(
      details("AI 추가 설명", el("p", report.explanation, "answer")),
    );
  $("#report-edit")
    .querySelectorAll("button")
    .forEach((b) => (b.disabled = false));
}
function withReportErrors(fn) {
  return async (event) => {
    try {
      await fn(event);
    } catch (error) {
      reportNotice(error.message);
    }
  };
}
$("#report-picker").onchange = withReportErrors(async (e) => {
  const id = e.target.value;
  if (reportDirty() && !confirm("편집 중인 요청 문구를 닫을까요?")) {
    e.target.value = currentReport.id;
    return;
  }
  const generation = ++reportGeneration;
  clearPhoto();
  if (!id) {
    window.resetReport();
    return;
  }
  const report = await api(reportPath(id));
  if (generation === reportGeneration) {
    renderReport(report);
    reportNotice("");
  }
});
$("#report-example").onclick = () => {
  if (
    $("#report-clauses").value.trim() &&
    !confirm("입력란에 예시 계약을 넣을까요?")
  )
    return;
  $("#report-clauses").value = reportExample;
  clearPhoto();
  $("#report-clauses").dispatchEvent(new Event("input"));
};
$("#report-clauses").oninput = () => {
  const field = $("#report-clauses");
  const tooLong = field.value.length > field.maxLength || field.value.split(/\r?\n/).filter(line => line.trim()).length > 160;
  field.setCustomValidity(tooLong ? "검토할 부분을 12,000자와 160줄 이내로 줄여 주세요." : "");
  if (!currentReport) return;
  const stale = $("#report-clauses").value.trim() !== currentReport.original;
  $("#report-edit")
    .querySelectorAll("button")
    .forEach((b) => (b.disabled = stale));
  reportNotice(
    stale ? "원문을 변경했습니다. 다시 분석하면 새 초안을 만듭니다." : "",
  );
};
$("#report-input").onsubmit = withReportErrors(async (event) => {
  event.preventDefault();
  if (reportDirty() && !confirm("현재 수정 내용을 닫고 새 초안을 만들까요?"))
    return;
  const generation = ++reportGeneration;
  const button = $("#report-analyze");
  button.disabled = true;
  reportNotice("계약 문구와 참고 조문을 확인하고 있습니다.");
  try {
    const report = await api("reports", "POST", {
      session_id: sid,
      clauses: $("#report-clauses").value,
      ai: $("#report-ai").checked,
    });
    if (generation !== reportGeneration) return;
    renderReport(report);
    await refreshReports();
    reportNotice(
      "초안을 만들었습니다. 오른쪽에서 요청 문구를 편집할 수 있습니다.",
    );
  } finally {
    button.disabled = Boolean(photoRequest);
  }
});
$("#report-edit").onsubmit = withReportErrors(async (event) => {
  event.preventDefault();
  const report = currentReport;
  const button = event.submitter;
  button.disabled = true;
  try {
    const saved = await api(reportPath(report.id), "PUT", {
      title: $("#report-title").value,
      revision: report.revision + 1,
      requests: [...$("#report-requests").querySelectorAll("textarea")].map(
        (t) => t.value,
      ),
    });
    if (currentReport !== report) return;
    renderReport(saved);
    await refreshReports();
    reportNotice("보고서를 저장했습니다.");
  } finally {
    button.disabled = false;
  }
});
function preparePrint() {
  if (!currentReport) return;
  const box = $("#print-report");
  box.replaceChildren(
    el("h1", $("#report-title").value),
    el("p", $(".report-scope").textContent),
  );
  if (currentReport.unverified)
    box.append(el("p", $("#report-unverified").textContent));
  currentReport.findings.forEach((item, i) => {
    const section = el("section");
    section.append(
      el("h2", item.title + " · " + item.level),
      el("blockquote", item.quote),
      el("p", item.reason),
    );
    if (item.source)
      section.append(
        el(
          "p",
          item.source.title +
            " · " +
            item.source.collected +
            " 수집\n" +
            item.source.url,
          "minor",
        ),
      );
    section.append(
      el("strong", "상대방에게 요청할 내용"),
      el("p", $("#request-" + i).value),
    );
    box.append(section);
  });
  if (currentReport.explanation)
    box.append(el("h2", "AI 추가 설명"), el("p", currentReport.explanation));
}
window.addEventListener("beforeprint", preparePrint);
$("#report-print").onclick = () => {
  preparePrint();
  window.print();
};
$("#report-follow").onclick = () => {
  editing = null;
  const form = $("#edit");
  form.reset();
  form.querySelector("h2").textContent = "후속 점검 일정";
  form.querySelector(".primary").textContent = "일정 저장";
  form.elements.title.value = "계약 검토 후속 점검";
  form.elements.notes.value = [
    ...$("#report-requests").querySelectorAll("textarea"),
  ]
    .map((x) => x.value)
    .join("\n")
    .slice(0, 2400);
  $("#editor").showModal();
};
if (location.hash === "#reports") view("reports");
