/* The ticket stub (Issue 51): print it, at once when opened with ?print=1.
 *
 * The page is the stub and the on-screen number in one; this file only asks the browser to print. The
 * print stylesheet (ticket-stub.css) sizes it for a 58 mm thermal roll. External file: the CSP allows
 * script only from 'self'.
 */
(function () {
  'use strict';

  var button = document.querySelector('[data-stub-print]');
  if (button) button.addEventListener('click', function () { window.print(); });

  if (new URLSearchParams(window.location.search).get('print') === '1') {
    window.addEventListener('load', function () { window.print(); });
  }
})();
