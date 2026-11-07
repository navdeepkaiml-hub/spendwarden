/* SpendWarden dashboard. No build step. Everything that comes from an agent, a web page or a user is
   treated as hostile text: it is only ever inserted with esc() or textContent. */
(function () {
  "use strict";

  var state = { token: "", tab: "overview", approvalSig: null, mandates: {}, policyFilled: false, grid: null, ledgerGrid: null };
  try { state.token = sessionStorage.getItem("sw_admin") || ""; } catch (e) { /* storage unavailable */ }

  function $(id) { return document.getElementById(id); }
  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function when(epoch) { return epoch ? new Date(epoch * 1000).toLocaleString() : ""; }

  /* ---------- api ---------- */
  function api(path, opts) {
    opts = opts || {};
    var headers = { "Content-Type": "application/json" };
    if (state.token) { headers["X-Admin-Token"] = state.token; }
    return fetch(path, { method: opts.method || "GET", headers: headers, body: opts.body ? JSON.stringify(opts.body) : undefined })
      .then(function (res) {
        if (res.status === 401) { showLogin(); throw new Error("Sign in with the admin token"); }
        return res.json().catch(function () { return {}; }).then(function (body) {
          if (!res.ok) { throw new Error(typeof body.detail === "string" ? body.detail : (res.statusText || "request failed")); }
          return body;
        });
      });
  }
  function banner(message) {
    var el = $("banner");
    if (!message) { el.classList.add("hidden"); return; }
    el.textContent = message;
    el.classList.remove("hidden");
  }
  function showLogin() { $("login").classList.remove("hidden"); }

  /* ---------- overview ---------- */
  function renderTiles(b) {
    var items = [
      ["Daily budget left", b.daily_remaining + " " + b.currency],
      ["Weekly budget left", b.weekly_remaining + " " + b.currency],
      ["Per-purchase cap", b.per_txn_cap + " " + b.currency],
      ["Auto-approved up to", b.auto_approve_limit + " " + b.currency]
    ];
    $("tiles").innerHTML = items.map(function (t) {
      return '<div class="tile"><div class="label">' + esc(t[0]) + '</div><div class="value">' + esc(t[1]) + "</div></div>";
    }).join("");
  }

  function failedChecks(p) {
    return (p.checks || []).filter(function (c) { return !c.ok; }).map(function (c) { return c.rule; });
  }

  function renderApprovals(list) {
    $("approval-count").textContent = list.length ? "(" + list.length + ")" : "";
    var sig = list.map(function (p) { return p.id + ":" + p.status; }).join("|");
    if (sig === state.approvalSig) { return; }
    state.approvalSig = sig;
    if (!list.length) { $("approvals").innerHTML = '<div class="empty">Nothing is waiting for approval.</div>'; return; }
    $("approvals").innerHTML = list.map(function (p) {
      var v = p.verifier || {};
      var task = state.mandates[p.mandate_id];
      var flag = v.injection_detected && v.flagged_text ? '<div class="flag">Flagged text: ' + esc(v.flagged_text) + "</div>" : "";
      var match = typeof v.match_score === "number" ? "Intent match " + v.match_score.toFixed(2) + ". " : "";
      return '<div class="approval"><div class="top"><span>' + esc(p.merchant) + " &middot; " + esc((p.items || []).join(", ")) +
        "</span><span>" + esc(p.amount) + " " + esc(p.currency) + "</span></div>" +
        (task ? '<div class="meta">User asked: ' + esc(task) + "</div>" : "") +
        '<div class="meta">' + esc(match) + esc(p.decision_reason) + "</div>" + flag +
        '<div class="actions"><button class="approve" data-act="approve" data-id="' + esc(p.id) + '">Approve</button>' +
        '<button class="reject" data-act="reject" data-id="' + esc(p.id) + '">Reject</button>' +
        '<button class="secondary" data-act="open" data-id="' + esc(p.id) + '">Details</button></div></div>';
    }).join("");
  }

  function gridMissing(el) {
    el.innerHTML = '<div class="empty">The table library could not be loaded. Check your internet connection and reload.</div>';
  }
  function decisionClass(value) {
    return { ALLOW: "chip-allow", ESCALATE: "chip-escalate", DENY: "chip-deny" }[value] || "";
  }

  function makeGrid() {
    var el = $("grid");
    if (typeof agGrid === "undefined") { gridMissing(el); return; }
    state.grid = agGrid.createGrid(el, {
      columnDefs: [
        { headerName: "Time", valueGetter: function (p) { return p.data.created_at; }, valueFormatter: function (p) { return when(p.value); }, sort: "desc", width: 170 },
        { headerName: "Merchant", field: "merchant", width: 140 },
        { headerName: "Items", valueGetter: function (p) { return (p.data.items || []).join(", "); }, width: 170 },
        { headerName: "Amount", valueGetter: function (p) { return parseFloat(p.data.amount); }, valueFormatter: function (p) { return p.data.amount + " " + p.data.currency; }, width: 110 },
        { headerName: "Decision", field: "decision", width: 110, cellClass: function (p) { return decisionClass(p.value); } },
        { headerName: "Status", field: "status", width: 160 },
        { headerName: "Match", valueGetter: function (p) { return p.data.verifier && typeof p.data.verifier.match_score === "number" ? p.data.verifier.match_score : null; }, valueFormatter: function (p) { return p.value == null ? "" : p.value.toFixed(2); }, width: 90 },
        { headerName: "Caught by", valueGetter: function (p) { return failedChecks(p.data).join(", "); }, width: 200 },
        { headerName: "Reason", field: "decision_reason", flex: 1, minWidth: 260, tooltipField: "decision_reason" }
      ],
      defaultColDef: { sortable: true, resizable: true, filter: true },
      getRowId: function (p) { return p.data.id; },
      rowData: [],
      tooltipShowDelay: 300,
      onRowClicked: function (e) { openDetail(e.data.id); }
    });
  }

  function makeLedgerGrid() {
    var el = $("ledger-grid");
    if (typeof agGrid === "undefined") { gridMissing(el); return; }
    state.ledgerGrid = agGrid.createGrid(el, {
      columnDefs: [
        { headerName: "#", field: "seq", width: 80, sort: "desc" },
        { headerName: "Time", valueGetter: function (p) { return p.data.ts; }, valueFormatter: function (p) { return when(p.value); }, width: 180 },
        { headerName: "Event", field: "event", width: 170 },
        { headerName: "Purchase", field: "purchase_id", width: 160 },
        { headerName: "Hash", valueGetter: function (p) { return p.data.hash.slice(0, 16); }, width: 170 },
        { headerName: "Previous hash", valueGetter: function (p) { return p.data.prev_hash.slice(0, 16); }, width: 170 }
      ],
      defaultColDef: { sortable: true, resizable: true, filter: true },
      getRowId: function (p) { return String(p.data.seq); },
      rowData: [],
      onRowClicked: function (e) { if (e.data.purchase_id) { openDetail(e.data.purchase_id); } }
    });
  }

  function setRows(grid, rows) { if (grid) { grid.setGridOption("rowData", rows); } }

  function renderChain(v) {
    var pill = $("pill-chain");
    if (v.ok) {
      pill.textContent = "ledger verified (" + v.entries + " entries)";
      pill.className = "pill ok";
      $("chain-detail").textContent = "Every entry's hash matches the one stored after it. " + v.entries + " entries verified.";
    } else {
      pill.textContent = "ledger TAMPERED at entry " + v.broken_at;
      pill.className = "pill bad";
      $("chain-detail").textContent = "The chain breaks at entry " + v.broken_at + ": a past entry was changed or removed.";
    }
  }

  /* ---------- detail drawer ---------- */
  function checkRow(c) {
    var cls = c.skipped ? "skip" : (c.ok ? "pass" : "fail");
    var mark = c.skipped ? "-" : (c.ok ? "\u2713" : "\u2717");
    return '<div class="check-row ' + cls + '"><span class="mark">' + mark + "</span><span><b>" + esc(c.rule) + "</b>: " + esc(c.detail) + "</span></div>";
  }

  function openDetail(id) {
    api("/v1/purchases/" + encodeURIComponent(id) + "/receipt").then(function (r) {
      var p = r.purchase, v = p.verifier, html = "";
      html += '<div class="big">' + esc(p.amount) + " " + esc(p.currency) + " at " + esc(p.merchant) + "</div>";
      html += "<p>" + esc((p.items || []).join(", ")) + "</p>";
      html += "<p><b>" + esc(p.decision) + "</b> &middot; " + esc(p.status) + "</p>";
      html += "<h3>User asked</h3><p>" + (r.mandate ? esc(r.mandate.task) : "No mandate attached") + "</p>";
      html += "<h3>Agent said</h3><p>" + esc(p.rationale || "(no rationale)") + "</p>";
      html += "<h3>Why this decision</h3><p>" + esc(p.decision_reason) + "</p>";
      html += "<h3>Checks</h3>" + ((p.checks || []).map(checkRow).join("") || "<p>None recorded.</p>");
      if (v && !v.error) {
        html += "<h3>Intent verifier</h3><div class='bar'><span style='width:" + Math.round(v.match_score * 100) + "%'></span></div>";
        html += "<p>Match score " + esc(v.match_score.toFixed(2)) + (v.injection_detected ? " &middot; <b>prompt injection suspected</b>" : "") + "</p>";
        if (v.flagged_text) { html += "<p>Flagged text: " + esc(v.flagged_text) + "</p>"; }
        (v.reasons || []).forEach(function (reason) { html += "<p>" + esc(reason) + "</p>"; });
      } else if (v && v.error) {
        html += "<h3>Intent verifier</h3><p>Unavailable: " + esc(v.error) + "</p>";
      }
      if (p.paypal_order_id) {
        var simulated = String(p.paypal_order_id).indexOf("SIM-") === 0;
        html += "<h3>" + (simulated ? "Payment (simulated)" : "PayPal (sandbox)") + "</h3><p class='mono'>order " + esc(p.paypal_order_id) + "<br>capture " + esc(p.paypal_capture_id || "-") + "</p>";
      }
      if (p.error) { html += "<h3>Error</h3><p>" + esc(p.error) + "</p>"; }
      html += "<h3>Audit receipt " + (r.chain.ok ? "(chain verified)" : "(CHAIN BROKEN)") + "</h3>";
      html += r.entries.map(function (e) {
        return "<p><b>#" + esc(e.seq) + " " + esc(e.event) + "</b><br><span class='mono'>" + esc(e.hash.slice(0, 24)) + " &lt;- " + esc(e.prev_hash.slice(0, 24)) + "</span></p>";
      }).join("");
      if (p.status === "AWAITING_APPROVAL") {
        html += '<div class="actions"><button class="approve" data-act="approve" data-id="' + esc(p.id) + '">Approve</button>' +
                '<button class="reject" data-act="reject" data-id="' + esc(p.id) + '">Reject</button></div>';
      }
      $("drawer-body").innerHTML = html;
      $("drawer").classList.remove("hidden");
    }).catch(function (e) { banner(e.message); });
  }

  function act(kind, id) {
    return api("/v1/purchases/" + encodeURIComponent(id) + "/" + kind, { method: "POST" })
      .then(function () { banner(""); $("drawer").classList.add("hidden"); state.approvalSig = null; return refresh(); })
      .catch(function (e) { banner(e.message); });
  }

  /* ---------- policy & mandates ---------- */
  function fillPolicy(p) {
    $("pol-currency").value = p.currency;
    $("pol-cap").value = p.per_txn_cap;
    $("pol-auto").value = p.auto_approve_limit;
    $("pol-daily").value = p.daily_budget;
    $("pol-weekly").value = p.weekly_budget;
    $("pol-allow").value = (p.merchant_allowlist || []).join(", ");
    $("pol-vmax").value = p.velocity_max_purchases;
    $("pol-vwin").value = p.velocity_window_seconds;
    $("pol-esc").value = p.verifier_escalate_below;
    $("pol-deny").value = p.verifier_deny_below;
    $("pol-req").checked = !!p.require_mandate;
  }
  function readPolicy() {
    return {
      currency: $("pol-currency").value.trim(),
      per_txn_cap: $("pol-cap").value.trim(),
      auto_approve_limit: $("pol-auto").value.trim(),
      daily_budget: $("pol-daily").value.trim(),
      weekly_budget: $("pol-weekly").value.trim(),
      merchant_allowlist: $("pol-allow").value.split(",").map(function (s) { return s.trim(); }).filter(Boolean),
      velocity_max_purchases: parseInt($("pol-vmax").value, 10),
      velocity_window_seconds: parseInt($("pol-vwin").value, 10),
      verifier_escalate_below: parseFloat($("pol-esc").value),
      verifier_deny_below: parseFloat($("pol-deny").value),
      require_mandate: $("pol-req").checked
    };
  }
  function message(id, text, ok) { var el = $(id); el.textContent = text; el.className = "msg " + (ok ? "ok" : "bad"); }

  function renderMandates(list) {
    state.mandates = {};
    list.forEach(function (m) { state.mandates[m.id] = m.task; });
    $("mandates").innerHTML = list.length ? list.map(function (m) {
      return "<li>" + esc(m.task) + "<br><small>" + esc(m.id) + " &middot; " + esc(when(m.created_at)) + "</small></li>";
    }).join("") : '<li class="empty">No mandates yet.</li>';
  }

  /* ---------- refresh loop ---------- */
  function refresh() {
    return api("/health").then(function (h) {
      $("pill-mode").textContent = "payments: " + h.payment_mode;
      $("pill-ver").textContent = "verifier: " + h.verifier_mode;
      $("demo-bar").classList.toggle("hidden", !h.demo_mode);
      if (h.auth_required && !state.token) { showLogin(); throw new Error("Sign in with the admin token"); }
      var calls = [api("/v1/purchases?limit=200"), api("/v1/approvals"), api("/v1/budget"), api("/v1/audit/verify"), api("/v1/mandates")];
      if (state.tab === "ledger") { calls.push(api("/v1/audit?limit=300")); }
      if (state.tab === "policy" && !state.policyFilled) { calls.push(api("/v1/policy")); }
      return Promise.all(calls);
    }).then(function (r) {
      banner("");
      renderMandates(r[4]);
      setRows(state.grid, r[0]);
      renderApprovals(r[1]);
      renderTiles(r[2]);
      renderChain(r[3]);
      if (state.tab === "ledger" && r[5]) { setRows(state.ledgerGrid, r[5]); }
      if (state.tab === "policy" && !state.policyFilled && r[5]) { fillPolicy(r[5]); state.policyFilled = true; }
    }).catch(function (e) { if (e.message !== "Sign in with the admin token") { banner("Cannot reach the server: " + e.message); } });
  }

  /* ---------- wiring ---------- */
  function init() {
    makeGrid();
    makeLedgerGrid();

    document.querySelectorAll(".tab").forEach(function (btn) {
      btn.addEventListener("click", function () {
        state.tab = btn.getAttribute("data-tab");
        document.querySelectorAll(".tab").forEach(function (b) { b.classList.toggle("active", b === btn); });
        ["overview", "policy", "ledger"].forEach(function (t) { $("view-" + t).classList.toggle("hidden", t !== state.tab); });
        refresh();
      });
    });

    document.addEventListener("click", function (e) {
      var t = e.target.closest("[data-act]");
      if (!t) { return; }
      var kind = t.getAttribute("data-act"), id = t.getAttribute("data-id");
      if (kind === "open") { openDetail(id); } else { act(kind, id); }
    });
    $("drawer-close").addEventListener("click", function () { $("drawer").classList.add("hidden"); });

    $("policy-form").addEventListener("submit", function (e) {
      e.preventDefault();
      api("/v1/policy", { method: "PUT", body: readPolicy() })
        .then(function (saved) { fillPolicy(saved); message("policy-msg", "Saved. New purchases use this policy.", true); })
        .catch(function (err) { message("policy-msg", err.message, false); });
    });
    $("mandate-form").addEventListener("submit", function (e) {
      e.preventDefault();
      api("/v1/mandates", { method: "POST", body: { task: $("mandate-task").value } })
        .then(function () { $("mandate-task").value = ""; message("mandate-msg", "Mandate created.", true); return refresh(); })
        .catch(function (err) { message("mandate-msg", err.message, false); });
    });
    $("login-form").addEventListener("submit", function (e) {
      e.preventDefault();
      state.token = $("login-token").value;
      try { sessionStorage.setItem("sw_admin", state.token); } catch (err) { /* ignore */ }
      $("login").classList.add("hidden");
      refresh();
    });

    $("demo-run").addEventListener("click", function () {
      message("demo-msg", "Running the scenes...", true);
      api("/v1/demo/run", { method: "POST" })
        .then(function () { message("demo-msg", "Done. Scene C is waiting under 'Waiting for a human'.", true); state.approvalSig = null; return refresh(); })
        .catch(function (err) { message("demo-msg", err.message, false); });
    });
    $("demo-reset").addEventListener("click", function () {
      api("/v1/demo/reset", { method: "POST" })
        .then(function () { message("demo-msg", "Demo data cleared.", true); state.approvalSig = null; return refresh(); })
        .catch(function (err) { message("demo-msg", err.message, false); });
    });

    refresh();
    setInterval(refresh, 3000);
  }

  if (document.readyState === "loading") { document.addEventListener("DOMContentLoaded", init); } else { init(); }
})();
