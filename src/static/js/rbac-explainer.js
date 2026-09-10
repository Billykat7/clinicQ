/**
 * RBAC explainer: the "why" affordance on role detail, user detail and the 403 page (Issue #175).
 *
 * Talks to /api/v1/admin/rbac:
 *   - POST /simulate                        → one decision, its deciding statement and its trace
 *   - GET  /users/{id}/effective-access     → the union of a user's active assignments
 *
 * **This file resolves nothing.** Every tier label, inheritance phrase, cascade note and summary
 * sentence is composed by the server and rendered here verbatim. Any permission logic in the
 * browser would be a second implementation of the rules — which is the drift Issue #174's
 * "one implementation, never two" exists to prevent, moved one layer out.
 *
 * External file only, to satisfy ``script-src 'self'``; no inline handlers.
 */
(function () {
  "use strict";

  var API = "/api/v1/admin/rbac";

  function readResponse(res) {
    var ct = res.headers.get("content-type") || "";
    var parse = ct.indexOf("application/json") >= 0 ? res.json() : Promise.resolve(null);
    return parse.then(function (data) { return { ok: res.ok, status: res.status, data: data }; });
  }

  function getJson(path) {
    return fetch(API + path, { credentials: "same-origin" }).then(readResponse);
  }

  /** POST a simulation. Uses the shared CSRF write headers, like every other unsafe call. */
  function simulate(body) {
    return fetch(API + "/simulate", {
      method: "POST",
      headers: window.BKP.writeHeaders(),
      credentials: "same-origin",
      body: JSON.stringify(body),
    }).then(readResponse);
  }

  function errorText(r, fallback) {
    var d = r && r.data;
    if (d && typeof d.detail === "string") return d.detail;
    if (r && r.status === 403) return "You do not have permission to see this.";
    return fallback;
  }

  /** Create an element with text content — never innerHTML, so API strings are never markup. */
  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = String(text);
    return node;
  }

  /**
   * Render one simulation response into `host`: the server's summary, the deciding statement, and
   * the raw trace behind a collapsed <details> for the reader who wants it.
   */
  function renderDecision(host, data) {
    host.textContent = "";
    var explanation = data.explanation || {};
    host.appendChild(el("p", "explain-summary", explanation.summary || ""));

    var facts = el("dl", "key-facts");
    function fact(label, value) {
      if (value == null || value === "") return;
      var row = document.createElement("div");
      row.appendChild(el("dt", null, label));
      row.appendChild(el("dd", null, value));
      facts.appendChild(row);
    }
    fact("Decision", data.decision === "allow" ? "Allowed" : "Refused");
    fact("Resource", data.resource);
    fact("Verb held", data.effective_verb ? data.effective_verb.toUpperCase() : "none");
    fact("Breadth", explanation.effective_tier_label);
    fact("Which rows", explanation.effective_tier_meaning);
    var statement = data.deciding_statement;
    if (statement) {
      fact("Deciding grant", explanation.deciding_sentence);
      fact("Written on role", statement.role);
      if (statement.inherited_via) fact("Inherited via", statement.inherited_via);
    }
    var instances = data.resolved_instances;
    if (instances && instances.count) {
      fact("Resolves to", instances.count + " " + instances.axis.replace(/_/g, " "));
    }
    if (data.principal_roles && data.principal_roles.length > 1) {
      fact("Roles in play", data.principal_roles.join(", "));
    }
    host.appendChild(facts);

    var details = el("details", "collapsible explain-trace");
    details.appendChild(el("summary", null, "Show the full decision trace"));
    var table = el("table", "list");
    var tbody = document.createElement("tbody");
    (data.trace || []).forEach(function (step) {
      var tr = document.createElement("tr");
      tr.appendChild(el("td", null, step.stage_label || step.stage));
      tr.appendChild(el("td", "muted", step.detail));
      tr.appendChild(el("td", null, step.outcome));
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    var wrap = el("div", "table-wrap");
    wrap.appendChild(table);
    details.appendChild(wrap);
    host.appendChild(details);
  }

  // ── Role detail: a "why" affordance per effective-access row ────────────────────────

  function initRoleDetail() {
    var panel = document.getElementById("role-effective-access");
    if (!panel) return;
    var role = panel.getAttribute("data-role");
    panel.addEventListener("click", function (e) {
      var btn = e.target.closest ? e.target.closest("[data-explain-resource]") : null;
      if (!btn || !panel.contains(btn)) return;
      var resource = btn.getAttribute("data-explain-resource");
      var verb = btn.getAttribute("data-explain-verb");
      var host = document.getElementById("explain-" + btn.getAttribute("data-explain-index"));
      if (!host) return;
      if (!host.hidden) { host.hidden = true; return; }
      host.hidden = false;
      host.textContent = "Resolving…";
      simulate({
        principal: { role: role },
        target: { resource: resource, verb: verb },
      }).then(function (r) {
        if (!r.ok) { host.textContent = errorText(r, "Could not explain this grant."); return; }
        renderDecision(host, r.data);
      });
    });
  }

  // ── User detail: the union of a user's active assignments ───────────────────────────

  function renderAssignments(host, rows) {
    host.textContent = "";
    if (!rows.length) {
      host.appendChild(el("p", "msg msg-muted", "This user has no explicit role assignments."));
      return;
    }
    var table = el("table", "list");
    var thead = document.createElement("thead");
    var hr = document.createElement("tr");
    ["Role", "Scope", "Status"].forEach(function (h) { hr.appendChild(el("th", null, h)); });
    thead.appendChild(hr);
    table.appendChild(thead);
    var tbody = document.createElement("tbody");
    rows.forEach(function (row) {
      var tr = document.createElement("tr");
      tr.appendChild(el("td", null, row.role));
      tr.appendChild(el("td", "muted", row.scope_type ? row.scope_type + " " + row.scope_id : "unscoped"));
      var status = el("td", null);
      status.appendChild(el(
        "span",
        "badge " + (row.status === "active" ? "badge-ok" : "badge-warn"),
        row.status_label
      ));
      tr.appendChild(status);
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    var wrap = el("div", "table-wrap");
    wrap.appendChild(table);
    host.appendChild(wrap);
  }

  function renderUserAccess(host, data) {
    host.textContent = "";
    if (!data.access.length) {
      host.appendChild(el("p", "msg msg-muted",
        "This user's active roles hold no grant on any resource, so they reach nothing."));
      return;
    }
    var table = el("table", "list");
    var thead = document.createElement("thead");
    var hr = document.createElement("tr");
    ["Resource", "Verb", "Breadth", "Which rows", "From role"].forEach(function (h) {
      hr.appendChild(el("th", null, h));
    });
    thead.appendChild(hr);
    table.appendChild(thead);
    var tbody = document.createElement("tbody");
    data.access.forEach(function (row) {
      var tr = document.createElement("tr");
      var res = el("td", null);
      res.appendChild(el("code", null, row.resource));
      tr.appendChild(res);
      tr.appendChild(el("td", null, (row.verb || "").toUpperCase()));
      var tier = el("td", null);
      tier.appendChild(el("span", "scope-tag", row.tier_label));
      tr.appendChild(tier);
      tr.appendChild(el("td", "muted", row.tier_meaning));
      tr.appendChild(el("td", "muted", (row.contributing_roles || []).join(", ")));
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    var wrap = el("div", "table-wrap");
    wrap.appendChild(table);
    host.appendChild(wrap);
  }

  function initUserDetail() {
    var panel = document.getElementById("user-effective-access");
    if (!panel) return;
    var userId = panel.getAttribute("data-user-id");
    var accessHost = document.getElementById("ud-access-body");
    var assignHost = document.getElementById("ud-assignments-body");
    accessHost.textContent = "Resolving…";
    getJson("/users/" + encodeURIComponent(userId) + "/effective-access").then(function (r) {
      if (!r.ok) {
        accessHost.textContent = errorText(r, "Could not load this user's access.");
        if (assignHost) assignHost.textContent = "";
        return;
      }
      renderUserAccess(accessHost, r.data);
      if (assignHost) renderAssignments(assignHost, r.data.assignments || []);
    });
  }

  // ── 403: why was this refused? ──────────────────────────────────────────────────────
  //
  // Rendered only when the server put the affordance on the page at all, which it does only for a
  // caller holding rbac:READ. For everyone else there is no element here to find, and this
  // function returns immediately — the 403 page they see is the one that shipped before Issue #175.

  function init403() {
    var panel = document.getElementById("denial-explainer");
    if (!panel) return;
    var btn = document.getElementById("denial-explain-btn");
    var host = document.getElementById("denial-explain-body");
    var path = panel.getAttribute("data-denied-path");
    var email = panel.getAttribute("data-principal-email");
    if (!btn || !host) return;
    btn.addEventListener("click", function () {
      host.hidden = false;
      host.textContent = "Resolving…";
      simulate({ principal: { email: email }, target: { path: path } }).then(function (r) {
        if (!r.ok) {
          host.textContent = errorText(
            r,
            "No navigation destination declares this path, so there is nothing to simulate."
          );
          return;
        }
        renderDecision(host, r.data);
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initRoleDetail();
    initUserDetail();
    init403();
  });
})();
