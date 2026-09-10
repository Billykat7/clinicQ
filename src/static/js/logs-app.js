/**
 * Application logs viewer (Issue 9).
 *
 * Talks to /api/v1/admin/logs:
 *   - GET /logs?level=&path=&year=&month=&day=&keyword=&page_size=&continuation_token=
 *   - GET /logs/object?key=...
 *
 * Lists S3 log objects for a level/source/date and opens one object's NDJSON
 * body, colourised by level. Timestamps display in Africa/Johannesburg.
 */
(function () {
  "use strict";

  var API = "/api/v1/admin/logs";
  var TZ = "Africa/Johannesburg";

  document.addEventListener("DOMContentLoaded", init);

  function init() {
    var el = {
      level: document.getElementById("log-level"),
      path: document.getElementById("log-path"),
      date: document.getElementById("log-date"),
      keyword: document.getElementById("log-keyword"),
      refresh: document.getElementById("log-refresh"),
      meta: document.getElementById("log-meta"),
      loading: document.getElementById("log-loading"),
      list: document.getElementById("log-list"),
      listMsg: document.getElementById("log-list-msg"),
      objectTitle: document.getElementById("log-object-title"),
      objectKey: document.getElementById("log-object-key"),
      objectLevel: document.getElementById("log-object-level"),
      objectBody: document.getElementById("log-object-body"),
      objectMsg: document.getElementById("log-object-msg"),
    };

    // Reusable detail dialog (Issue 95): opening it traps focus and Esc/backdrop/close dismisses it;
    // on close we drop the row highlight so a reopened panel starts clean.
    var slideover = window.BKPAdmin && window.BKPAdmin.mountSlideover("log-detail", {
      lockScroll: false, // the panel floats to one side — keep the page/list scrollable behind it
      onClose: function () {
        Array.prototype.forEach.call(el.list.children, function (row) { row.classList.remove("selected"); });
      },
    });

    // Numbered pagination over the full (server-cached) listing: the endpoint returns a page slice
    // plus the grand total, so the shared pager can page without re-hitting S3 per page.
    var pager = window.BKPAdmin.createPager({ sizes: [10, 25, 50, 100], onChange: function () { load(); } });
    pager.mount(document.getElementById("log-pager"));

    function buildParams() {
      var params = new URLSearchParams();
      params.set("level", el.level.value || "all");
      if (el.path.value) params.set("path", el.path.value);
      if (el.date.value) {
        var parts = el.date.value.split("-");
        if (parts.length === 3) {
          params.set("year", parts[0]);
          params.set("month", String(parseInt(parts[1], 10)));
          params.set("day", String(parseInt(parts[2], 10)));
        }
      }
      if (el.keyword.value.trim()) params.set("keyword", el.keyword.value.trim());
      params.set("offset", String(pager.offset));
      params.set("page_size", String(pager.limit));
      return params;
    }

    function load() {
      el.loading.hidden = false;
      setMsg(el.listMsg, "");
      fetch(API + "?" + buildParams().toString(), { credentials: "same-origin" })
        .then(readResponse)
        .then(function (r) {
          el.loading.hidden = true;
          if (!r.ok || !r.data) return setMsg(el.listMsg, errorText(r, "Could not load logs."), "error");
          var data = r.data;
          renderMeta(data);
          var items = data.items || [];
          renderRows(items);
          pager.update(items.length, data.total || 0);
          if (!(data.total || 0)) {
            setMsg(el.listMsg, data.message || "No log objects for these filters.", "muted");
          }
        })
        .catch(function () { el.loading.hidden = true; setMsg(el.listMsg, "Network error.", "error"); });
    }
    // A new search / filter restarts paging from the first page.
    function reload() { pager.reset(); load(); }

    function renderMeta(data) {
      var bits = [];
      if (data.bucket) bits.push("bucket " + data.bucket);
      if (data.prefix_used) bits.push("prefix " + data.prefix_used);
      setMsg(el.meta, bits.join(" · "), "muted");
    }

    function renderRows(items) {
      el.list.innerHTML = "";
      items.forEach(function (item) {
        var tr = document.createElement("tr");

        var obj = document.createElement("td");
        obj.textContent = item.file_name || item.key;
        var sub = document.createElement("div");
        sub.className = "msg-muted";
        sub.style.fontSize = ".75rem";
        sub.textContent = [item.path_segment, item.calendar_date].filter(Boolean).join(" · ");
        obj.appendChild(sub);
        tr.appendChild(obj);

        var level = document.createElement("td");
        var badge = document.createElement("span");
        badge.className = "badge " + levelBadge(item.log_level);
        badge.textContent = item.log_level || "—";
        level.appendChild(badge);
        tr.appendChild(level);

        tr.appendChild(td(formatBytes(item.size)));
        tr.appendChild(td(fmtDateTime(item.last_modified)));

        // A row opens the detail slide-over. Make it keyboard-operable (focusable + Enter/Space)
        // so it can be reached without a mouse and so focus can be restored to it on close.
        tr.tabIndex = 0;
        function activate() {
          Array.prototype.forEach.call(el.list.children, function (row) { row.classList.remove("selected"); });
          tr.classList.add("selected");
          if (slideover) slideover.open(tr);
          openObject(item);
        }
        tr.addEventListener("click", activate);
        tr.addEventListener("keydown", function (e) {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); activate(); }
        });
        el.list.appendChild(tr);
      });
    }

    function openObject(item) {
      el.objectTitle.textContent = item.file_name || "Log contents";
      el.objectTitle.title = item.file_name || "";
      el.objectKey.textContent = item.key;
      el.objectKey.title = item.key || "";
      if (el.objectLevel) {
        var lvl = item.log_level || "";
        el.objectLevel.textContent = lvl;
        el.objectLevel.className = "badge " + levelBadge(lvl);
        el.objectLevel.hidden = !lvl;
      }
      el.objectBody.hidden = true;
      setMsg(el.objectMsg, "Loading…", "muted");
      fetch(API + "/object?key=" + encodeURIComponent(item.key), { credentials: "same-origin" })
        .then(readResponse)
        .then(function (r) {
          if (!r.ok || !r.data) return setMsg(el.objectMsg, errorText(r, "Could not load that object."), "error");
          renderBody(r.data.content || "");
          setMsg(el.objectMsg, r.data.truncated ? "Output truncated for size." : "", "muted");
        })
        .catch(function () { setMsg(el.objectMsg, "Network error.", "error"); });
    }

    function renderBody(content) {
      el.objectBody.hidden = false;
      el.objectBody.innerHTML = "";
      var lines = content.split("\n");
      lines.forEach(function (raw) {
        if (!raw.trim()) return;
        var line = document.createElement("div");
        line.className = "log-line";
        var level = detectLevel(raw);
        if (level) {
          var tag = document.createElement("span");
          tag.className = "lv lv-" + level;
          tag.textContent = level.toUpperCase() + " ";
          line.appendChild(tag);
        }
        line.appendChild(document.createTextNode(prettyLine(raw)));
        el.objectBody.appendChild(line);
      });
    }

    function detectLevel(raw) {
      try {
        var obj = JSON.parse(raw);
        var lv = (obj.level || obj.levelname || "").toString().toLowerCase();
        if (lv.indexOf("error") >= 0 || lv.indexOf("critical") >= 0) return "error";
        if (lv.indexOf("warn") >= 0) return "warning";
        if (lv.indexOf("debug") >= 0) return "debug";
        if (lv.indexOf("info") >= 0) return "info";
      } catch (e) { /* not JSON */ }
      return "";
    }

    function prettyLine(raw) {
      try { return JSON.stringify(JSON.parse(raw)); } catch (e) { return raw; }
    }

    el.refresh.addEventListener("click", reload);
    el.keyword.addEventListener("keydown", function (e) { if (e.key === "Enter") reload(); });
    [el.level, el.path, el.date].forEach(function (control) {
      control.addEventListener("change", reload);
    });

    load();
  }

  // ── Helpers ────────────────────────────────────────────────────────────────

  function readResponse(res) {
    var ct = res.headers.get("content-type") || "";
    var parse = ct.indexOf("application/json") >= 0 ? res.json() : Promise.resolve(null);
    return parse.then(function (data) { return { ok: res.ok, status: res.status, data: data }; });
  }
  function errorText(r, fallback) {
    var d = r && r.data;
    if (d && typeof d.detail === "string") return d.detail;
    return fallback;
  }
  function setMsg(el, text, kind) { if (el) { el.textContent = text || ""; el.className = "msg" + (kind ? " msg-" + kind : ""); } }
  function td(text) { var el = document.createElement("td"); el.textContent = text; return el; }
  function levelBadge(level) {
    var l = (level || "").toLowerCase();
    if (l === "error") return "badge-warn";
    if (l === "warning") return "badge-warn";
    if (l === "info") return "badge-ok";
    return "badge-muted";
  }
  function formatBytes(n) {
    if (n === null || n === undefined) return "—";
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    return (n / (1024 * 1024)).toFixed(1) + " MB";
  }
  function fmtDateTime(iso) {
    if (!iso) return "—";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    try {
      return d.toLocaleString("en-ZA", { timeZone: TZ, year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" });
    } catch (e) { return d.toISOString(); }
  }
})();
