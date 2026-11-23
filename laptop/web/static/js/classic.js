var hrs = [], freshLabel = 'no data yet', sw = 1, sh = 1, currentSt = 'OFFLINE';
var tickBusy = false;

function $(id) { return document.getElementById(id); }
function esc(s) {
  return String(s || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/* ---- Sparkline: Smooth Cardinal Spline + State-Aware Glow ------------ */
function sizeSpark() {
  var c = $('spark');
  if (!c) return;
  var r = c.getBoundingClientRect();
  var dpr = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
  sw = Math.max(1, Math.round(r.width));
  sh = Math.max(1, Math.round(r.height));
  c.width = Math.round(sw * dpr);
  c.height = Math.round(sh * dpr);
  c.getContext('2d').setTransform(dpr, 0, 0, dpr, 0, 0);
}

function drawSplinePath(ctx, pts) {
  ctx.moveTo(pts[0][0], pts[0][1]);
  if (pts.length === 2) {
    ctx.lineTo(pts[1][0], pts[1][1]);
    return;
  }
  for (var i = 0; i < pts.length - 1; i++) {
    var p0 = pts[i === 0 ? 0 : i - 1];
    var p1 = pts[i];
    var p2 = pts[i + 1];
    var p3 = pts[i + 2 < pts.length ? i + 2 : i + 1];
    var cp1x = p1[0] + (p2[0] - p0[0]) / 6;
    var cp1y = p1[1] + (p2[1] - p0[1]) / 6;
    var cp2x = p2[0] - (p3[0] - p1[0]) / 6;
    var cp2y = p2[1] - (p3[1] - p1[1]) / 6;
    ctx.bezierCurveTo(cp1x, cp1y, cp2x, cp2y, p2[0], p2[1]);
  }
}

function draw() {
  if (document.hidden) return;
  var c = $('spark');
  if (!c) return;
  var x = c.getContext('2d');
  x.clearRect(0, 0, sw, sh);

  var padY = Math.max(14, sh * 0.15),
      padR = Math.max(16, sw * 0.02),
      gutter = 40,
      i;
  var top = padY,
      base = sh - padY,
      w = Math.max(1, sw - gutter - padR),
      h = Math.max(1, base - top);

  // Subtle horizontal reference gridlines
  x.strokeStyle = 'rgba(148, 163, 184, 0.10)';
  x.lineWidth = 1;
  x.beginPath();
  x.moveTo(gutter, top + 0.5);
  x.lineTo(sw - padR, top + 0.5);
  x.moveTo(gutter, base + 0.5);
  x.lineTo(sw - padR, base + 0.5);
  x.stroke();

  if (hrs.length < 2) {
    x.strokeStyle = 'rgba(148, 163, 184, 0.18)';
    x.lineWidth = 1;
    x.setLineDash([4, 6]);
    x.beginPath();
    x.moveTo(gutter, sh / 2);
    x.lineTo(sw - padR, sh / 2);
    x.stroke();
    x.setLineDash([]);
    return;
  }

  var rawMn = Math.min.apply(null, hrs),
      rawMx = Math.max.apply(null, hrs);
  var mn = rawMn,
      mx = rawMx;
  if (mx - mn < 6) {
    mx += 3;
    mn -= 3;
  }

  var pts = [];
  for (i = 0; i < hrs.length; i++) {
    pts.push([
      gutter + (i / (hrs.length - 1)) * w,
      top + h - ((hrs[i] - mn) / (mx - mn)) * h
    ]);
  }

  var strokeCol = '#38bdf8',
      fillTop = 'rgba(56, 189, 248, 0.28)';
  if (currentSt === 'UNCHANGED') {
    strokeCol = '#fbbf24';
    fillTop = 'rgba(251, 191, 36, 0.22)';
  } else if (currentSt === 'STALE') {
    strokeCol = '#f87171';
    fillTop = 'rgba(248, 113, 113, 0.22)';
  }

  // Area fill under spline
  x.beginPath();
  drawSplinePath(x, pts);
  var lastPt = pts[pts.length - 1];
  x.lineTo(lastPt[0], base);
  x.lineTo(pts[0][0], base);
  x.closePath();
  var grad = x.createLinearGradient(0, top, 0, base);
  grad.addColorStop(0, fillTop);
  grad.addColorStop(1, 'rgba(56, 189, 248, 0.0)');
  x.fillStyle = grad;
  x.fill();

  // Main spline stroke
  x.lineWidth = Math.max(1.8, h / 20);
  x.lineJoin = 'round';
  x.lineCap = 'round';
  x.beginPath();
  drawSplinePath(x, pts);
  x.strokeStyle = strokeCol;
  x.stroke();

  // Glowing endpoint indicator on latest sample
  x.beginPath();
  x.arc(lastPt[0], lastPt[1], 3.5, 0, Math.PI * 2);
  x.fillStyle = '#ffffff';
  x.fill();
  x.lineWidth = 2;
  x.strokeStyle = strokeCol;
  x.stroke();

  // Min / Max gutter labels
  x.font = '600 12.5px "Segoe UI", system-ui, sans-serif';
  x.fillStyle = 'rgba(148, 163, 184, 0.78)';
  x.textAlign = 'right';
  x.textBaseline = 'middle';
  x.fillText(String(Math.round(rawMx)), gutter - 8, top);
  x.fillText(String(Math.round(rawMn)), gutter - 8, base);
}

/* ---- OFFLINE Blanking & Telemetry Polling ---------------------------- */
function blank() {
  $('hr').textContent = '--';
  $('activity').textContent = '--';
  $('activity').className = 'act';
  $('spo2').textContent = '--';
  $('ex').textContent = '--';
  $('steps').textContent = '--';
  var ppgEl = $('ppg');
  ppgEl.textContent = '--';
  ppgEl.className = 'big';
  $('mv').textContent = '--';
  $('mbar').style.width = '0%';
  hrs = [];
  draw();
}

function fmtUptime(s) {
  s = Number(s || 0);
  if (s < 60) return s + 's';
  if (s < 3600) return Math.floor(s / 60) + 'm';
  var h = Math.floor(s / 3600),
      m = Math.floor((s % 3600) / 60);
  return h + 'h' + (m < 10 ? '0' : '') + m + 'm';
}

function tick() {
  if (tickBusy) return;
  tickBusy = true;
  fetch('/api/telemetry')
    .then(function (r) { return r.json(); })
    .then(function (d) {
      tickBusy = false;
      var f = d.freshness || {},
          lk = d.link || {},
          L = d.latest;
      var st = f.state || 'OFFLINE';
      currentSt = st;
      freshLabel = f.label || 'no data yet';

      document.body.setAttribute('data-st', st);
      var chip = $('stChip');
      chip.textContent = st;
      chip.className = 'stpill ' + st;
      $('stAge').textContent = freshLabel;
      $('ageH').textContent = freshLabel;
      $('ageM').textContent = freshLabel;
      $('ageS').textContent = freshLabel;

      var lb = $('link');
      lb.textContent = lk.label || 'LINK';
      lb.className = 'badge ' + (lk.state === 'up' ? 'up' : 'down');
      lb.title = lk.detail || '';

      var lane = $('lane'),
          lsep = $('laneSep');
      if (d.lane) {
        lane.style.display = '';
        lane.textContent = d.lane;
        lsep.style.display = '';
      } else {
        lane.style.display = 'none';
        lsep.style.display = 'none';
      }

      var s = $('src');
      s.textContent = d.source;
      s.className = d.source === 'CAMERA' ? 'cam' : '';

      var disc = $('discText');
      if (disc) {
        disc.innerHTML =
          d.mode === 'band'
            ? 'Non-clinical wellness prototype &middot; Huawei Band 6 relay &mdash; no video stored or uploaded'
            : 'Sensors simulated &middot; camera motion only &mdash; no video stored or uploaded';
      }

      $('up').textContent = 'uptime ' + fmtUptime(d.uptime_s);
      $('rej').textContent =
        'rejected frames ' + (d.rejects === undefined ? '--' : d.rejects);

      $('vals').classList.toggle('dim', st === 'OFFLINE');
      $('vals2').classList.toggle('dim', st === 'OFFLINE');
      if (d.mode) updateModeUI(d.mode, d.locked);

      if (!L || st === 'OFFLINE') {
        blank();
        return;
      }

      var a = String(L.activity || '--').toUpperCase(),
          el = $('activity');
      el.textContent = a;
      el.className =
        'act ' +
        (a === 'REST' ? 'rest' : a === 'WALK' ? 'walk' : a === 'RUN' ? 'run' : '');

      var hrVal = Number(L.heart_rate) || 0;
      $('hr').textContent = L.heart_rate;
      if (hrVal >= 30 && hrVal <= 220) {
        var beatSec = Math.max(0.28, Math.min(2.0, 60.0 / hrVal)).toFixed(2) + 's';
        document.documentElement.style.setProperty('--beat-dur', beatSec);
      }

      $('spo2').textContent = L.spo2;
      $('ex').textContent = L.exertion;
      $('steps').textContent = L.steps;

      var ppgEl = $('ppg');
      ppgEl.textContent = L.ppg_quality;
      ppgEl.className = 'big ' + (L.ppg_quality === 'good' ? 'good' : 'poor');

      var mot = Math.max(0, Math.min(1, Number(L.motion_intensity) || 0));
      $('mv').textContent = Number(L.motion_intensity).toFixed(2);
      $('mbar').style.width = (mot * 100).toFixed(1) + '%';

      hrs = (d.history || []).slice(-60);
      draw();
    })
    .catch(function () {
      tickBusy = false;
      currentSt = 'OFFLINE';
      document.body.setAttribute('data-st', 'OFFLINE');
      var chip = $('stChip');
      chip.textContent = 'OFFLINE';
      chip.className = 'stpill OFFLINE';
      var lb = $('link');
      lb.textContent = 'LINK DOWN';
      lb.className = 'badge down';
      $('vals').classList.add('dim');
      $('vals2').classList.add('dim');
      blank();
    });
}

function setMode(m) {
  fetch('/api/mode', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode: m })
  })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (d.error) alert(d.error);
      updateModeUI(d.mode, d.locked);
      tick();
    })
    .catch(function () {});
}

