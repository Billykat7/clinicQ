/* The waiting-room board (Issue 56): draws now serving and up next for every queue, all day.
 *
 * The page carries the privacy projection's payload (Issue 58) in data-initial, so the screen is full
 * the moment it loads. After that, board-live.js hands it each new board from the live stream, or from
 * /state while the stream is down (Issue 57), through window.ClinicQBoard.apply. The payload is the
 * only thing drawn: a ticket's name or reason appears only when the server put it there.
 *
 * What it does with the payload:
 *
 *   - Layout. data-layout on the grid is the number of panels on screen: 1, 2, 3, or 4 for a grid of
 *     two by two. A clinic with more queues than data-panels-per-page shows them a page at a time,
 *     turning every data-page-seconds, always in layout 4, so sizes do not jump between pages.
 *   - Now serving. The newest call is drawn large; the calls before it are listed on one line under it.
 *   - Newly called. A ticket called (or called again) less than data-highlight-seconds ago, on the
 *     server's clock, is marked is-new and says "Called now". The page with a new call is shown at
 *     once, and does not turn while the highlight lasts. The call is also said once to assistive
 *     technology through an assertive live region, and dispatched once as a `board:call` event on the
 *     document, whose detail is { number, room } and nothing else, for board-announce.js to say aloud
 *     (Issue 60).
 *   - Health notices. One at a time, changed every data-message-seconds with a short fade (no fade
 *     under reduced motion; board.css).
 *
 * Every board drawn is announced as a `board:applied` event, whose detail says whether it was an old one;
 * board-offline.js keeps the new ones (Issue 62).
 *
 * Built to run for days on a small box: the panels are kept and updated rather than rebuilt, no
 * listener is added after start-up, and the only timers are a one-second tick and the notice change.
 *
 * External file with no inline handlers: the CSP allows script only from 'self'.
 */
