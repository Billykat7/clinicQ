/**
 * Finding waiting-room screens on the server's network, and sending one the board (Issue 237).
 *
 * The rest of this page is declarative (`dashboard-api.js`): a form with `data-api` becomes a
 * request and a reload. This section is not a form — it is a search whose answer a manager reads
 * and picks from — so it has a file of its own, and it still decides nothing. Both buttons call the
 * clinic's display-devices API, which checks the grant, holds the row and records the change; this
 * file shows what came back, in the server's own words.
 *
 * Nothing found is a normal answer with a reason attached (`note`), and the reason is what a manager
 * can act on: the TV is asleep, this server is in another building, an operator has not turned the
 * feature on. It is shown exactly as the server wrote it.
 *
 * External file only, to satisfy `script-src 'self'`.
 */
(function () {
  "use strict";

  var root = document.getElementById("screen-finder");
  if (!root) return;

  var findButton = document.getElementById("find-screens");
  var message = document.getElementById("find-msg");
  var list = document.getElementById("screen-list");
  var rowTemplate = document.getElementById("screen-row");

  /** Show `text` beside the button; `ok` marks the hopeful kind. */
  function say(text, ok) {
    if (!message) return;
    message.textContent = text || "";
    message.classList.toggle("is-ok", Boolean(ok));
  }

  /** POST JSON with the CSRF header, resolving {ok, status, data} — never throwing for a refusal. */
  function post(url, body) {
    return fetch(url, {
      method: "POST",
      headers: window.BKP.writeHeaders(),
      credentials: "same-origin",
      body: JSON.stringify(body || {}),
    }).then(function (res) {
      var type = res.headers.get("content-type") || "";
      var parse =
        type.indexOf("application/json") >= 0 ? res.json() : Promise.resolve(null);
      return parse
        .catch(function () { return null; })
        .then(function (data) {
          return { ok: res.ok, status: res.status, data: data };
        });
    });
  }

  /** The human sentence in a FastAPI error body, or `fallback`. */
  function errorText(result, fallback) {
    var detail = result && result.data && result.data.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length && detail[0] && detail[0].msg) {
      return detail[0].msg;
    }
    return fallback;
  }

  /** Where a screen is, said the way a person would: its model, then its address. */
  function whereLine(screen) {
    var parts = [];
    if (screen.model && screen.model !== screen.name) parts.push(screen.model);
    if (screen.manufacturer && screen.manufacturer !== screen.model) {
      parts.push(screen.manufacturer);
    }
    parts.push(screen.address);
    return parts.join(" · ");
  }

  function busy(on) {
    if (findButton) {
      findButton.disabled = on;
      findButton.textContent = on ? "Searching…" : "Search the network";
    }
  }

  /** Send the board to one screen, then say what the screen did about it. */
  function connect(screen, button) {
    button.disabled = true;
    button.textContent = "Sending…";
    say("");
    post(root.dataset.connect, {
      address: screen.address,
      port: screen.port,
      name: screen.name,
      uuid: screen.uuid,
      label: screen.suggested_label,
    })
      .then(function (result) {
        if (!result.ok) {
          say(errorText(result, "Could not send the board to that screen."));
          button.disabled = false;
          button.textContent = "Send the board";
          return;
        }
        var data = result.data || {};
        // `showing` and `reached` are separate answers on purpose: a screen that answered but has
        // no ClinicQ receiver to open is a different problem from a screen that is switched off,
        // and the server says which in `note`.
        say(data.note || "The screen is opening the waiting-room board.", data.showing);
        button.textContent = data.showing ? "Sent" : "Send the board";
        button.disabled = Boolean(data.showing);
        // The row is held whether or not the screen took the board, so a cast the manager cannot
        // fix from here -- no receiver registered, a set that will not be cast to -- still has a
        // way out. The server sends the claim address only while it is still worth something.
        offerLink(data.claim_url);
        if (data.showing) {
          // The screen is now one of this clinic's, so the list below it is out of date.
          window.setTimeout(function () { window.location.reload(); }, 2500);
        }
      })
      .catch(function () {
        say("Could not reach the server. Check your connection and try again.");
        button.disabled = false;
        button.textContent = "Send the board";
      });
  }

  /**
   * Put the claim address under the message, as something to open on the screen itself.
   *
   * A link rather than the code: the code alone needs somebody to know where to type it, and the
   * point of this whole section is that nobody carries anything across the room. The full section
   * for making one on purpose is above; this is the same thing offered where the failure happened.
   */
  function offerLink(url) {
    var existing = document.getElementById("cast-fallback");
    if (existing) existing.remove();
    if (!url || !message || !message.parentNode) return;
    var note = document.createElement("p");
    note.id = "cast-fallback";
    note.className = "hint";
    note.appendChild(
      document.createTextNode("This screen is still waiting for a board. Open it on the screen's own browser: ")
    );
    var link = document.createElement("a");
    link.href = url;
    link.rel = "noreferrer";
    // Not a link to follow from here: opening it makes *this* browser the screen.
    link.textContent = url;
    note.appendChild(link);
    message.parentNode.insertBefore(note, message.nextSibling);
  }

  function render(screens) {
    if (!list || !rowTemplate) return;
    list.textContent = "";
    screens.forEach(function (screen) {
      var row = rowTemplate.content.firstElementChild.cloneNode(true);
      row.querySelector(".screen-name").textContent =
        screen.suggested_label || screen.name || "Unnamed screen";
      row.querySelector(".screen-where").textContent = whereLine(screen);
      var send = row.querySelector(".screen-send");
      send.addEventListener("click", function () { connect(screen, send); });
      list.appendChild(row);
    });
    list.hidden = screens.length === 0;
  }

  findButton.addEventListener("click", function () {
    busy(true);
    say("Listening for screens…");
    if (list) { list.hidden = true; list.textContent = ""; }
    post(root.dataset.discover, {})
      .then(function (result) {
        busy(false);
        if (!result.ok) {
          say(errorText(result, "Could not search this server's network."));
          return;
        }
        var data = result.data || {};
        var screens = data.items || [];
        render(screens);
        if (screens.length) {
          say(
            screens.length === 1
              ? "One screen answered."
              : screens.length + " screens answered.",
            true
          );
        } else {
          say(data.note || "No screen answered.");
        }
      })
      .catch(function () {
        busy(false);
        say("Could not reach the server. Check your connection and try again.");
      });
  });
})();