function updateModeUI(m, locked) {
  var bb = $('btnModeBand'),
      br = $('btnModeRtos');
  if (bb && br) {
    bb.classList.toggle('active', m === 'band');
    br.classList.toggle('active', m === 'rtos');
    if (locked) {
      bb.disabled = true;
      br.disabled = true;
      bb.style.cursor = 'not-allowed';
      br.style.cursor = 'not-allowed';
      bb.style.opacity = m === 'band' ? '1.0' : '0.4';
      br.style.opacity = m === 'rtos' ? '1.0' : '0.4';
      bb.title = 'Locked: live telemetry piped from stdin';
      br.title = 'Locked: live telemetry piped from stdin';
    } else {
      bb.disabled = false;
      br.disabled = false;
      bb.style.cursor = 'pointer';
      br.style.cursor = 'pointer';
      bb.style.opacity = '';
      br.style.opacity = '';
      bb.title = 'Switch to Huawei Band 6 mode';
      br.title = 'Switch to FreeRTOS control mode';
    }
  }
}

/* ---- Natural Female Speech Synthesis (Jenny / Aria / Zira) ----------- */
var currentAudio = null;

function stopSpeech() {
  if (currentAudio) {
    try {
      currentAudio.pause();
      currentAudio.currentTime = 0;
      currentAudio.src = '';
    } catch (e) {}
    currentAudio = null;
  }
  if (window.speechSynthesis) {
    try {
      window.speechSynthesis.cancel();
    } catch (e) {}
  }
}

