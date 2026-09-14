/* The waiting-room board, aloud (Issue 60): a chime, then the number and the room, one call at a time.
 *
 * board.js dispatches a `board:call` event on the document once for each new call (and each recall).
 * Its detail is { number, room } and nothing else: this script is never given a ticket, so it has no
 * name or reason to say, whatever the clinic's display mode puts on the screen. What it says is the
 * clinic's sentence from data-announce-call, which has two blanks, {number} and {room}
 * (src/modules/display/announcements.py refuses any other).
 *
 * How a call is said, in order of preference:
 *
 *   1. The browser's speech synthesis, with a voice for the clinic's language (data-announce-voice,
 *      a BCP 47 tag such as zu-ZA). The number is spelled out, character by character: "A 0 1 2".
 *   2. Recorded clips of each letter and digit of the number, in the clinic's language
 *      (data-announce-clips: character -> address), when the browser has no speech synthesis or no voice
 *      for the language, and every character of this number has a clip.
 *   3. Speech in English (data-announce-fallback-call and -voice), when the browser has an English voice
 *      but neither of the above.
 *   4. The chime alone. The screen still shows and highlights the call.
 *
 * A browser that lists no voices at all may still speak with its default one, so it is tried in the
 * clinic's language when no clips can say the number.
 *
 * Calls are queued: two called at the same moment are said one after the other, never over each other,
 * with a pause between. Every step (a chime, a clip, a sentence) has a time limit, so a sound that never
 * reports its end cannot stop the queue, and a sentence the speech engine has not begun within two seconds
 * is cancelled, so an engine with no voice cannot hold on to a day of sentences. At most MAX_WAITING calls
 * wait; the oldest is dropped past that.
 *
 * The clinic's settings come from the board payload (window.ClinicQBoard.state()) at the moment a call
 * is said, so a change in the dashboard applies to the next call with no reload:
 *   - announce_audio false mutes the board: the queue is emptied, speech stops, and the screen's
 *     highlight is untouched, because that is board.js's alone;
 *   - announce_volume (0-100) sets the chime's, the clips' and the voice's volume.
 *
 * window.ClinicQBoardAnnounce.log() returns the last few calls said, with how each was said and when:
 * the numbers and rooms of calls already on the screen, for tests and for someone at the box.
 *
 * Kiosk browsers must allow sound without a click: the board's pages are served with
 * Permissions-Policy autoplay=(self), and Chromium is started with --autoplay-policy=no-user-gesture-required
 * (docs/OPS/KIOSK_SETUP.md). Without them, play() and speak() are refused and the queue moves on.
 *
 * External file with no inline handlers: the CSP allows script only from 'self'.
 */
