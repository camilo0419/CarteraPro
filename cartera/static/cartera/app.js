// ======================
// Utilidades numéricas (lote)
// ======================
function parseNumber(x) {
  if (typeof x === "number") return x;
  return parseFloat(String(x).replace(/[^\d.-]/g, "")) || 0;
}
function formatCOP(n) {
  try { return Number(n).toLocaleString("es-CO"); }
  catch { return String(n); }
}

// ======================
// Overlay de progreso
// ======================
const ProgressOverlay = (() => {
  let el = null;
  function ensure() {
    if (el) return el;
    el = document.getElementById("progress-overlay");
    if (!el) {
      el = document.createElement("div");
      el.id = "progress-overlay";
      el.className = "progress-overlay";
      el.innerHTML = `
        <div class="progress-box">
          <div>Procesando…</div>
          <div class="progress-bar"><div></div></div>
        </div>`;
      document.body.appendChild(el);
    }
    return el;
  }
  function show(){ ensure().classList.add("show"); }
  function hide(){ ensure().classList.remove("show"); }
  window.addEventListener("pageshow", hide);
  return { show, hide };
})();
window.ProgressOverlay = ProgressOverlay;

function bindProgressForms() {
  document
    .querySelectorAll('form.js-progress-on-submit:not([data-overlay="manual"])')
    .forEach((form) => {
      form.addEventListener("submit", (e) => {
        if (e.defaultPrevented) return;
        if (typeof form.checkValidity === "function" && !form.checkValidity()) {
          e.preventDefault();
          return;
        }
        ProgressOverlay.show();
      });
    });
}

// ======================
// BÚSQUEDA (solo Enter o botón)
// ======================
function submitSearchClearingPage(form, inputName = "q") {
  const url = new URL(window.location.href);

  // 1) quita siempre el page para no quedar atrapado en la página 2+
  url.searchParams.delete("page");

  // 2) setea / limpia ?q
  const qVal = (form.elements[inputName]?.value || "").trim();
  if (qVal) url.searchParams.set("q", qVal);
  else url.searchParams.delete("q");

  // 3) preserva otros hidden del form (ej. prov)
  Array.from(form.elements).forEach((el) => {
    if (el.type === "hidden" && el.name && el.name !== inputName) {
      const v = (el.value || "").trim();
      if (v) url.searchParams.set(el.name, v);
      else url.searchParams.delete(el.name);
    }
  });

  window.location.assign(url.toString());
}

/**
 * Enlaza un input de búsqueda para:
 *  - Enviar al presionar Enter
 *  - Enviar al hacer submit del form (click en "Buscar")
 *  No hay live-search.
 */
function wireSearch(inputId) {
  const input = document.getElementById(inputId);
  if (!input) return;
  const form = input.form || input.closest("form");
  if (!form) return;

  // Enter en el input
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      submitSearchClearingPage(form, input.name || "q");
    }
  });

  // Click en "Buscar" (submit del form)
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    submitSearchClearingPage(form, input.name || "q");
  });
}

// ======================
// Pago por lote
// ======================
function bindLotePago() {
  const tbl = document.getElementById("tabla-facturas");
  const panel = document.getElementById("panel-lote");
  const idsInput = document.getElementById("ids-lote");
  const provSpan = document.getElementById("lote-prov");
  const cntSpan = document.getElementById("lote-cnt");
  const totSpan = document.getElementById("lote-total");
  const btn = document.getElementById("btn-lote");

  const floatBox = document.getElementById("lote-float");
  const lfProv = document.getElementById("lf-prov");
  const lfCnt = document.getElementById("lf-cnt");
  const lfTot = document.getElementById("lf-total");

  if (!tbl || !panel || !idsInput || !provSpan || !cntSpan || !totSpan || !btn) return;

  function pintarFilas() {
    tbl.querySelectorAll("input.cb-fact").forEach((cb) => {
      const tr = cb.closest("tr");
      if (tr) tr.classList.toggle("row-selected", cb.checked);
    });
  }

  function recompute(lastTarget) {
    const checks = Array.from(tbl.querySelectorAll("input.cb-fact:checked"));

    if (!checks.length) {
      panel.style.display = "none";
      if (floatBox) floatBox.style.display = "none";
      idsInput.value = "";
      const set = (el, v) => { if (el) el.textContent = v; };
      set(provSpan, "—"); set(cntSpan, "0"); set(totSpan, "0");
      set(lfProv, "—"); set(lfCnt, "0"); set(lfTot, "0");
      pintarFilas();
      return;
    }

    const prov0 = checks[0].dataset.proveedor;
    const provName = checks[0].dataset.proveedorNombre || "—";
    let same = true;
    let total = 0;

    for (const cb of checks) {
      total += parseNumber(cb.dataset.saldo);
      if (cb.dataset.proveedor !== prov0) same = false;
    }

    if (!same) {
      if (lastTarget && lastTarget.checked) lastTarget.checked = false;
      alert("Solo puedes seleccionar facturas del MISMO proveedor.");
      return recompute();
    }

    panel.style.display = checks.length >= 2 ? "flex" : "none";
    if (floatBox) floatBox.style.display = "block";

    provSpan.textContent = provName;
    cntSpan.textContent = String(checks.length);
    totSpan.textContent = formatCOP(total);
    if (lfProv) lfProv.textContent = provName;
    if (lfCnt) lfCnt.textContent = String(checks.length);
    if (lfTot) lfTot.textContent = formatCOP(total);

    idsInput.value = checks.map((cb) => cb.value).join(",");
    btn.disabled = false;
    btn.title = "";

    pintarFilas();
  }

  tbl.addEventListener("change", (e) => {
    if (e.target && e.target.classList.contains("cb-fact")) recompute(e.target);
  });
  tbl.addEventListener("click", (e) => {
    if (e.target && e.target.classList.contains("cb-fact")) recompute(e.target);
  });

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") recompute();
  });

  recompute();
}

// ======================
// Auto-init
// ======================
document.addEventListener("DOMContentLoaded", () => {
  console.log("cartera/app.js cargado");

  // 1) Buscadores SIN live-search
  wireSearch("q-facturas"); // input de pendientes
  wireSearch("q-pagos");    // input de pagados

  // 2) Overlay de formularios
  bindProgressForms();

  // 3) Selección para pago por lote
  bindLotePago();

  // (opcional) inicializa inputs date vacíos con hoy
  document.querySelectorAll('input[type="date"]').forEach((el) => {
    if (!el.value) el.value = new Date().toISOString().slice(0, 10);
  });
});