function speakNaturalFemale(text) {
  stopSpeech();
  if (!window.speechSynthesis || !ttsOn) return;
  try {
    var clean = text.replace(/[*_#`~]/g, '').trim();
    var u = new SpeechSynthesisUtterance(clean);
    var voices = window.speechSynthesis.getVoices();
    var v =
      voices.find(function (x) {
        var n = x.name.toLowerCase();
        return (
          (n.includes('natural') ||
            n.includes('jenny') ||
            n.includes('aria') ||
            n.includes('zira') ||
            n.includes('female')) &&
          x.lang.startsWith('en')
        );
      }) ||
      voices.find(function (x) {
        return (
          x.lang.startsWith('en') &&
          !x.name.toLowerCase().includes('david') &&
          !x.name.toLowerCase().includes('george') &&
          !x.name.toLowerCase().includes('male')
        );
      });
    if (v) u.voice = v;
    u.rate = 1.0;
    u.pitch = 1.05;
    window.speechSynthesis.speak(u);
  } catch (e) {
    console.error('Speech synthesis error:', e);
  }
}

function speakAudio(text) {
  stopSpeech();
  if (!ttsOn || !text) return;
  var clean = text.replace(/[*_#`~]/g, '').trim();
  if (!clean) return;
  try {
    var a = new Audio('/api/tts?text=' + encodeURIComponent(clean));
    currentAudio = a;
    a.onended = function () {
      if (currentAudio === a) currentAudio = null;
    };
    a.onerror = function () {
      if (currentAudio === a) currentAudio = null;
      speakNaturalFemale(clean);
    };
    a.play().catch(function () {
      if (currentAudio === a) currentAudio = null;
      speakNaturalFemale(clean);
    });
  } catch (e) {
    speakNaturalFemale(clean);
  }
}

if (window.speechSynthesis) {
  window.speechSynthesis.onvoiceschanged = function () {
    window.speechSynthesis.getVoices();
  };
}

/* ---- Smart Camera Presence Detection & Zero-GC Motion Loop ----------- */
var camStream = null,
    camHasPrev = false,
    camEMA = 0,
    camSenseTimer = null,
    camPostTimer = null;
var camBufA = new Float32Array(64 * 48),
    camBufB = new Float32Array(64 * 48);
var presenceSeconds = 0,
    autoGreetDone = false,
    lastGreetTimestamp = 0;
var faceDetector = null,
    lastHasFace = false,
    personInFrame = false;
var emoCanvas = null,
    emoCtx = null;

if ('FaceDetector' in window) {
  try {
    faceDetector = new FaceDetector({ fastMode: true, maxDetectedFaces: 1 });
  } catch (e) {}
}

function updatePresenceUI(state, text, msg) {
  var b = $('presBadge');
  if (b) {
    b.className = 'pres-badge ' + state;
    b.innerHTML = text;
  }
  if (msg) {
    $('camMsg').textContent = msg;
  }
  var bar = $('presBar');
  if (bar) {
    var pct = Math.min(100, Math.max(0, (presenceSeconds / 5) * 100));
    bar.style.width = pct + '%';
  }
}

function checkPresenceAndGreet() {
  var v = $('camVideo');
  if (!v || !v.videoWidth || !camStream) {
    presenceSeconds = 0;
    updatePresenceUI(
      'idle',
      '<span class="dot"></span>No person detected',
      'Click above to activate camera mirror.'
    );
    return;
  }

  if (personInFrame) {
    presenceSeconds++;
    if (presenceSeconds < 5) {
      updatePresenceUI(
        'detecting',
        '<span class="dot"></span>Person in range: ' + presenceSeconds + 's / 5s',
        'Person detected! Hold position in front of mirror (' +
          presenceSeconds +
          '/5s to greeting)...'
      );
    } else {
      updatePresenceUI(
        'active',
        '<span class="dot"></span>User Present (' + presenceSeconds + 's)',
        'User recognized in mirror range! Health assistant is listening.'
      );
      if (!autoGreetDone && Date.now() - lastGreetTimestamp > 45000) {
        autoGreetDone = true;
        lastGreetTimestamp = Date.now();
        var m = $('msgs');
        m.innerHTML +=
          '<div class="m system">User detected for 5s &mdash; Spoken greeting activated!</div>';
        m.scrollTop = m.scrollHeight;
        sendText(
          'Hello mirror, I just stepped in front of you. Greet me, note my live stats and how I look, and give me a quick health suggestion.',
          true
        );
      }
    }
  } else {
    if (presenceSeconds > 0) {
      presenceSeconds = Math.max(0, presenceSeconds - 2);
    }
    if (presenceSeconds === 0) {
      updatePresenceUI(
        'idle',
        '<span class="dot"></span>No person detected',
        'Mirror idle — Step into camera range to activate.'
      );
      if (autoGreetDone && Date.now() - lastGreetTimestamp > 10000) {
        autoGreetDone = false;
      }
    } else {
      var bar = $('presBar');
      if (bar) bar.style.width = Math.min(100, (presenceSeconds / 5) * 100) + '%';
    }
  }
}

var lastEmoPost = 0,
    emoBusy = false;
function postEmotionSnapshot(force) {
  var now = Date.now();
  if (!camStream || emoBusy || (!force && now - lastEmoPost < 3500)) return;
  var v = $('camVideo');
  if (!v || !v.videoWidth) return;
  lastEmoPost = now;
  emoBusy = true;
  try {
    if (!emoCanvas) {
      emoCanvas = document.createElement('canvas');
      emoCanvas.width = 320;
      emoCanvas.height = 240;
      emoCtx = emoCanvas.getContext('2d');
    }
    emoCtx.drawImage(v, 0, 0, 320, 240);
    var dataUrl = emoCanvas.toDataURL('image/jpeg', 0.6);
    fetch('/api/emotion', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image: dataUrl, person: personInFrame })
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        emoBusy = false;
        var eb = $('emoBadge');
        if (eb) {
          if (!personInFrame) {
            eb.className = 'pres-badge idle';
            eb.innerHTML = '<span class="dot"></span>Mood: --';
          } else if (d && d.vision && d.vision.label) {
            eb.style.display = 'inline-flex';
            var raw = d.vision.raw_emotion;
            var cls =
              raw === 'Anger' || raw === 'Fear' || raw === 'Sadness'
                ? 'detecting'
                : 'active';
            eb.className = 'pres-badge ' + cls;
            eb.innerHTML = '<span class="dot"></span>Mood: ' + esc(d.vision.label);
          }
        }
      })
      .catch(function () {
        emoBusy = false;
      });
  } catch (e) {
    emoBusy = false;
  }
}

function camStop() {
  if (camSenseTimer) {
    clearInterval(camSenseTimer);
    camSenseTimer = null;
  }
  if (camPostTimer) {
    clearInterval(camPostTimer);
    camPostTimer = null;
  }
  if (camStream) {
    camStream.getTracks().forEach(function (t) { t.stop(); });
    camStream = null;
  }
  camHasPrev = false;
  presenceSeconds = 0;
  autoGreetDone = false;
  personInFrame = false;
  var v = $('camVideo'),
      cw = $('camWrap');
  v.removeAttribute('srcObject');
  v.srcObject = null;
  v.style.display = 'none';
  if (cw) cw.style.display = 'none';
  $('camBtn').textContent = 'Activate Camera Mirror';
  updatePresenceUI(
    'idle',
    '<span class="dot"></span>No person detected',
    'Click above to activate camera mirror.'
  );
  var eb = $('emoBadge');
  if (eb) {
    eb.style.display = 'none';
    eb.className = 'pres-badge idle';
    eb.innerHTML = '<span class="dot"></span>Mood: --';
  }
}

function camFrame() {
  var v = $('camVideo');
  if (!v || !v.videoWidth) return;

  if (faceDetector) {
    faceDetector
      .detect(v)
      .then(function (faces) {
        lastHasFace = faces.length > 0;
      })
      .catch(function () {});
  }

  var c = $('camCanvas'),
      x = c.getContext('2d', { willReadFrequently: true });
  x.drawImage(v, 0, 0, 64, 48);
  var d = x.getImageData(0, 0, 64, 48).data;
  var nPixels = 64 * 48;

  for (var p = 0, i = 0; p < nPixels; p++, i += 4) {
    camBufA[p] = (d[i] + d[i + 1] + d[i + 2]) * 0.3333333;
  }

  if (camHasPrev) {
    var totalDiff = 0,
        centerDiff = 0,
        centerCount = 0;
    for (var y = 0; y < 48; y++) {
      var rowOffset = y * 64;
      var inCenterY = y >= 10 && y <= 38;
      for (var col = 0; col < 64; col++) {
        var idx = rowOffset + col;
        var diff = Math.abs(camBufA[idx] - camBufB[idx]);
        totalDiff += diff;
        if (inCenterY && col >= 14 && col <= 50) {
          centerDiff += diff;
          centerCount++;
        }
      }
    }
    var raw = totalDiff / nPixels / 255.0;
    var centerRaw = centerDiff / (centerCount || 1) / 255.0;
    camEMA = camEMA + 0.3 * (raw - camEMA);
    personInFrame = lastHasFace || centerRaw >= 0.010 || raw >= 0.014;
  }

  // Swap pre-allocated typed buffers (zero GC pressure)
  var tmp = camBufB;
  camBufB = camBufA;
  camBufA = tmp;
  camHasPrev = true;
}

function camPost() {
  fetch('/api/camera_ingest', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ motion: camEMA, activity: 'auto' })
  }).catch(function () {});
  postEmotionSnapshot(false);
  checkPresenceAndGreet();
}