(function () {
  'use strict';

  var root = document.querySelector('.kiosk[data-announce-call]');
  if (!root || !window.ClinicQBoard) return;

  var CALL = root.getAttribute('data-announce-call');
  var VOICE_TAG = root.getAttribute('data-announce-voice') || 'en-ZA';
  var FALLBACK_CALL = root.getAttribute('data-announce-fallback-call') || CALL;
  var FALLBACK_VOICE_TAG = root.getAttribute('data-announce-fallback-voice') || 'en-ZA';
  var CHIME_URL = root.getAttribute('data-announce-chime');
  var CLIPS = parseClips(root.getAttribute('data-announce-clips'));

  var AFTER_CHIME_MS = 250; // the room looks up, then hears the number
  var BETWEEN_CLIPS_MS = 80;
  var BETWEEN_CALLS_MS = 700;
  var STEP_LIMIT_MS = 10000; // the longest one sound may hold the queue
  var SPEECH_START_LIMIT_MS = 2000; // a sentence not begun by then is not going to be
  var MAX_WAITING = 8;
  var LOG_SIZE = 20;
  var DEFAULT_VOLUME = 80;
  var SPEECH_RATE = 0.9; // a little slower than conversation, for a room

  var synth = window.speechSynthesis || null;
  var Utterance = window.SpeechSynthesisUtterance || null;
  var voices = [];
  var waiting = [];
  var busy = false;
  var player = new Audio(); // one element for every sound all day
  var speaking = null; // the utterance being said, kept so the browser does not collect it mid-sentence
  var log = [];

  function parseClips(raw) {
    try {
      var clips = JSON.parse(raw || '{}');
      return clips && typeof clips === 'object' ? clips : {};
    } catch (error) {
      return {};
    }
  }

  function loadVoices() {
    voices = synth ? synth.getVoices() || [] : [];
  }

  if (synth) {
    loadVoices();
    if (synth.addEventListener) synth.addEventListener('voiceschanged', loadVoices);
  }

  /* A voice for a BCP 47 tag: the exact tag first, then any voice for the same language. */
  function voiceFor(tag) {
    var wanted = tag.toLowerCase();
    var language = wanted.split('-')[0];
    var sameLanguage = null;
    for (var i = 0; i < voices.length; i += 1) {
      var lang = String(voices[i].lang || '').toLowerCase().replace('_', '-');
      if (lang === wanted) return voices[i];
      if (!sameLanguage && lang.split('-')[0] === language) sameLanguage = voices[i];
    }
    return sameLanguage;
  }

  function settings() {
    var board = window.ClinicQBoard.state();
    var volume = board && typeof board.announce_volume === 'number' ? board.announce_volume : DEFAULT_VOLUME;
    return {
      on: !board || board.announce_audio !== false,
      volume: Math.max(0, Math.min(100, volume)) / 100,
    };
  }

  function spelled(number) {
    return String(number).split('').join(' ');
  }

  /* The sentence with its two blanks filled, in one pass, so a room called "{number}" stays a room. */
  function sentence(template, call) {
    return template.replace(/\{(number|room)\}/g, function (whole, key) {
      return key === 'number' ? spelled(call.number) : call.room;
    });
  }

  function clipsFor(number) {
    var characters = String(number).toUpperCase().split('');
    var urls = [];
    for (var i = 0; i < characters.length; i += 1) {
      if (!Object.prototype.hasOwnProperty.call(CLIPS, characters[i])) return null;
      urls.push(CLIPS[characters[i]]);
    }
    return urls.length ? urls : null;
  }

  /* How this call will be said, decided when it is its turn (voices can arrive late). */
  function plan(call) {
    var canSpeak = !!(synth && Utterance);
    if (canSpeak && voiceFor(VOICE_TAG)) {
      return { how: 'speech', text: sentence(CALL, call), tag: VOICE_TAG };
    }
    var clips = clipsFor(call.number);
    if (clips) return { how: 'clips', clips: clips };
    if (canSpeak && voices.length === 0) {
      return { how: 'speech', text: sentence(CALL, call), tag: VOICE_TAG };
    }
    if (canSpeak && voiceFor(FALLBACK_VOICE_TAG)) {
      return { how: 'speech', text: sentence(FALLBACK_CALL, call), tag: FALLBACK_VOICE_TAG };
    }
    return { how: 'chime' };
  }

  /* Run one sound; resolve when it ends, fails, or runs past the limit. */
  function step(start) {
    return new Promise(function (resolve) {
      var finished = false;
      var limit = setTimeout(done, STEP_LIMIT_MS);
      function done() {
        if (finished) return;
        finished = true;
        clearTimeout(limit);
        resolve();
      }
      try {
        start(done);
      } catch (error) {
        done();
      }
    });
  }

  function pause(ms) {
    return new Promise(function (resolve) {
      setTimeout(resolve, ms);
    });
  }

  function play(url, volume) {
    return step(function (done) {
      player.onended = done;
      player.onerror = done;
      player.src = url;
      player.volume = volume;
      var started = player.play();
      if (started && started.catch) started.catch(done);
    });
  }

  /* Say a sentence. A speech engine that never starts it (no voice installed, a stuck engine) is told to
     forget it, or it would keep every sentence it was given for the rest of the day. */
  function speak(text, tag, volume) {
    var started = false;
    var unstarted = null;
    return step(function (done) {
      var utterance = new Utterance(text);
      utterance.lang = tag;
      var voice = voiceFor(tag);
      if (voice) utterance.voice = voice;
      utterance.volume = volume;
      utterance.rate = SPEECH_RATE;
      utterance.onstart = function () { started = true; };
      utterance.onend = done;
      utterance.onerror = done;
      speaking = utterance;
      synth.cancel(); // nothing left over from an earlier sentence
      synth.speak(utterance);
      unstarted = setTimeout(function () {
        if (!started && !synth.speaking) done();
      }, SPEECH_START_LIMIT_MS);
    }).then(function () {
      clearTimeout(unstarted);
      if (speaking) {
        speaking.onstart = speaking.onend = speaking.onerror = null;
        speaking = null;
      }
      synth.cancel();
    });
  }

  function playAll(urls, volume) {
    return urls.reduce(function (chain, url, index) {
      return chain.then(function () {
        return index ? pause(BETWEEN_CLIPS_MS).then(function () { return play(url, volume); }) : play(url, volume);
      });
    }, Promise.resolve());
  }

  function remember(entry) {
    log.push(entry);
    if (log.length > LOG_SIZE) log.shift();
  }

  function say(call) {
    var chosen = settings();
    if (!chosen.on) return Promise.resolve();
    var entry = { number: call.number, room: call.room, how: null, text: null, started: Date.now(), ended: null };
    remember(entry);
    var chime = CHIME_URL ? play(CHIME_URL, chosen.volume) : Promise.resolve();
    return chime
      .then(function () { return pause(AFTER_CHIME_MS); })
      .then(function () {
        if (!settings().on) return null;
        var how = plan(call);
        entry.how = how.how;
        if (how.how === 'speech') {
          entry.text = how.text;
          return speak(how.text, how.tag, chosen.volume);
        }
        if (how.how === 'clips') {
          entry.text = how.clips.join(' ');
          return playAll(how.clips, chosen.volume);
        }
        return null;
      })
      .then(function () {
        entry.ended = Date.now();
      });
  }

  function next() {
    if (busy) return;
    var call = waiting.shift();
    if (!call) return;
    busy = true;
    say(call)
      .then(function () { return pause(BETWEEN_CALLS_MS); })
      .then(function () {
        busy = false;
        next();
      });
  }

  function mute() {
    waiting.length = 0;
    if (synth) synth.cancel();
    player.pause();
  }

  document.addEventListener('board:call', function (event) {
    var detail = event.detail || {};
    if (!settings().on) {
      mute();
      return;
    }
    // Only the number and the room are kept, whatever else an event might carry.
    waiting.push({ number: String(detail.number || ''), room: String(detail.room || '') });
    if (waiting.length > MAX_WAITING) waiting.shift();
    next();
  });

  window.ClinicQBoardAnnounce = {
    log: function () {
      return log.map(function (entry) {
        return {
          number: entry.number,
          room: entry.room,
          how: entry.how,
          text: entry.text,
          started: entry.started,
          ended: entry.ended,
        };
      });
    },
  };
})();
