// ======================
// Utilidades
// ======================

// Búsqueda en vivo por filas (todas las columnas)
function bindLiveSearch(inputId, tableId) {
  const q = document.getElementById(inputId);
  const tbl = document.getElementById(tableId);
  if (!q || !tbl) return;
  const tbody = tbl.tBodies[0];
  q.addEventListener("input", () => {
    const needle = q.value.trim().toLowerCase();
    for (const tr of tbody.rows) {
      const hay = tr.innerText.toLowerCase().includes(needle);
      tr.style.display = hay ? "" : "none";
    }
  });
}

// Formateo/parseo de números para el total
function parseNumber(x) {
  if (typeof x === "number") return x;
  // elimina cualquier separador o símbolo y deja solo número/decimal/signo
  return parseFloat(String(x).replace(/[^\d.-]/g, "")) || 0;
}
function formatCOP(n) {
  try {
    return Number(n).toLocaleString("es-CO");
  } catch {
    return String(n);
  }
}

// ======================
// Overlay de progreso (para formularios con clase .js-progress-on-submit)
// ======================
const ProgressOverlay = (() => {
  let el = null;

  function ensure() {
    if (el) return el;
    // Usamos el overlay que ya viene en base.html
    el = document.getElementById("progress-overlay");
    // Si no existe, creamos uno básico como fallback
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

  function show() {
    const o = ensure();
    // En tu CSS .progress-overlay.show { display:grid }
    o.classList.add("show");
  }
  function hide() {
    const o = ensure();
    o.classList.remove("show");
  }

  // Ocultar overlay al (re)cargar cualquier página, incluso si se viene de history
  window.addEventListener("pageshow", hide);

  // Exponer API
  return { show, hide };
})();

// Hook de envío seguro: solo muestra overlay si el submit NO fue cancelado
// Solo formularios que NO pidan manejo manual del overlay
function bindProgressForms() {
  document.querySelectorAll('form.js-progress-on-submit:not([data-overlay="manual"])')
    .forEach((form) => {
      form.addEventListener('submit', (e) => {
        if (e.defaultPrevented) return;
        if (typeof form.checkValidity === 'function' && !form.checkValidity()) {
          e.preventDefault();
          return;
        }
        ProgressOverlay.show();
      });
    });
}

// (opcional) expone para uso manual
window.ProgressOverlay = ProgressOverlay;


// ======================
// Pago por lote (selección múltiple)
// ======================
function bindLotePago() {
  const tbl = document.getElementById("tabla-facturas");
  const panel = document.getElementById("panel-lote");          // panel superior (dentro de <form id="form-lote">)
  const idsInput = document.getElementById("ids-lote");
  const provSpan = document.getElementById("lote-prov");
  const cntSpan = document.getElementById("lote-cnt");
  const totSpan = document.getElementById("lote-total");
  const btn = document.getElementById("btn-lote");

  // Flotante lateral (si existe en tu HTML; si no, se ignora)
  const floatBox = document.getElementById("lote-float");
  const lfProv = document.getElementById("lf-prov");
  const lfCnt = document.getElementById("lf-cnt");
  const lfTot = document.getElementById("lf-total");

  if (!tbl || !panel || !idsInput || !provSpan || !cntSpan || !totSpan || !btn) {
    // No estamos en la vista de pendientes
    return;
  }

  function pintarFilas() {
    tbl.querySelectorAll("input.cb-fact").forEach((cb) => {
      const tr = cb.closest("tr");
      if (tr) tr.classList.toggle("row-selected", cb.checked);
    });
  }

  function recompute(lastTarget) {
    const checks = Array.from(tbl.querySelectorAll("input.cb-fact:checked"));

    // Sin selección: oculta panel/flotante y limpia datos
    if (!checks.length) {
      panel.style.display = "none";
      if (floatBox) floatBox.style.display = "none";
      idsInput.value = "";
      provSpan.textContent = lfProv ? (lfProv.textContent = "—") : "—";
      cntSpan.textContent = lfCnt ? (lfCnt.textContent = "0") : "0";
      totSpan.textContent = lfTot ? (lfTot.textContent = "0") : "0";
      pintarFilas();
      return;
    }

    // Validación de mismo proveedor
    const prov0 = checks[0].dataset.proveedor;
    const provName = checks[0].dataset.proveedorNombre || "—";
    let same = true;
    let total = 0;

    for (const cb of checks) {
      total += parseNumber(cb.dataset.saldo);
      if (cb.dataset.proveedor !== prov0) same = false;
    }

    if (!same) {
      // Revertimos el último click y avisamos
      if (lastTarget && lastTarget.checked) lastTarget.checked = false;
      alert("Solo puedes seleccionar facturas del MISMO proveedor.");
      return recompute();
    }

    // Panel superior: solo si hay 2 o más
    panel.style.display = checks.length >= 2 ? "flex" : "none";

    // Flotante lateral: si existe, muéstralo siempre que haya 1 o más
    if (floatBox) floatBox.style.display = "block";

    // Actualizar datos (panel + flotante si aplica)
    provSpan.textContent = provName;
    cntSpan.textContent = String(checks.length);
    totSpan.textContent = formatCOP(total);
    if (lfProv) lfProv.textContent = provName;
    if (lfCnt) lfCnt.textContent = String(checks.length);
    if (lfTot) lfTot.textContent = formatCOP(total);

    // IDs para el form
    idsInput.value = checks.map((cb) => cb.value).join(",");

    // Habilitar botón
    btn.disabled = false;
    btn.title = "";

    pintarFilas();
  }

  // Listeners (change/click por seguridad con distintos navegadores)
  tbl.addEventListener("change", (e) => {
    if (e.target && e.target.classList.contains("cb-fact")) recompute(e.target);
  });
  tbl.addEventListener("click", (e) => {
    if (e.target && e.target.classList.contains("cb-fact")) recompute(e.target);
  });

  // Recalcular si vuelves con checks ya marcados
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") recompute();
  });

  // Estado inicial
  recompute();
}

// ======================
// Auto-init
// ======================
document.addEventListener("DOMContentLoaded", () => {
  console.log("cartera/app.js cargado");
  bindLiveSearch("q-facturas", "tabla-facturas");
  bindLiveSearch("q-pagos", "tabla-pagos");
  bindProgressForms();
  bindLotePago();
});

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('input[type="date"]').forEach(el => {
    if (!el.value) el.value = new Date().toISOString().slice(0, 10);
  });
});