$('camBtn').onclick = function () {
  if (camStream) {
    camStop();
    return;
  }
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    $('camMsg').textContent = 'No camera in this browser — staying on mock engine.';
    return;
  }
  var btn = this;
  navigator.mediaDevices
    .getUserMedia({ video: true })
    .then(function (stream) {
      camStream = stream;
      camEMA = 0;
      camHasPrev = false;
      presenceSeconds = 0;
      var v = $('camVideo'),
          cw = $('camWrap');
      v.srcObject = stream;
      v.style.display = 'block';
      if (cw) cw.style.display = 'block';
      btn.textContent = 'Stop Camera';
      updatePresenceUI(
        'idle',
        '<span class="dot"></span>Scanning...',
        'Camera live — stand in front of mirror.'
      );
      var eb = $('emoBadge');
      if (eb) {
        eb.style.display = 'inline-flex';
        eb.className = 'pres-badge idle';
        eb.innerHTML = '<span class="dot"></span>Mood: Scanning...';
      }
      camSenseTimer = setInterval(camFrame, 250);
      camPostTimer = setInterval(camPost, 1000);
    })
    .catch(function () {
      $('camMsg').textContent = 'Camera blocked — allow camera permission in browser.';
    });
};

setInterval(function () {
  $('clock').textContent = new Date().toLocaleTimeString();
}, 1000);