(function () {
  'use strict';

  var root = document.querySelector('.kiosk[data-state-url]');
  if (!root) return;

  var grid = root.querySelector('[data-board-grid]');
  var empty = root.querySelector('[data-board-empty]');
  var ticker = root.querySelector('[data-board-ticker]');
  var pager = root.querySelector('[data-board-pages]');
  var announcer = root.querySelector('[data-board-announce]');
  var panelTemplate = document.getElementById('kiosk-panel');
  var servingTemplate = document.getElementById('kiosk-serving');
  var nextTemplate = document.getElementById('kiosk-next');

  function seconds(name, fallback) {
    var value = parseFloat(root.getAttribute(name));
    return (isFinite(value) && value > 0 ? value : fallback) * 1000;
  }

  var HIGHLIGHT_MS = seconds('data-highlight-seconds', 20);
  var PAGE_MS = seconds('data-page-seconds', 15);
  var MESSAGE_MS = seconds('data-message-seconds', 12);
  var PER_PAGE = parseInt(root.getAttribute('data-panels-per-page'), 10) || 4;
  var FADE_MS = 600;
  var TICK_MS = 1000;
  // How many waiting numbers each layout has room for (board.css sizes them per layout).
  var UP_NEXT_ROOM = { 1: 5, 2: 5, 3: 5, 4: 4 };
  var STATUS_IN_PROGRESS = 'in_progress';

  var state = null;
  var clockOffset = 0; // server time minus this box's time, in ms
  var page = 0;
  var pageShownAt = Date.now();
  var panels = {}; // queue id -> its panel element, kept across updates
  var announced = {}; // "number@called_at" -> true, so each call is said once
  var quiet = false; // drawing an old board: its calls are marked as said, and not said
  var oldBoard = false; // the board on screen is an old one, kept from before (Issue 62)

  function serverNow() {
    return Date.now() + clockOffset;
  }

  function calledRecently(ticket) {
    // An old board (Issue 62) says nothing is happening now: none of its calls is "Called now".
    if (oldBoard || !ticket.called_at || ticket.status === STATUS_IN_PROGRESS) return false;
    var age = serverNow() - Date.parse(ticket.called_at);
    return age >= -5000 && age < HIGHLIGHT_MS;
  }

  function pageCount() {
    return state ? Math.max(1, Math.ceil(state.queues.length / PER_PAGE)) : 1;
  }

  /* The page holding the most recent call still highlighted, or -1 when nothing is new. */
  function pageWithNewCall() {
    var newest = -1;
    var newestAt = -Infinity;
    state.queues.forEach(function (queue, index) {
      queue.now_serving.forEach(function (ticket) {
        var at = Date.parse(ticket.called_at);
        if (calledRecently(ticket) && at > newestAt) {
          newestAt = at;
          newest = Math.floor(index / PER_PAGE);
        }
      });
    });
    return newest;
  }

  function text(element, value) {
    if (element.textContent !== value) element.textContent = value;
  }

  function fill(template, values) {
    return template.replace(/\{(\w+)\}/g, function (whole, key) {
      return Object.prototype.hasOwnProperty.call(values, key) ? String(values[key]) : whole;
    });
  }

  function panelFor(queue) {
    var panel = panels[queue.id];
    if (!panel) {
      panel = panelTemplate.content.firstElementChild.cloneNode(true);
      panel.setAttribute('data-queue-id', queue.id);
      panels[queue.id] = panel;
    }
    return panel;
  }

  function servingItem(ticket) {
    var item = servingTemplate.content.firstElementChild.cloneNode(true);
    item.setAttribute('data-called-at', ticket.called_at || '');
    item.setAttribute('data-status', ticket.status);
    text(item.querySelector('.serving-number'), ticket.number);
    // Only what the payload carries: a name or reason the server left out does not exist here.
    if (ticket.name) text(item.querySelector('.serving-name'), ticket.name);
    if (ticket.comment) text(item.querySelector('.serving-comment'), ticket.comment);
    markServing(item);
    return item;
  }

  function markServing(item) {
    var ticket = {
      called_at: item.getAttribute('data-called-at'),
      status: item.getAttribute('data-status'),
    };
    var isNew = calledRecently(ticket);
    item.classList.toggle('is-new', isNew);
    var label = isNew
      ? item.getAttribute('data-status-new')
      : item.getAttribute('data-status-' + ticket.status) || '';
    text(item.querySelector('.serving-state'), label);
  }

  function nextItem(ticket) {
    var item = nextTemplate.content.firstElementChild.cloneNode(true);
    text(item.querySelector('.next-number'), ticket.number);
    if (ticket.name) text(item.querySelector('.next-name'), ticket.name);
    return item;
  }

  function drawPanel(panel, queue, layout) {
    text(panel.querySelector('.panel-label'), queue.label);
    text(panel.querySelector('.panel-room'), queue.room || '');

    var serving = queue.now_serving;
    var servingList = panel.querySelector('.panel-serving');
    servingList.replaceChildren.apply(
      servingList,
      serving.length ? [servingItem(serving[0])] : []
    );
    panel.querySelector('.panel-none').hidden = serving.length > 0;
    var also = panel.querySelector('.panel-also');
    text(
      also,
      serving.length > 1
        ? fill(also.getAttribute('data-template'), {
            numbers: serving.slice(1).map(function (t) { return t.number; }).join(' · '),
          })
        : ''
    );

    var room = UP_NEXT_ROOM[layout] || 4;
    var queueList = panel.querySelector('.panel-queue');
    queueList.replaceChildren.apply(queueList, queue.up_next.slice(0, room).map(nextItem));
    var waiting = panel.querySelector('.panel-waiting');
    var count = queue.waiting;
    text(
      waiting,
      count === 0
        ? waiting.getAttribute('data-none')
        : count === 1
          ? waiting.getAttribute('data-one')
          : fill(waiting.getAttribute('data-many'), { count: count })
    );
  }

  function announce() {
    state.queues.forEach(function (queue) {
      queue.now_serving.forEach(function (ticket) {
        var key = ticket.number + '@' + ticket.called_at;
        if (calledRecently(ticket) && !announced[key]) {
          announced[key] = true;
          // An old board's calls were said when they happened; they are not said again (Issue 62).
          if (quiet) return;
          // A call is its number and where to go: nothing else about the ticket leaves this function.
          var call = { number: ticket.number, room: queue.room || queue.label };
          if (announcer) text(announcer, fill(announcer.getAttribute('data-template'), call));
          document.dispatchEvent(new CustomEvent('board:call', { detail: call }));
        }
      });
    });
    // Forget calls long past, so the record stays the size of one screen.
    Object.keys(announced).forEach(function (key) {
      var at = Date.parse(key.slice(key.indexOf('@') + 1));
      if (!(serverNow() - at < HIGHLIGHT_MS * 2)) delete announced[key];
    });
  }

  function render() {
    if (!state) return;
    var queues = state.queues;
    var pages = pageCount();
    grid.hidden = queues.length === 0;
    empty.hidden = queues.length > 0;

    var withNew = pageWithNewCall();
    if (withNew >= 0 && withNew !== page) {
      page = withNew;
      pageShownAt = Date.now();
    }
    if (page >= pages) page = 0;

    var layout = Math.min(queues.length, PER_PAGE);
    grid.setAttribute('data-layout', String(layout));
    var shown = queues.slice(page * PER_PAGE, (page + 1) * PER_PAGE);
    var elements = shown.map(function (queue) {
      var panel = panelFor(queue);
      drawPanel(panel, queue, layout);
      return panel;
    });
    grid.replaceChildren.apply(grid, elements);

    // Panels of queues that closed are let go, so a long day does not collect them.
    var live = {};
    queues.forEach(function (queue) { live[queue.id] = true; });
    Object.keys(panels).forEach(function (id) {
      if (!live[id]) delete panels[id];
    });

    if (pager) {
      pager.hidden = pages < 2;
      text(pager, pages < 2 ? '' : fill(pager.getAttribute('data-template') || '{page} / {pages}', { page: page + 1, pages: pages }));
    }
    announce();
  }

  /* Draw a board. options.stale marks one that is old (kept from before, Issue 62): its time is not
     taken as the clinic's clock, none of its calls is highlighted as "Called now", and none is announced
     again. The next board from the clinic ends that. */
  function apply(payload, options) {
    if (!payload || !Array.isArray(payload.queues)) return;
    var stale = !!(options && options.stale);
    state = payload;
    // The clinic's theme (Issue 59) follows a settings change at once, with no reload.
    if (payload.theme && root.getAttribute('data-board-theme') !== payload.theme) {
      root.setAttribute('data-board-theme', payload.theme);
    }
    var asOf = Date.parse(payload.as_of);
    if (isFinite(asOf) && !stale) clockOffset = asOf - Date.now();
    oldBoard = stale;
    quiet = stale;
    render();
    quiet = false;
    document.dispatchEvent(new CustomEvent('board:applied', { detail: { stale: stale } }));
  }

  function tick() {
    if (!state) return;
    grid.querySelectorAll('.serving').forEach(markServing);
    var pages = pageCount();
    var holding = pageWithNewCall() === page;
    if (pages > 1 && !holding && Date.now() - pageShownAt >= PAGE_MS) {
      page = (page + 1) % pages;
      pageShownAt = Date.now();
      render();
    }
  }

  function rotateMessages() {
    if (!ticker) return;
    var messages;
    try {
      messages = JSON.parse(ticker.getAttribute('data-messages') || '[]');
    } catch (error) {
      return;
    }
    if (messages.length < 2) return;
    var index = 0;
    setInterval(function () {
      index = (index + 1) % messages.length;
      ticker.classList.add('is-changing');
      setTimeout(function () {
        text(ticker, messages[index]);
        ticker.classList.remove('is-changing');
      }, FADE_MS);
    }, MESSAGE_MS);
  }

  try {
    // A page the service worker kept (data-from-cache, Issue 62) carries an old board.
    apply(JSON.parse(root.getAttribute('data-initial') || 'null'), { stale: root.hasAttribute('data-from-cache') });
  } catch (error) {
    // A page without a usable payload draws the first board the stream or /state brings.
  }
  setInterval(tick, TICK_MS);
  rotateMessages();

  window.ClinicQBoard = {
    apply: apply,
    state: function () { return state; },
  };
})();
