/* The post-visit question (Issue 87): one tap on a score sends it, with any comment typed above.
 *
 * The server decides everything (whether the question is open, what is kept of the comment); this sends the
 * tap and shows the answer. External file, no inline handlers: the CSP allows script only from 'self'.
 */
(function () {
  "use strict";

  var stateNode = document.getElementById("fb-state");
  if (!stateNode) return;
  var state = JSON.parse(stateNode.textContent);
  var ask = document.getElementById("fb-ask");
  var thanks = document.getElementById("fb-thanks");
  var closed = document.getElementById("fb-closed");
  var note = document.getElementById("fb-note");
  var buttons = document.querySelectorAll(".fb-score");

  function csrfToken() {
    var match = document.cookie.match(/(?:^|; )bk_clinicq_csrf=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function show(next) {
    state = next;
    ask.hidden = next.answered || next.expired;
    thanks.hidden = !next.answered;
    closed.hidden = next.answered || !next.expired;
  }

  function busy(on) {
    for (var i = 0; i < buttons.length; i++) buttons[i].disabled = on;
  }

  for (var i = 0; i < buttons.length; i++) {
    buttons[i].addEventListener("click", function (event) {
      if (!state.answer_url) return;
      var comment = document.getElementById("fb-comment").value.trim();
      busy(true);
      note.hidden = true;
      fetch(state.answer_url, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", Accept: "application/json", "X-CSRF-Token": csrfToken() },
        body: JSON.stringify({ score: Number(event.currentTarget.getAttribute("data-score")), comment: comment || null })
      })
        .then(function (response) {
          return response.json().then(function (body) {
            return { ok: response.ok, body: body };
          });
        })
        .then(function (result) {
          busy(false);
          if (result.ok) {
            show(result.body);
          } else {
            note.hidden = false;
            note.textContent = (result.body && result.body.detail) || "That did not go through. Please try again.";
          }
        })
        .catch(function () {
          busy(false);
          note.hidden = false;
          note.textContent = "No connection: your answer has not been sent. Try again when you have signal.";
        });
    });
  }
})();