/* ---- Grounded Assistant & Voice Input -------------------------------- */
var ttsOn = true,
    recorder = null,
    chunks = [],
    recTimer = null,
    chatOpen = true;

function send() {
  var i = $('q');
  sendText(i.value.trim());
  i.value = '';
}

function sendText(q, isAuto) {
  if (!q) return;
  stopSpeech();
  postEmotionSnapshot(true);
  var m = $('msgs');
  if (!isAuto) {
    m.innerHTML +=
      '<div class="m user"><span class="m-tag">YOU</span><span class="m-body">' +
      esc(q) +
      '</span></div>';
  }
  var waitId = 'wait_' + Date.now();
  m.innerHTML +=
    '<div class="m assistant" id="' +
    waitId +
    '"><span class="m-tag">KINETIQ</span><span class="m-body" style="color:var(--fg-3)">Analyzing live telemetry<span class="typing-dots"><span></span><span></span><span></span></span></span></div>';
  m.scrollTop = m.scrollHeight;

  var useBackendTTS = !('speechSynthesis' in window) && ttsOn;
  fetch('/api/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ q: q, tts: useBackendTTS })
  })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      var waitEl = $(waitId);
      if (waitEl) waitEl.remove();
      var g = d.grounding || 'FAIL',
          cls = g === 'PASS' ? 'pass' : 'fail';
      var reply = d.answer || d.error || '?';
      m.innerHTML +=
        '<div class="m assistant"><span class="m-tag">KINETIQ</span><span class="m-body">' +
        esc(reply) +
        '<span class="g ' +
        cls +
        '">' +
        esc(g) +
        '</span>' +
        '<span class="age" style="display:inline;margin-left:8px">' +
        esc(freshLabel) +
        '</span></span></div>';
      m.scrollTop = m.scrollHeight;
      var lr = $('lastReply');
      lr.textContent = reply;
      lr.title = reply;
      if (ttsOn && d.answer) {
        speakAudio(d.answer);
      }
    })
    .catch(function () {
      var waitEl = $(waitId);
      if (waitEl) waitEl.remove();
      m.innerHTML +=
        '<div class="m system">Assistant unavailable &mdash; check server connection.</div>';
      m.scrollTop = m.scrollHeight;
    });
}

function setChat(open) {
  chatOpen = open;
  $('chat').classList.toggle('collapsed', !open);
  $('q').placeholder = open ? 'How am I doing?' : 'Ask\u2026';
  var b = $('chatToggle');
  b.innerHTML = open ? '&#9662;' : '&#9652;';
  b.title = open ? 'Collapse assistant' : 'Expand assistant';
  b.setAttribute('aria-expanded', open ? 'true' : 'false');
  if (open) {
    var m = $('msgs');
    m.scrollTop = m.scrollHeight;
  }
}

$('chatToggle').onclick = function () {
  setChat(!chatOpen);
};

$('spk').onclick = function () {
  ttsOn = !ttsOn;
  if (!ttsOn) stopSpeech();
  this.textContent = ttsOn ? 'Voice On' : 'Voice Muted';
  this.classList.toggle('primary', ttsOn);
};

function micHint(t) {
  var m = $('msgs');
  m.innerHTML +=
    '<div class="m system">' +
    esc(t) +
    ' &mdash; type instead, text chat keeps working.</div>';
  m.scrollTop = m.scrollHeight;
}

function stopMic() {
  if (recTimer) {
    clearTimeout(recTimer);
    recTimer = null;
  }
  if (recorder && recorder.state !== 'inactive') {
    try {
      recorder.stop();
    } catch (e) {}
  }
  var b = $('mic');
  b.classList.remove('rec');
  b.textContent = 'Mic';
}

$('mic').onclick = function () {
  stopSpeech();
  if (recorder && recorder.state !== 'inactive') {
    stopMic();
    return;
  }
  if (!window.MediaRecorder || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    micHint('no microphone in this browser');
    return;
  }
  var btn = this;
  navigator.mediaDevices
    .getUserMedia({ audio: true })
    .then(function (stream) {
      stopSpeech();
      chunks = [];
      try {
        recorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
      } catch (e) {
        recorder = new MediaRecorder(stream);
      }
      recorder.ondataavailable = function (e) {
        if (e.data && e.data.size) chunks.push(e.data);
      };
      recorder.onstop = function () {
        stream.getTracks().forEach(function (t) { t.stop(); });
        if (!chunks.length) {
          micHint('heard nothing, try again');
          return;
        }
        var blob = new Blob(chunks, { type: 'audio/webm' }),
            m = $('msgs');
        var statusId = 'status_' + Date.now();
        m.innerHTML +=
          '<div class="m system" id="' +
          statusId +
          '">Transcribing voice input<span class="typing-dots"><span></span><span></span><span></span></span></div>';
        m.scrollTop = m.scrollHeight;
        fetch('/api/transcribe', {
          method: 'POST',
          headers: { 'Content-Type': 'application/octet-stream' },
          body: blob
        })
          .then(function (r) { return r.json(); })
          .then(function (d) {
            var statusEl = $(statusId);
            if (statusEl) statusEl.remove();
            if (d.text) {
              sendText(d.text);
            } else {
              micHint('could not hear that (' + (d.error || '?') + ')');
            }
          })
          .catch(function () {
            var statusEl = $(statusId);
            if (statusEl) statusEl.remove();
            micHint('transcription failed');
          });
      };
      recorder.start();
      btn.classList.add('rec');
      btn.textContent = 'Listening...';
      recTimer = setTimeout(stopMic, 10000);
    })
    .catch(function () {
      micHint('mic blocked — allow microphone permission, or type instead');
    });
};

$('send').onclick = send;
$('q').onkeydown = function (e) {
  if (e.key === 'Enter') send();
};

sizeSpark();
setInterval(tick, 1000);
tick();
window.addEventListener('resize', function () {
  sizeSpark();
  draw();
});
document.addEventListener('visibilitychange', function () {
  if (!document.hidden) {
    sizeSpark();
    draw();
  }
});