/* KinetiQ — Smart Mirror / Home Edition (Reactive Frontend Engine) */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };

  /* -------------------- MONOTONIC SEQUENCE GUARDS -------------------- */
  var askRequestSeq = 0;
  var speechSeq = 0;
  var userInteracted = false;

  /* -------------------- STATE -------------------- */
  var state = {
    tickBusy: false,
    mode: 'band',
    aiModel: 'qwen3.5:4b',
    locked: false,
    freshState: 'OFFLINE',
    freshLabel: 'waiting for first packet',
    fastFresh: false,
    latest: null,
    bandMeta: {},
    rtosDiag: {},
    history: [],
    currentHr: 0,
    ttsOn: true,
    camStream: null,
    camVisible: false,
    personInFrame: false,
    presenceSeconds: 0,
    autoGreetDone: false,
    lastGreetTs: 0,
    emotionLabel: '',
    emotionRaw: '',
    faceDetected: false,
    faceBox: null,
    motionEMA: 0,
    motionCenterX: 0.5,
    motionCenterY: 0.44,
    lastReceipt: null,
    lastAnswerText: '',
    wellAcked: false,
    snapAcked: false,
    orbMode: 'idle', // idle | listening | thinking | speaking
    orbEnergy: 0,
    breathTimerId: null,
    breathRemaining: 60
  };

  /* -------------------- HELPERS -------------------- */
  function esc(str) {
    return String(str == null ? '' : str).replace(/[&<>"']/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c];
    });
  }

  function fmtTimeShort(d) {
    d = d || new Date();
    var h = String(d.getHours()).padStart(2, '0');
    var m = String(d.getMinutes()).padStart(2, '0');
    return h + ':' + m;
  }

  function fmtHeaderClock() {
    var now = new Date();
    var days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    var months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    var h = now.getHours();
    var ampm = h >= 12 ? 'PM' : 'AM';
    var h12 = h % 12 || 12;
    var min = String(now.getMinutes()).padStart(2, '0');
    return days[now.getDay()] + ', ' + months[now.getMonth()] + ' ' + now.getDate() + ', ' + h12 + ':' + min + ' ' + ampm;
  }

  function fmtUptime(s) {
    s = Number(s) || 0;
    if (s < 60) return s + 's';
    var m = Math.floor(s / 60);
    if (m < 60) return m + 'm ' + (s % 60) + 's';
    return Math.floor(m / 60) + 'h ' + (m % 60) + 'm';
  }

  function stressBand(score) {
    if (score <= 29) return 'Calm';
    if (score <= 59) return 'Balanced';
    if (score <= 79) return 'Elevated';
    return 'High';
  }

  function titleCase(str) {
    str = String(str || '').trim();
    if (!str) return '--';
    return str.charAt(0).toUpperCase() + str.slice(1).toLowerCase();
  }

  function getActiveMood() {
    var mp = window.KinetiQFace;
    if (mp && mp.ready && mp.faceDetected) {
      return mp.mpMood || 'Calm';
    }
    return state.emotionLabel || '';
  }

  /* -------------------- CLOCK & GREETING -------------------- */
  function updateClock() {
    var el = $('clock');
    if (el) el.textContent = fmtHeaderClock();
  }

  function updateGreeting() {
    var title = $('greetingTitle');
    var eye = $('greetingEyebrow');
    if (!title || !eye) return;

    var hr = new Date().getHours();
    var timeGreet = hr < 12 ? 'Good morning.' : hr < 17 ? 'Good to see you.' : 'Good evening.';

    if (state.mode === 'rtos' && state.rtosDiag && state.rtosDiag.override_mode === 'fall') {
      eye.textContent = 'FREERTOS CONTROL · EVENT TEST';
      title.textContent = 'Simulated fall impulse.';
      return;
    }
    if (state.personInFrame && state.presenceSeconds >= 5) {
      var actMood = getActiveMood();
      if (actMood && actMood.toLowerCase() !== 'calm') {
        eye.textContent = 'YOUR SPACE · LOOKING ' + actMood.toUpperCase();
      } else if (actMood) {
        eye.textContent = 'YOUR SPACE · FEELING CALM';
      } else {
        eye.textContent = 'YOUR SPACE · PRESENCE ACTIVE';
      }
      title.textContent = timeGreet;
    } else if (state.personInFrame && state.presenceSeconds > 0) {
      eye.textContent = 'CHECKING IN · ' + state.presenceSeconds + 'S / 5S';
      title.textContent = 'Hold steady for check-in.';
    } else {
      eye.textContent = 'YOUR SPACE · YOUR PACE';
      title.textContent = timeGreet;
    }
  }

  /* -------------------- TABS (CALM OVERVIEW / DETAILED READINGS) -------------------- */
  function initTabs() {
    var tabs = document.querySelectorAll('.seg-tab');
    tabs.forEach(function (btn) {
      btn.addEventListener('click', function () {
        var target = btn.getAttribute('data-tab');
        tabs.forEach(function (b) {
          var active = b === btn;
          b.classList.toggle('active', active);
          b.setAttribute('aria-selected', active ? 'true' : 'false');
        });
        $('tabCalm').hidden = target !== 'calm';
        $('tabDetail').hidden = target !== 'detail';
        if (target === 'detail') drawSparkline();
      });
    });
  }

  /* -------------------- MODE & RTOS CONTROLS -------------------- */
  function setMode(m) {
    if (state.locked) return;
    fetch('/api/mode', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: m })
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.error && d.locked) {
          updateSnapshotCard('Mode locked (stdin pipe)', d.error, 'SNAPSHOT');
          return;
        }
        state.mode = d.mode || m;
        state.snapAcked = false;
        syncModeButtons();
        tick();
      })
      .catch(function () {});
  }

  function syncModeButtons() {
    document.body.setAttribute('data-mode', state.mode);
    var bBand = $('btnModeBand');
    var bRtos = $('btnModeRtos');
    if (bBand) {
      bBand.classList.toggle('active', state.mode === 'band');
      bBand.disabled = !!state.locked;
    }
    if (bRtos) {
      bRtos.classList.toggle('active', state.mode === 'rtos');
      bRtos.disabled = !!state.locked;
    }
    var sw = $('btnSnapSwitch');
    if (sw) {
      sw.textContent = state.mode === 'band' ? 'Switch to FreeRTOS lane' : 'Switch to Band 6 lane';
      sw.disabled = !!state.locked;
    }
  }

  function sendRtosCommand(cmd) {
    var postCmd = function () {
      fetch('/api/rtos/command', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cmd: cmd })
      })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d.ok) {
            highlightRtosCmd(cmd);
            tick();
          }
        })
        .catch(function () {});
    };

    if (state.mode !== 'rtos' && !state.locked) {
      fetch('/api/mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: 'rtos' })
      })
        .then(function () {
          state.mode = 'rtos';
          syncModeButtons();
          setTimeout(postCmd, 250);
        })
        .catch(function () {});
    } else {
      postCmd();
    }
  }

  function highlightRtosCmd(activeCmd) {
    document.querySelectorAll('#rtosCmdRow .cmd-btn').forEach(function (btn) {
      btn.classList.toggle('active', btn.getAttribute('data-cmd') === activeCmd);
    });
  }

  function toggleFastFreshness() {
    var next = !state.fastFresh;
    fetch('/api/freshness_mode', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ fast: next })
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && d.freshness) {
          state.fastFresh = !!d.freshness.fast_mode;
          updateFastFreshUI();
          tick();
        }
      })
      .catch(function () {});
  }

  function updateFastFreshUI() {
    var lbl = $('fastFreshLabel');
    if (lbl) {
      lbl.textContent = state.fastFresh ? '15s / 30s (Demo)' : '60s / 300s';
    }
  }

  /* -------------------- TELEMETRY POLLING & RENDERING -------------------- */
  function setTileSub(elId, text) {
    var el = $(elId);
    if (!el) return;
    el.innerHTML = '<span class="sub-dots"></span><span>' + esc(text) + '</span>';
  }

  function blankMetrics(freshLabel) {
    state.currentHr = 0;
    $('valHr').textContent = '--';
    $('valSpo2').textContent = '--';
    $('valSteps').textContent = '--';
    $('valActivity').textContent = '--';
    $('valStress').textContent = '--';
    var msg = freshLabel || 'No live data — blanked';
    setTileSub('subHr', msg);
    setTileSub('subSpo2', msg);
    setTileSub('subSteps', msg);
    setTileSub('subActivity', 'Values blanked when offline');
    $('subStress').textContent = state.mode === 'band'
      ? 'Waiting for Huawei Band 6 relay'
      : 'Waiting for FreeRTOS v11 telemetry';
    $('breathHr').textContent = 'HR -- bpm';
    var bBtn = $('btnStartBreathe');
    if (bBtn) bBtn.classList.remove('suggest-breathe');
  }

  function renderTelemetry(d) {
    var f = d.freshness || {};
    var lk = d.link || {};
    var L = d.latest || null;
    var bm = d.band_meta || {};
    var rd = d.rtos_diag || {};

    state.latest = L;
    state.bandMeta = bm;
    state.rtosDiag = rd;
    if (d.ai_model) state.aiModel = d.ai_model;
    state.mode = d.mode || state.mode;
    state.locked = !!d.locked;
    state.freshState = f.state || 'OFFLINE';
    state.freshLabel = f.label || 'waiting for first packet';
    state.fastFresh = !!f.fast_mode;
    state.history = (d.history || []).slice(-60);

    document.body.setAttribute('data-st', state.freshState);
    syncModeButtons();
    updateFastFreshUI();

    // Top-right snapshot freshness pill
    var fp = $('freshPill');
    if (fp) {
      fp.className = 'status-outline-pill ' + state.freshState;
      if (state.mode === 'rtos' && state.freshState === 'LIVE') {
        fp.textContent = 'SYNTHETIC · 1 HZ';
      } else if (state.freshState === 'LIVE' && f.age_s != null) {
        fp.textContent = 'LIVE · ' + Math.round(f.age_s) + 'S AGO';
      } else if (state.freshState === 'STALE' && f.age_s != null) {
        fp.textContent = 'STALE · ' + Math.round(f.age_s) + 'S';
      } else {
        fp.textContent = state.freshState;
      }
    }

    // Mirror top-right provenance pill (carbon copy of SIMULATED MIRROR FEED slot)
    var sp = $('sourcePill');
    var spt = $('sourcePillText');
    if (sp && spt) {
      sp.className = 'frosted-pill source-pill';
      if (state.mode === 'rtos') {
        sp.classList.add(state.freshState === 'OFFLINE' ? 'is-offline' : 'is-rtos');
        spt.textContent = state.freshState === 'OFFLINE'
          ? 'FREERTOS v11 · STANDBY'
          : 'FREERTOS v11 · SYNTHETIC';
      } else {
        if (state.freshState === 'LIVE') sp.classList.add('is-live');
        else if (state.freshState === 'STALE') sp.classList.add('is-stale');
        else sp.classList.add('is-offline');

        if (state.freshState === 'OFFLINE') {
          spt.textContent = 'HUAWEI BAND 6 · OFFLINE';
        } else {
          spt.textContent = 'HUAWEI BAND 6 · ' + state.freshState;
        }
      }
    }

    // Footer honest status
    var fr = $('footerRight');
    if (fr) {
      var modeDesc = state.mode === 'band'
        ? ('Band 6' + (bm.device_model ? ' (' + bm.device_model + ')' : '') + (bm.transport ? ' · ' + bm.transport : ''))
        : ('FreeRTOS v11 synthetic' + (rd.running ? ' · native C' : ''));
      var fastTag = state.fastFresh ? ' · Demo timing (15s/30s)' : '';
      fr.textContent = 'Local only · Zero cloud · ' + modeDesc + fastTag;
    }

    // Detailed tab stats
    $('detailFreshMeta').textContent = state.freshLabel;
    $('detailLink').textContent = (lk.state === 'up' ? 'UP' : 'DOWN');
    $('detailPpg').textContent = L ? String(L.ppg_quality || '--').toUpperCase() : '--';
    $('detailRej').textContent = String(d.rejects == null ? 0 : d.rejects);
    $('detailUp').textContent = fmtUptime(d.uptime_s);

    // FreeRTOS Kernel Inspector
    renderRtosInspector(rd);

    // Blank cleanly on OFFLINE
    if (!L || state.freshState === 'OFFLINE') {
      blankMetrics(state.freshLabel);
      drawSparkline();
      updateWellbeingCard(null, bm);
      updateSnapshotCardFromState(null, bm, rd);
      return;
    }

    // Live BPM pulse variable
    var hrVal = Number(L.heart_rate) || 0;
    state.currentHr = hrVal;
    if (hrVal >= 30 && hrVal <= 220) {
      var beatSec = Math.max(0.32, Math.min(1.6, 60.0 / hrVal)).toFixed(2) + 's';
      document.documentElement.style.setProperty('--beat-dur', beatSec);
    }
    $('breathHr').textContent = 'HR ' + hrVal + ' bpm';

    var isBand = state.mode === 'band';

    // Tile 1: Heart rate
    $('valHr').textContent = String(hrVal);
    var hrModeLabel = isBand
      ? ((bm.heart_rate_mode ? titleCase(bm.heart_rate_mode) : 'Real-time') + ' · Band 6')
      : 'Synthetic · FreeRTOS v11';
    setTileSub('subHr', hrModeLabel);

    // Tile 2: SpO2
    $('valSpo2').textContent = String(L.spo2);
    var spo2Label = isBand
      ? ((bm.spo2_mode === 'historical' ? 'Last completed' : (bm.spo2_mode ? titleCase(bm.spo2_mode) : 'Last completed')) + ' · Band 6')
      : 'Synthetic · FreeRTOS v11';
    setTileSub('subSpo2', spo2Label);

    // Tile 3: Steps today
    var stepsNum = Number(L.steps);
    $('valSteps').textContent = isNaN(stepsNum) ? '--' : stepsNum.toLocaleString();
    setTileSub('subSteps', isBand ? 'Daily count · Band 6' : 'Simulated steps · FreeRTOS');

    // Tile 4: Activity & motion (honest!)
    var actRaw = String(L.activity || 'unknown').toLowerCase();
    var motVal = Number(L.motion_intensity || 0);
    if (isBand && actRaw === 'unknown') {
      if (state.camStream && state.camVisible) {
        var camBand = state.motionEMA > 0.35 ? 'Active' : state.motionEMA > 0.1 ? 'Moving' : 'Still';
        $('valActivity').textContent = camBand;
        setTileSub('subActivity', 'Camera motion ' + state.motionEMA.toFixed(2));
      } else {
        $('valActivity').textContent = 'Unmeasured';
        setTileSub('subActivity', 'Not in Band 6 packet');
      }
    } else {
      $('valActivity').textContent = titleCase(actRaw);
      setTileSub('subActivity', 'Motion ' + motVal.toFixed(2) + ' · ' + titleCase(L.exertion));
    }

    // Wide Bottom Tile: Stress / readiness (Band 6 genuine stress OR RTOS exertion)
    var actMood = getActiveMood();
    var moodSuffix = (state.camStream && state.camVisible && actMood) ? (' · Mood: ' + actMood) : '';
    if (isBand) {
      $('stressLabel').textContent = 'Stress / readiness';
      if (bm.stress_score != null && bm.stress_score !== '') {
        var sc = Number(bm.stress_score);
        $('valStress').textContent = stressBand(sc) + ' · ' + sc + '/100';
        $('subStress').textContent = 'Genuine Band 6' + (bm.device_model ? ' · ' + bm.device_model : '') + ' · ' + state.freshLabel + moodSuffix;
      } else {
        $('valStress').textContent = 'Not reported';
        $('subStress').textContent = 'Exertion heuristic: ' + titleCase(L.exertion) + ' · Stress absent' + moodSuffix;
      }
    } else {
      $('stressLabel').textContent = 'Exertion / RTOS load';
      $('valStress').textContent = titleCase(L.exertion) + ' · PPG ' + String(L.ppg_quality || 'good');
      $('subStress').textContent = 'Synthetic control lane · ' + (rd.running ? 'native FreeRTOS v11' : 'host fallback') + moodSuffix;
    }

    drawSparkline();
    updateWellbeingCard(L, bm);
    updateSnapshotCardFromState(L, bm, rd);
  }

  function renderRtosInspector(rd) {
    var tag = $('rtosKindTag');
    if (tag) {
      if (state.mode === 'rtos' && rd.running) {
        tag.textContent = rd.source_kind === 'native_freertos_v11' ? 'NATIVE C · LIVE' : 'FALLBACK · LIVE';
      } else {
        tag.textContent = 'STANDBY';
      }
    }
    $('rtosTickTasks').textContent = (rd.tick || 0) + ' / ' + (rd.tasks || 0);
    $('rtosPeriod').textContent = (rd.measured_ms || 0) + ' / ' + (rd.period_ms || 1000);
    $('rtosLateness').textContent = (rd.max_lateness_ms || 0) + ' ms';
    $('rtosHeap').textContent = rd.heap_bytes ? (rd.heap_bytes / 1024).toFixed(1) + ' KB' : '-- KB';
    $('rtosDrops').textContent = String(rd.drops || 0);
    $('rtosMissed').textContent = String(rd.missed_deadlines || 0);

    var cap = Number(rd.queue_cap) || 8;
    var hwm = Math.min(cap, Number(rd.queue_hwm) || 0);
    $('rtosQueueFill').style.width = ((hwm / cap) * 100).toFixed(0) + '%';
    $('rtosQueueText').textContent = hwm + ' / ' + cap;
    if (rd.override_mode) highlightRtosCmd(rd.override_mode);
  }

  function tick() {
    if (state.tickBusy) return;
    state.tickBusy = true;
    fetch('/api/telemetry')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        state.tickBusy = false;
        renderTelemetry(d);
      })
      .catch(function () {
        state.tickBusy = false;
        state.freshState = 'OFFLINE';
        document.body.setAttribute('data-st', 'OFFLINE');
        blankMetrics('Server disconnected');
      });
  }

  /* -------------------- HEART RATE SPARKLINE -------------------- */
  function sizeSpark() {
    drawSparkline();
  }

  function drawSparkline() {
    var canvas = $('spark');
    if (!canvas) return;
    var ctx = canvas.getContext('2d');
    var w = canvas.width;
    var h = canvas.height;
    ctx.clearRect(0, 0, w, h);

    var vals = state.history;
    if (!vals || vals.length < 2) {
      ctx.fillStyle = 'rgba(108, 144, 149, 0.45)';
      ctx.font = '12px Inter, Segoe UI, sans-serif';
      ctx.fillText('Waiting for heart-rate samples...', 14, h / 2 + 4);
      return;
    }

    var minV = Math.min.apply(null, vals) - 5;
    var maxV = Math.max.apply(null, vals) + 5;
    if (maxV - minV < 12) { minV -= 6; maxV += 6; }

    var padX = 10, padY = 12;
    var stepX = (w - padX * 2) / Math.max(1, vals.length - 1);
    var pts = vals.map(function (v, i) {
      var x = padX + i * stepX;
      var norm = (v - minV) / (maxV - minV);
      var y = h - padY - norm * (h - padY * 2);
      return { x: x, y: y };
    });

    // Area gradient
    var grad = ctx.createLinearGradient(0, padY, 0, h);
    grad.addColorStop(0, state.mode === 'rtos' ? 'rgba(94, 194, 230, 0.28)' : 'rgba(77, 179, 173, 0.30)');
    grad.addColorStop(1, 'rgba(77, 179, 173, 0.0)');

    ctx.beginPath();
    ctx.moveTo(pts[0].x, pts[0].y);
    for (var i = 1; i < pts.length; i++) {
      var prev = pts[i - 1];
      var cur = pts[i];
      var midX = (prev.x + cur.x) / 2;
      ctx.quadraticCurveTo(prev.x, prev.y, midX, (prev.y + cur.y) / 2);
    }
    ctx.lineTo(pts[pts.length - 1].x, pts[pts.length - 1].y);

    // Fill
    ctx.save();
    ctx.lineTo(pts[pts.length - 1].x, h);
    ctx.lineTo(pts[0].x, h);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();
    ctx.restore();

    // Stroke
    ctx.strokeStyle = state.mode === 'rtos' ? '#5ec2e6' : '#73dcd5';
    ctx.lineWidth = 2.2;
    ctx.stroke();

    // Latest dot
    var last = pts[pts.length - 1];
    ctx.beginPath();
    ctx.arc(last.x, last.y, 4, 0, Math.PI * 2);
    ctx.fillStyle = '#e8fbfa';
    ctx.fill();
  }

  /* -------------------- DYNAMIC GUIDANCE CARDS (CARDS 2 & 3) -------------------- */
  function updateWellbeingCard(L, bm) {
    var bBtn = $('btnStartBreathe');
    var actMood = getActiveMood();
    var hr = L ? (Number(L.heart_rate) || 70) : 0;
    var stress = (state.mode === 'band' && bm && bm.stress_score != null) ? Number(bm.stress_score) : null;
    var needBreathe = state.freshState === 'LIVE' && ((stress != null && stress >= 60) || hr >= 105 || actMood === 'Angry' || actMood === 'Sad');
    if (bBtn) {
      bBtn.classList.toggle('suggest-breathe', !!needBreathe);
    }

    if (state.wellAcked) return;
    $('wellTime').textContent = fmtTimeShort();

    var mp = window.KinetiQFace;
    if (mp && mp.faceDetected && mp.posture !== 'upright' && mp.postureNote) {
      $('wellCat').textContent = 'POSTURE';
      $('wellTitle').textContent = 'A gentle posture reminder';
      $('wellBody').textContent = mp.postureNote;
      return;
    }

    if (!L || state.freshState === 'OFFLINE') {
      $('wellCat').textContent = 'WELLBEING';
      $('wellTitle').textContent = 'A calm pause is ready';
      $('wellBody').textContent = 'Once live readings arrive, KinetiQ adapts gentle hydration, posture, and 4-4-4 breathing nudges to your current state.';
      return;
    }

    var hr = Number(L.heart_rate) || 70;
    var stress = (state.mode === 'band' && bm.stress_score != null) ? Number(bm.stress_score) : null;

    if (state.mode === 'rtos' && L.motion_intensity >= 0.9 && L.activity === 'active') {
      $('wellCat').textContent = 'RTOS EVENT';
      $('wellTitle').textContent = 'Simulated fall impulse active';
      $('wellBody').textContent = 'High-motion synthetic control spike (0.95) triggered inside FreeRTOS v11. Reset to Auto when ready.';
      return;
    }

    if ((stress != null && stress >= 60) || hr >= 105) {
      $('wellCat').textContent = 'BREATHING';
      $('wellTitle').textContent = 'A moment to slow your breath';
      $('wellBody').textContent = stress != null
        ? 'Your Band 6 stress score is ' + stress + '/100 at ' + hr + ' bpm. A 60-second box breath can help settle your pace.'
        : 'Heart rate is elevated at ' + hr + ' bpm (' + L.exertion + '). Take a brief recovery breath or sip of water.';
    } else if (state.emotionLabel && /tired|sad|tense|stressed/i.test(state.emotionLabel)) {
      $('wellCat').textContent = 'CHECK-IN';
      $('wellTitle').textContent = 'Relax your shoulders';
      $('wellBody').textContent = 'Mirror presence noted a ' + state.emotionLabel.toLowerCase() + ' expression alongside ' + hr + ' bpm. A gentle stretch or water break may help.';
    } else {
      $('wellCat').textContent = 'WELLBEING';
      $('wellTitle').textContent = 'Steady baseline rhythm';
      $('wellBody').textContent = 'Heart rate is ' + hr + ' bpm with SpO2 at ' + L.spo2 + '%. A glass of water or a quick shoulder stretch pairs nicely with this calm pace.';
    }
  }

  function updateSnapshotCard(title, body, cat) {
    $('snapTime').textContent = fmtTimeShort();
    $('snapCat').textContent = cat || 'SNAPSHOT';
    $('snapTitle').textContent = title;
    $('snapBody').textContent = body;
  }

  function updateSnapshotCardFromState(L, bm, rd) {
    if (state.snapAcked) return;
    bm = bm || state.bandMeta || {};
    rd = rd || state.rtosDiag || {};

    if (!L || state.freshState === 'OFFLINE') {
      if (state.mode === 'band') {
        if (bm.receiver_up === false) {
          updateSnapshotCard(
            'Receiver offline (:8000)',
            'Run LAUNCH_FINAL_DEMO.bat to start the Band 6 receiver on port 8000.',
            'OFFLINE'
          );
        } else if (bm.missing_fields && bm.missing_fields.length > 0 && bm.partial_hr != null) {
          updateSnapshotCard(
            'Band 6 linked — incomplete frame',
            'Receiving HR ' + bm.partial_hr + ' bpm — open ' + bm.missing_fields.join(', ') + ' in Huawei Health to complete snapshot.',
            'WAITING'
          );
        } else {
          updateSnapshotCard(
            'Waiting for Huawei Band 6',
            'Receiver :8000 online. Open Huawei Health home screen and enable KinetiQ Streamer.',
            'OFFLINE'
          );
        }
      } else {
        updateSnapshotCard(
          'FreeRTOS control standby',
          'Switch to FreeRTOS to stream real-time 1 Hz native kernel frames with queue and lateness diagnostics.',
          'OFFLINE'
        );
      }
      return;
    }

    if (state.freshState === 'STALE') {
      updateSnapshotCard(
        'Telemetry stream is stale',
        'No new packet has arrived recently (' + state.freshLabel + '). Values will automatically blank if transport stays offline.',
        'STALE'
      );
      return;
    }

    if (state.mode === 'band') {
      var stressBit = bm.stress_score != null ? ', stress ' + bm.stress_score + '/100' : '';
      updateSnapshotCard(
        'Verified Band 6 snapshot',
        L.heart_rate + ' bpm, ' + L.spo2 + '% SpO2' + stressBit + ', and ' + Number(L.steps).toLocaleString() + ' steps (' + state.freshLabel + ').',
        'BAND 6 · ' + state.freshState
      );
    } else {
      updateSnapshotCard(
        'FreeRTOS v11 kernel active',
        'Native C node streaming ' + String(L.activity).toUpperCase() + ' (' + L.heart_rate + ' bpm) · queue HWM ' + (rd.queue_hwm || 0) + '/' + (rd.queue_cap || 8) + ' · ' + (rd.missed_deadlines || 0) + ' missed deadlines.',
        'RTOS · SYNTHETIC'
      );
    }
  }

  /* -------------------- CAMERA, MOTION, EMOTION & 5S PRESENCE -------------------- */
  var camBufA = new Float32Array(64 * 48);
  var camBufB = new Float32Array(64 * 48);
  var camHasPrev = false;
  var emoBusy = false;
  var lastEmoTs = 0;
  var emoCanvas = null;
  var emoCtx = null;

  function setPreviewPill(text, isLive, mood) {
    var pt = $('previewText');
    if (pt) pt.textContent = text;
    var pill = $('previewPill');
    if (pill) {
      if (mood) {
        pill.setAttribute('data-mood', String(mood).toLowerCase());
      } else {
        pill.removeAttribute('data-mood');
      }
    }
    var stage = $('mirrorStage');
    if (stage) stage.classList.toggle('cam-live', !!isLive);
  }

  function startCamera() {
    if (state.camStream) {
      state.camVisible = true;
      $('mirrorStage').classList.add('cam-live');
      $('camToggleBtn').textContent = 'Hide preview';
      setPreviewPill('PREVIEW ACTIVE', true);
      return;
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      $('matteSub').textContent = 'Camera API unavailable in this browser context.';
      return;
    }
    $('matteTitle').textContent = 'Connecting mirror camera...';
    navigator.mediaDevices.getUserMedia({
      video: { width: { ideal: 640 }, height: { ideal: 360 }, frameRate: { ideal: 24, max: 30 }, facingMode: 'user' },
      audio: false
    })
      .then(function (stream) {
        state.camStream = stream;
        state.camVisible = true;
        var v = $('camVideo');
        v.srcObject = stream;
        v.play().catch(function () {});
        $('mirrorStage').classList.add('cam-live');
        $('camToggleBtn').textContent = 'Hide preview';
        setPreviewPill('PREVIEW ACTIVE', true);
      })
      .catch(function () {
        $('matteTitle').textContent = 'Mirror camera on standby';
        $('matteSub').textContent = 'Allow webcam access or click below to enable live reflection and presence detection.';
        $('camToggleBtn').textContent = 'Enable preview';
        setPreviewPill('CAMERA STANDBY', false);
      });
  }

  function toggleCameraPreview() {
    if (!state.camStream) {
      startCamera();
      return;
    }
    state.camVisible = !state.camVisible;
    $('mirrorStage').classList.toggle('cam-live', state.camVisible);
    $('camToggleBtn').textContent = state.camVisible ? 'Hide preview' : 'Show preview';
    if (!state.camVisible) {
      $('matteTitle').textContent = 'Preview paused';
      $('matteSub').textContent = 'Telemetry and voice assistance remain active. Click Show preview to return to live reflection.';
      setPreviewPill('PREVIEW HIDDEN', false);
    } else {
      setPreviewPill('PREVIEW ACTIVE', true);
    }
  }

  function sampleCameraMotion() {
    var v = $('camVideo');
    var c = $('camCanvas');
    if (!state.camStream || !v || !v.videoWidth || !c) return;
    var ctx = c.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(v, 0, 0, 64, 48);
    var img = ctx.getImageData(0, 0, 64, 48).data;

    var diffSum = 0;
    var weightSum = 0;
    var wx = 0;
    var wy = 0;
    var lumaMean = 0;

    for (var i = 0, p = 0; p < 64 * 48; i += 4, p++) {
      var y = 0.299 * img[i] + 0.587 * img[i + 1] + 0.114 * img[i + 2];
      camBufB[p] = y;
      lumaMean += y;
      if (camHasPrev) {
        var d = Math.abs(y - camBufA[p]);
        if (d > 10) {
          diffSum += d;
          var px = p % 64;
          var py = (p - px) / 64;
          wx += (63 - px) * d; // mirrored X
          wy += py * d;
          weightSum += d;
        }
      }
      camBufA[p] = y;
    }
    camHasPrev = true;

    var normMotion = Math.min(1.0, diffSum / (64 * 48 * 24));
    state.motionEMA = state.motionEMA * 0.72 + normMotion * 0.28;

    if (weightSum > 450) {
      var cx = (wx / weightSum) / 64;
      var cy = (wy / weightSum) / 48;
      state.motionCenterX = state.motionCenterX * 0.86 + Math.max(0.28, Math.min(0.72, cx)) * 0.14;
      state.motionCenterY = state.motionCenterY * 0.86 + Math.max(0.26, Math.min(0.62, cy)) * 0.14;
    }

    // Presence heuristics: face detected OR sustained subtle movement in a lit frame
    var litFrame = (lumaMean / (64 * 48)) > 14;
    if (state.faceDetected || (litFrame && state.motionEMA > 0.018)) {
      state.personInFrame = true;
    } else if (!state.faceDetected && state.motionEMA < 0.008) {
      state.personInFrame = false;
    }
  }

  function postEmotionSnapshot(force) {
    if (!force && window.KinetiQFace && window.KinetiQFace.ready) return;
    var now = Date.now();
    if (!state.camStream || !state.camVisible || emoBusy || (!force && now - lastEmoTs < 8000)) return;
    if (state.orbMode === 'listening' || state.orbMode === 'thinking') return;
    var v = $('camVideo');
    if (!v || !v.videoWidth) return;
    lastEmoTs = now;
    emoBusy = true;

    try {
      if (!emoCanvas) {
        emoCanvas = document.createElement('canvas');
        emoCanvas.width = 320;
        emoCanvas.height = 240;
        emoCtx = emoCanvas.getContext('2d');
      }
      emoCtx.drawImage(v, 0, 0, 320, 240);
      var dataUrl = emoCanvas.toDataURL('image/jpeg', 0.62);
      fetch('/api/emotion', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image: dataUrl, person: true })
      })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          emoBusy = false;
          if (d && d.vision) {
            state.faceDetected = !!d.vision.face_detected;
            if (state.faceDetected && d.vision.face_box) {
              state.faceBox = {
                cx: 1.0 - d.vision.face_box.cx,
                cy: d.vision.face_box.cy,
                w: d.vision.face_box.w,
                h: d.vision.face_box.h
              };
            } else if (!state.faceDetected) {
              state.faceBox = null;
            }
            if (state.faceDetected) {
              state.personInFrame = true;
              state.emotionLabel = d.vision.label || '';
              state.emotionRaw = d.vision.raw_emotion || '';
            }
          }
        })
        .catch(function () { emoBusy = false; });
    } catch (e) {
      emoBusy = false;
    }
  }

  function cameraHeartbeat() {
    if (!state.camStream || !state.camVisible) return;
    fetch('/api/camera_ingest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ motion: state.motionEMA, activity: 'auto' })
    }).catch(function () {});

    postEmotionSnapshot(false);

    if ((window.KinetiQFace && window.KinetiQFace.faceDetected) || state.faceDetected) {
      state.personInFrame = true;
    }

    // 5-second presence progress & auto-greeting
    var track = $('presenceTrack');
    var bar = $('presBar');

    if (state.personInFrame) {
      state.presenceSeconds = Math.min(300, state.presenceSeconds + 1);
      var actM = getActiveMood() || (state.emotionLabel ? state.emotionLabel : 'Calm');
      if (state.presenceSeconds < 5) {
        if (track) track.classList.add('visible');
        if (bar) bar.style.width = ((state.presenceSeconds / 5) * 100) + '%';
        setPreviewPill('PRESENCE · ' + state.presenceSeconds + '/5S', true, actM);
      } else {
        if (track) track.classList.remove('visible');
        setPreviewPill('PREVIEW ACTIVE · ' + String(actM).toUpperCase(), true, actM);

        if (!userInteracted && state.orbMode === 'idle' && !state.autoGreetDone && Date.now() - state.lastGreetTs > 50000 && state.freshState !== 'OFFLINE') {
          state.autoGreetDone = true;
          state.lastGreetTs = Date.now();
          askAssistant(
            'Hello mirror, I just stepped in front of you. Greet me briefly, cite my live vitals, and give one calm wellness suggestion.',
            true
          );
        }
      }
    } else {
      state.presenceSeconds = Math.max(0, state.presenceSeconds - 2);
      if (bar) bar.style.width = ((state.presenceSeconds / 5) * 100) + '%';
      if (state.presenceSeconds === 0) {
        if (track) track.classList.remove('visible');
        setPreviewPill('PREVIEW ACTIVE', true, null);
        if (state.autoGreetDone && Date.now() - state.lastGreetTs > 15000) {
          state.autoGreetDone = false;
        }
      }
    }
    updateGreeting();
  }

  function syncLiveMoodUI() {
    if (!state.camStream || !state.camVisible) return;
    var mp = window.KinetiQFace;
    var faceHere = (mp && mp.faceDetected) || state.faceDetected;
    if (faceHere) {
      state.personInFrame = true;
      var mood = getActiveMood() || 'Calm';
      if (state.presenceSeconds >= 5) {
        setPreviewPill('PREVIEW ACTIVE · ' + mood.toUpperCase(), true, mood);
      }
      updateGreeting();
    }
  }

  /* -------------------- SUBTLE DOTTED FACIAL CONSTELLATION OVERLAY -------------------- */
  var constellationAlpha = 0;
  var constW = 0;
  var constH = 0;
  var lastConstTs = 0;
  var smoothCx = 0.5;
  var smoothCy = 0.44;
  var smoothScale = 0.23;

  function syncConstellationSize() {
    var stage = $('mirrorStage');
    var canvas = $('constellationCanvas');
    if (!stage || !canvas) return;
    var rect = stage.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) {
      constW = Math.round(rect.width);
      constH = Math.round(rect.height);
      if (canvas.width !== constW || canvas.height !== constH) {
        canvas.width = constW;
        canvas.height = constH;
      }
    }
  }

  function renderConstellation(ts) {
    if (document.hidden) return;
    if (ts - lastConstTs < 32) return;
    lastConstTs = ts;

    var canvas = $('constellationCanvas');
    if (!canvas) return;
    if (!constW || !constH) {
      syncConstellationSize();
      if (!constW || !constH) return;
    }

    var ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, constW, constH);

    var targetAlpha = (state.camVisible && state.personInFrame) ? 0.85 : (state.camVisible ? 0.22 : 0);
    constellationAlpha += (targetAlpha - constellationAlpha) * 0.08;
    if (constellationAlpha < 0.02) return;

    var w = constW;
    var h = constH;
    var mp = window.KinetiQFace;

    // 1) Primary: MediaPipe 478-Point FaceLandmarker Constellation
    if (mp && mp.ready && mp.faceDetected && mp.landmarks && mp.landmarks.length >= 454) {
      state.personInFrame = true;
      var v = $('camVideo');
      var vw = (v && v.videoWidth) || 640;
      var vh = (v && v.videoHeight) || 360;
      var sc = Math.max(w / vw, h / vh);
      var dispW = vw * sc;
      var dispH = vh * sc;
      var offX = (w - dispW) * 0.5;
      var offY = (h - dispH) * 0.5;
      var lm = mp.landmarks;

      // Video is mirrored horizontally via CSS transform: scaleX(-1), so x is flipped: (1.0 - p.x)
      function pt(idx) {
        var p = lm[idx];
        if (!p) return { x: w * 0.5, y: h * 0.5 };
        return { x: offX + (1.0 - p.x) * dispW, y: offY + p.y * dispH };
      }

      var chin = pt(152);
      var glabella = pt(168);
      var jawL = pt(172);
      var jawR = pt(397);
      var faceH = Math.max(20, chin.y - glabella.y);

      // 19 key facial landmark indices
      var keyIndices = [10, 67, 297, 70, 105, 107, 336, 334, 300, 33, 263, 168, 4, 234, 454, 50, 280, 172, 397, 152];
      var facePts = keyIndices.map(function (idx) { return pt(idx); });

      // 4 soft neck/shoulder drop points derived from chin and jaw
      var neckL = { x: jawL.x * 0.75 + chin.x * 0.25, y: chin.y + faceH * 0.30 };
      var neckR = { x: jawR.x * 0.75 + chin.x * 0.25, y: chin.y + faceH * 0.30 };
      var shL   = { x: jawL.x - faceH * 0.45, y: chin.y + faceH * 0.70 };
      var shR   = { x: jawR.x + faceH * 0.45, y: chin.y + faceH * 0.70 };

      var allPts = facePts.concat([neckL, neckR, shL, shR]);

      var mpEdges = [
        // Forehead & temples
        [0, 1], [0, 2], [1, 3], [2, 8], [0, 11],
        // Eyebrows
        [3, 4], [4, 5], [5, 11], [11, 6], [6, 7], [7, 8],
        // Eyes to nose / cheek
        [3, 9], [9, 13], [9, 11], [8, 10], [10, 14], [10, 11],
        // Nose bridge & tip
        [11, 12],
        // Cheeks to jaw
        [13, 17], [14, 18],
        // Mouth
        [12, 15], [12, 16], [15, 16], [15, 19], [16, 19],
        // Jaw to chin
        [17, 19], [18, 19],
        // Neck & shoulders
        [17, 20], [18, 21], [19, 20], [19, 21], [20, 21],
        [20, 22], [21, 23]
      ];

      ctx.save();
      ctx.strokeStyle = 'rgba(130, 199, 197, ' + (0.30 * constellationAlpha).toFixed(3) + ')';
      ctx.lineWidth = 1;
      ctx.setLineDash([2, 4]);
      ctx.beginPath();
      for (var e = 0; e < mpEdges.length; e++) {
        var pA = allPts[mpEdges[e][0]];
        var pB = allPts[mpEdges[e][1]];
        if (pA && pB) {
          ctx.moveTo(pA.x, pA.y);
          ctx.lineTo(pB.x, pB.y);
        }
      }
      ctx.stroke();

      ctx.setLineDash([]);
      for (var pIdx = 0; pIdx < allPts.length; pIdx++) {
        var ptNode = allPts[pIdx];
        if (!ptNode) continue;
        ctx.beginPath();
        ctx.arc(ptNode.x, ptNode.y, 2.1, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(170, 232, 229, ' + (0.80 * constellationAlpha).toFixed(3) + ')';
        ctx.fill();
      }
      ctx.restore();
      return;
    }

    // 2) Fallback: OpenCV Haar face_box / Motion constellation
    var targetCx = state.motionCenterX;
    var targetCy = state.motionCenterY;
    var targetScale = 0.23;
    if (state.faceBox) {
      targetCx = state.faceBox.cx;
      targetCy = state.faceBox.cy;
      targetScale = Math.max(0.18, Math.min(0.40, (state.faceBox.w || 0.22) * 1.3));
    }

    smoothCx += (targetCx - smoothCx) * 0.15;
    smoothCy += (targetCy - smoothCy) * 0.15;
    smoothScale += (targetScale - smoothScale) * 0.15;

    var w = constW;
    var h = constH;
    var cx = w * smoothCx;
    var cy = h * smoothCy;
    var s = Math.max(48, Math.min(w, h) * smoothScale);
    var breath = Math.sin(ts * 0.0022) * 2.5;

    // Delicate facial + shoulder silhouette landmarks matching reference screenshot
    var nodes = [
      { x: cx,           y: cy - s * 0.72 + breath }, // 0 forehead
      { x: cx - s * 0.42, y: cy - s * 0.44 + breath }, // 1 L temple
      { x: cx + s * 0.42, y: cy - s * 0.44 + breath }, // 2 R temple
      { x: cx - s * 0.22, y: cy - s * 0.18 + breath }, // 3 L eye
      { x: cx + s * 0.22, y: cy - s * 0.18 + breath }, // 4 R eye
      { x: cx,           y: cy + s * 0.04 + breath }, // 5 nose bridge
      { x: cx - s * 0.48, y: cy + s * 0.12 + breath }, // 6 L cheek
      { x: cx + s * 0.48, y: cy + s * 0.12 + breath }, // 7 R cheek
      { x: cx - s * 0.30, y: cy + s * 0.46 + breath }, // 8 L jaw
      { x: cx + s * 0.30, y: cy + s * 0.46 + breath }, // 9 R jaw
      { x: cx,           y: cy + s * 0.62 + breath }, // 10 chin
      { x: cx - s * 0.26, y: cy + s * 0.96 },          // 11 L neck/collar
      { x: cx + s * 0.26, y: cy + s * 0.96 },          // 12 R neck/collar
      { x: cx - s * 0.92, y: cy + s * 1.18 },          // 13 L shoulder
      { x: cx + s * 0.92, y: cy + s * 1.18 }           // 14 R shoulder
    ];

    var edges = [
      [0, 1], [0, 2], [1, 6], [2, 7], [6, 8], [7, 9], [8, 10], [9, 10],
      [1, 3], [2, 4], [3, 5], [4, 5], [5, 10],
      [8, 11], [9, 12], [11, 12], [11, 13], [12, 14]
    ];

    ctx.save();
    ctx.strokeStyle = 'rgba(130, 224, 220, ' + (0.28 * constellationAlpha).toFixed(3) + ')';
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 5]);
    ctx.beginPath();
    for (var i = 0; i < edges.length; i++) {
      var a = nodes[edges[i][0]];
      var b = nodes[edges[i][1]];
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
    }
    ctx.stroke();

    ctx.setLineDash([]);
    for (var j = 0; j < nodes.length; j++) {
      var n = nodes[j];
      ctx.beginPath();
      ctx.arc(n.x, n.y, j < 11 ? 2.2 : 2.6, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(165, 242, 238, ' + (0.72 * constellationAlpha).toFixed(3) + ')';
      ctx.fill();
    }
    ctx.restore();
  }

  /* -------------------- AUDIO-REACTIVE VOICE ORB + STT + NEURAL TTS -------------------- */
  var audioCtx = null;
  var micAnalyser = null;
  var micData = null;
  var micStream = null;
  var mediaRecorder = null;
  var audioChunks = [];
  var isRecording = false;
  var speechDetected = false;
  var silenceSince = 0;
  var recordStartTs = 0;
  var currentTtsAudio = null;
  var ttsAnalyser = null;
  var ttsData = null;

  function setOrbState(mode, statusText) {
    state.orbMode = mode;
    var btn = $('voiceOrbBtn');
    if (btn) btn.setAttribute('data-state', mode);
    var pill = $('orbStatusPill');
    var txt = $('orbStatusText');
    if (pill && txt) {
      if (statusText) {
        txt.textContent = statusText;
        pill.hidden = false;
      } else {
        pill.hidden = true;
      }
    }
  }

  function renderVoiceOrb(ts) {
    var canvas = $('voiceOrbCanvas');
    if (!canvas) return;
    var ctx = canvas.getContext('2d');
    var w = canvas.width;
    var h = canvas.height;
    var cx = w / 2;
    var cy = h / 2;
    ctx.clearRect(0, 0, w, h);

    // Measure live audio energy
    var targetEnergy = 0;
    if (state.orbMode === 'listening' && micAnalyser && micData) {
      micAnalyser.getByteFrequencyData(micData);
      var sum = 0;
      for (var i = 0; i < micData.length; i++) sum += micData[i];
      var avg = sum / (micData.length * 255);
      targetEnergy = Math.min(1.0, avg * 3.4);

      // Auto-silence stop once user finishes speaking
      var now = Date.now();
      if (targetEnergy > 0.14) {
        speechDetected = true;
        silenceSince = 0;
      } else if (speechDetected && now - recordStartTs > 1200) {
        if (!silenceSince) silenceSince = now;
        else if (now - silenceSince > 1400) stopVoiceRecording();
      }
      if (now - recordStartTs > 9000) stopVoiceRecording();
    } else if (state.orbMode === 'speaking') {
      if (ttsAnalyser && ttsData) {
        ttsAnalyser.getByteFrequencyData(ttsData);
        var s2 = 0;
        for (var j = 0; j < ttsData.length; j++) s2 += ttsData[j];
        targetEnergy = Math.min(1.0, (s2 / (ttsData.length * 255)) * 2.8);
      } else {
        targetEnergy = 0.28 + 0.24 * Math.abs(Math.sin(ts * 0.009)) * Math.abs(Math.cos(ts * 0.005));
      }
    } else if (state.orbMode === 'thinking') {
      targetEnergy = 0.22;
    } else {
      targetEnergy = 0.06 + 0.04 * Math.sin(ts * 0.003);
    }

    state.orbEnergy += (targetEnergy - state.orbEnergy) * 0.22;
    var e = state.orbEnergy;

    // Color palette per state
    var rgb = state.orbMode === 'speaking'
      ? '77, 212, 172'
      : state.orbMode === 'thinking'
        ? '94, 194, 230'
        : '115, 220, 213';

    // Inner luminous core
    var coreGrad = ctx.createRadialGradient(cx, cy, 4, cx, cy, 38 + e * 18);
    coreGrad.addColorStop(0, 'rgba(' + rgb + ', ' + (0.28 + e * 0.45).toFixed(2) + ')');
    coreGrad.addColorStop(1, 'rgba(' + rgb + ', 0)');
    ctx.fillStyle = coreGrad;
    ctx.beginPath();
    ctx.arc(cx, cy, 40 + e * 16, 0, Math.PI * 2);
    ctx.fill();

    // Organic fluid reactive rings (morphs smoothly with voice amplitude!)
    var ringCount = state.orbMode === 'idle' ? 1 : 3;
    for (var r = 0; r < ringCount; r++) {
      var baseR = 26 + r * 7 + e * (10 + r * 5);
      ctx.beginPath();
      var steps = 48;
      for (var k = 0; k <= steps; k++) {
        var ang = (k / steps) * Math.PI * 2;
        var waveAmp = (state.orbMode === 'idle' ? 1.2 : (2.5 + e * 11.5)) * (1 - r * 0.18);
        var wobble =
          Math.sin(ang * 3 + ts * 0.006 * (r % 2 === 0 ? 1 : -1) + r) * waveAmp +
          Math.cos(ang * 5 - ts * 0.004) * (waveAmp * 0.45);
        var rad = baseR + wobble;
        var x = cx + Math.cos(ang) * rad;
        var y = cy + Math.sin(ang) * rad;
        if (k === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.closePath();
      ctx.strokeStyle = 'rgba(' + rgb + ', ' + Math.max(0.18, (0.72 - r * 0.2)).toFixed(2) + ')';
      ctx.lineWidth = r === 0 ? 2.0 : 1.3;
      ctx.stroke();
    }

    // Thinking orbital sweep arc
    if (state.orbMode === 'thinking') {
      var startAng = ts * 0.0055;
      ctx.beginPath();
      ctx.arc(cx, cy, 38, startAng, startAng + Math.PI * 0.75);
      ctx.strokeStyle = 'rgba(150, 232, 255, 0.9)';
      ctx.lineWidth = 2.4;
      ctx.stroke();
    }
  }

  function stopSpeech() {
    speechSeq++;
    if (currentTtsAudio) {
      try {
        currentTtsAudio.onended = null;
        currentTtsAudio.onerror = null;
        currentTtsAudio.pause();
        currentTtsAudio.src = '';
      } catch (e) {}
      currentTtsAudio = null;
      ttsAnalyser = null;
      ttsData = null;
    }
    if ('speechSynthesis' in window) {
      try { window.speechSynthesis.cancel(); } catch (e) {}
    }
    if (state.orbMode === 'speaking') setOrbState('idle', '');
  }

  function speakFallbackBrowser(clean, mySeq) {
    if (!('speechSynthesis' in window)) {
      if (mySeq === speechSeq) setOrbState('idle', '');
      return;
    }
    var u = new SpeechSynthesisUtterance(clean);
    u.rate = 0.98;
    u.onstart = function () {
      if (mySeq !== speechSeq) return;
      setOrbState('speaking', 'Speaking...');
    };
    u.onend = function () {
      if (mySeq !== speechSeq) return;
      setOrbState('idle', '');
    };
    u.onerror = function () {
      if (mySeq !== speechSeq) return;
      setOrbState('idle', '');
    };
    window.speechSynthesis.speak(u);
  }

  function speakText(text) {
    stopSpeech();
    var mySeq = speechSeq;
    var fallbackFired = false;
    if (!state.ttsOn || !text) return;
    var clean = String(text).replace(/[*_#`~]/g, '').trim();
    if (!clean) return;

    function triggerFallback() {
      if (fallbackFired || mySeq !== speechSeq) return;
      fallbackFired = true;
      currentTtsAudio = null;
      ttsAnalyser = null;
      speakFallbackBrowser(clean, mySeq);
    }

    setOrbState('speaking', 'Speaking...');
    try {
      var audio = new Audio('/api/tts?text=' + encodeURIComponent(clean));
      currentTtsAudio = audio;

      // Wire TTS output into Web Audio analyser so Voice Orb ripples to the mirror's speech!
      try {
        var AC = window.AudioContext || window.webkitAudioContext;
        if (AC) {
          if (!audioCtx) audioCtx = new AC();
          if (audioCtx.state === 'suspended') audioCtx.resume();
          var src = audioCtx.createMediaElementSource(audio);
          var an = audioCtx.createAnalyser();
          an.fftSize = 64;
          src.connect(an);
          an.connect(audioCtx.destination);
          ttsAnalyser = an;
          ttsData = new Uint8Array(an.frequencyBinCount);
        }
      } catch (e) {
        ttsAnalyser = null;
      }

      audio.onended = function () {
        if (mySeq !== speechSeq) return;
        currentTtsAudio = null;
        ttsAnalyser = null;
        setOrbState('idle', '');
      };
      audio.onerror = triggerFallback;
      audio.play().catch(triggerFallback);
    } catch (e) {
      triggerFallback();
    }
  }

  function startVoiceRecording() {
    if (isRecording) {
      stopVoiceRecording();
      return;
    }
    stopSpeech();
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setOrbState('idle', 'Mic unavailable');
      setTimeout(function () { setOrbState('idle', ''); }, 2200);
      return;
    }

    navigator.mediaDevices.getUserMedia({ audio: true })
      .then(function (stream) {
        micStream = stream;
        isRecording = true;
        speechDetected = false;
        silenceSince = 0;
        recordStartTs = Date.now();
        audioChunks = [];

        // Wire live microphone into Web Audio Analyser for Voice Orb reactivity
        try {
          var AC = window.AudioContext || window.webkitAudioContext;
          if (AC) {
            if (!audioCtx) audioCtx = new AC();
            if (audioCtx.state === 'suspended') audioCtx.resume();
            var src = audioCtx.createMediaStreamSource(stream);
            micAnalyser = audioCtx.createAnalyser();
            micAnalyser.fftSize = 128;
            src.connect(micAnalyser);
            micData = new Uint8Array(micAnalyser.frequencyBinCount);
          }
        } catch (e) {
          micAnalyser = null;
        }

        mediaRecorder = new MediaRecorder(stream);
        mediaRecorder.ondataavailable = function (ev) {
          if (ev.data && ev.data.size > 0) audioChunks.push(ev.data);
        };
        mediaRecorder.onstop = function () {
          cleanupMic();
          var blob = new Blob(audioChunks, { type: 'audio/webm' });
          if (blob.size < 400) {
            setOrbState('idle', '');
            return;
          }
          setOrbState('thinking', 'Whisper STT...');
          fetch('/api/transcribe', {
            method: 'POST',
            headers: { 'Content-Type': 'application/octet-stream' },
            body: blob
          })
            .then(function (r) { return r.json(); })
            .then(function (d) {
              if (d && d.text && d.text.trim()) {
                askAssistant(d.text.trim(), false);
              } else {
                setOrbState('idle', 'Did not catch that');
                setTimeout(function () { if (state.orbMode === 'idle') setOrbState('idle', ''); }, 2200);
              }
            })
            .catch(function () {
              setOrbState('idle', 'STT error');
              setTimeout(function () { if (state.orbMode === 'idle') setOrbState('idle', ''); }, 2200);
            });
        };

        mediaRecorder.start();
        setOrbState('listening', 'Listening... (tap or pause)');
      })
      .catch(function () {
        setOrbState('idle', 'Mic permission denied');
        setTimeout(function () { setOrbState('idle', ''); }, 2500);
      });
  }

  function cleanupMic() {
    isRecording = false;
    micAnalyser = null;
    micData = null;
    if (micStream) {
      micStream.getTracks().forEach(function (t) { t.stop(); });
      micStream = null;
    }
  }

  function stopVoiceRecording() {
    if (!isRecording) return;
    isRecording = false;
    try {
      if (mediaRecorder && mediaRecorder.state !== 'inactive') {
        mediaRecorder.stop();
      } else {
        cleanupMic();
      }
    } catch (e) {
      cleanupMic();
    }
  }

  /* -------------------- SENSOR-GROUNDED AI ASSISTANT & RECEIPT -------------------- */
  function askAssistant(question, isAuto, selfTestInterlock) {
    if (!question && !selfTestInterlock) return;
    if (!isAuto) {
      userInteracted = true;
    }
    var myAskSeq = ++askRequestSeq;
    stopSpeech();
    postEmotionSnapshot(true);

    setOrbState('thinking', selfTestInterlock ? 'Testing safety interlock...' : 'Grounding on live vitals...');
    $('aiTime').textContent = fmtTimeShort();
    $('aiBadge').className = 'g-cat';
    $('aiBadge').textContent = 'VERIFYING...';
    $('aiHeadline').textContent = selfTestInterlock
      ? 'Running fail-closed interlock test'
      : (isAuto ? 'Presence check-in' : question);
    $('aiBody').textContent = 'Thinking with ' + state.aiModel + ' using your live context...';

    var mp = window.KinetiQFace;
    var payload = {
      q: question || 'How am I doing?',
      tts: false,
      vision: {
        mood: getActiveMood(),
        posture: (mp && mp.faceDetected && mp.posture) ? mp.posture : 'upright'
      }
    };
    if (selfTestInterlock) payload.self_test_interlock = true;

    fetch('/api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (myAskSeq !== askRequestSeq) return;
        var g = d.grounding || 'FAIL';
        var meta = d.meta || {};
        var ans = d.answer || d.error || 'Assistant unavailable.';
        state.lastAnswerText = ans;
        state.lastReceipt = {
          question: selfTestInterlock ? '[Interlock Self-Test]' : question,
          answer: ans,
          grounding: g,
          meta: meta,
          time: fmtTimeShort()
        };

        $('aiTime').textContent = state.lastReceipt.time;
        var badge = $('aiBadge');
        if (meta.self_healed) {
          badge.className = 'g-cat healed';
          badge.textContent = 'INTERCEPTED · SAFE FALLBACK';
          $('aiHeadline').textContent = 'Ungrounded drift blocked — safe reply served';
        } else if (g === 'PASS') {
          badge.className = 'g-cat pass';
          badge.textContent = meta.offline ? 'OFFLINE GUARD · PASS' : 'VERIFIED · PASS';
          $('aiHeadline').textContent = isAuto ? 'Live presence check-in' : (question.length > 46 ? question.slice(0, 45) + '…' : question);
        } else {
          badge.className = 'g-cat fail';
          badge.textContent = 'BLOCKED · FAIL';
          $('aiHeadline').textContent = 'Grounding check failed (speech blocked)';
        }

        $('aiBody').textContent = ans;
        $('btnReceipt').disabled = false;
        $('btnReplay').hidden = (g !== 'PASS');

        var ey = $('aiEyebrow');
        if (ey) {
          var lat = meta.latency_ms != null ? ' · ' + meta.latency_ms + ' ms' : '';
          ey.textContent = 'AI ACTIVITY / ' + String(meta.model || meta.backend || state.aiModel).toUpperCase() + lat;
        }

        // Strict fail-closed speech gate on frontend as well!
        if (g === 'PASS' && state.ttsOn) {
          speakText(ans);
        } else {
          setOrbState('idle', g === 'FAIL' ? 'Speech blocked (FAIL)' : '');
          if (g === 'FAIL') setTimeout(function () { setOrbState('idle', ''); }, 2600);
        }
      })
      .catch(function () {
        if (myAskSeq !== askRequestSeq) return;
        $('aiBadge').className = 'g-cat fail';
        $('aiBadge').textContent = 'ERROR';
        $('aiBody').textContent = 'Could not reach local assistant endpoint.';
        setOrbState('idle', '');
      });
  }

  function openReceiptModal() {
    var rec = state.lastReceipt;
    if (!rec) return;
    var m = rec.meta || {};
    var snap = m.snapshot || {};
    var body = $('receiptBody');

    var html = '';
    html += '<div style="margin-bottom:14px;padding:12px 14px;border-radius:10px;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);">';
    html += '<div style="font-size:11px;font-weight:700;letter-spacing:0.06em;color:var(--text-dim,#9bb3b0);margin-bottom:6px;text-transform:uppercase;">Served Reply (' + esc(m.model || m.backend || state.aiModel) + ')</div>';
    html += '<div style="font-size:14px;line-height:1.55;color:var(--text,#e8fbfa);">' + esc(rec.answer) + '</div>';
    html += '</div>';

    html += '<p><strong>User Query:</strong> ' + esc(rec.question) + '</p>';
    html += '<p><strong>Verdict:</strong> <code>' + esc(rec.grounding) + '</code> &middot; <strong>Backend:</strong> ' +
      esc(m.backend || m.model || state.aiModel) + ' &middot; <strong>Latency:</strong> ' + esc(m.latency_ms != null ? m.latency_ms + ' ms' : '--') + '</p>';

    if (m.self_healed) {
      html += '<div class="receipt-alert"><strong>Fail-Closed Interlock Triggered:</strong> An ungrounded LLM response was intercepted before reaching the user and replaced with a verified snapshot response.';
      if (m.intercepted_text) {
        html += '<code>Blocked text: "' + esc(m.intercepted_text) + '"</code>';
      }
      if (m.intercepted_reasons && m.intercepted_reasons.length) {
        html += '<code>Reasons:\n- ' + m.intercepted_reasons.map(esc).join('\n- ') + '</code>';
      }
      html += '</div>';
    }

    html += '<div class="receipt-grid">';
    html += '<div class="receipt-kv"><span>Heart Rate</span><strong>' + esc(snap.heart_rate != null ? snap.heart_rate + ' bpm' : '--') + '</strong></div>';
    html += '<div class="receipt-kv"><span>SpO2</span><strong>' + esc(snap.spo2 != null ? snap.spo2 + '%' : '--') + '</strong></div>';
    html += '<div class="receipt-kv"><span>Steps</span><strong>' + esc(snap.steps != null ? Number(snap.steps).toLocaleString() : '--') + '</strong></div>';
    html += '<div class="receipt-kv"><span>Stress</span><strong>' + esc(snap.stress_score != null ? snap.stress_score + '/100' : 'N/A') + '</strong></div>';
    html += '<div class="receipt-kv"><span>Source</span><strong>' + esc(snap.source || '--') + '</strong></div>';
    html += '<div class="receipt-kv"><span>Freshness</span><strong>' + esc((snap.freshness || '--') + (snap.age_s != null ? ' (' + snap.age_s + 's)' : '')) + '</strong></div>';
    html += '</div>';

    body.innerHTML = html;
    $('receiptModal').hidden = false;
  }

  /* -------------------- 60-SECOND CALM BREATHING OVERLAY -------------------- */
  function startBreathing() {
    stopBreathing();
    state.breathRemaining = 60;
    $('breathOverlay').hidden = false;
    var step = 0; // 0..11 => 4s Inhale, 4s Hold, 4s Exhale

    function updateBreath() {
      if (state.breathRemaining <= 0) {
        stopBreathing();
        return;
      }
      var phaseIdx = Math.floor((step % 12) / 4);
      var secInPhase = 4 - (step % 4);
      var ring = $('breathRing');
      var phaseEl = $('breathPhase');
      var countEl = $('breathCount');

      ring.classList.remove('inhale', 'hold', 'exhale');
      if (phaseIdx === 0) {
        ring.classList.add('inhale');
        phaseEl.textContent = 'Inhale';
      } else if (phaseIdx === 1) {
        ring.classList.add('hold');
        phaseEl.textContent = 'Hold';
      } else {
        ring.classList.add('exhale');
        phaseEl.textContent = 'Exhale';
      }
      countEl.textContent = String(secInPhase);

      var mm = String(Math.floor(state.breathRemaining / 60)).padStart(2, '0');
      var ss = String(state.breathRemaining % 60).padStart(2, '0');
      $('breathTimer').textContent = mm + ':' + ss + ' remaining';

      step++;
      state.breathRemaining--;
    }

    updateBreath();
    state.breathTimerId = setInterval(updateBreath, 1000);
  }

  function stopBreathing() {
    if (state.breathTimerId) {
      clearInterval(state.breathTimerId);
      state.breathTimerId = null;
    }
    $('breathOverlay').hidden = true;
    $('breathRing').classList.remove('inhale', 'hold', 'exhale');
  }

  /* -------------------- ANIMATION LOOP -------------------- */
  function rafLoop(ts) {
    if (!document.hidden) {
      renderVoiceOrb(ts);
      renderConstellation(ts);
    }
    requestAnimationFrame(rafLoop);
  }

  /* -------------------- EVENT BINDING & BOOT -------------------- */
  function bindEvents() {
    $('btnModeBand').addEventListener('click', function () { setMode('band'); });
    $('btnModeRtos').addEventListener('click', function () { setMode('rtos'); });
    $('camActivateBtn').addEventListener('click', startCamera);
    $('camToggleBtn').addEventListener('click', toggleCameraPreview);

    $('spkBtn').addEventListener('click', function () {
      state.ttsOn = !state.ttsOn;
      $('spkLabel').textContent = state.ttsOn ? 'Voice On' : 'Voice Muted';
      $('spkBtn').classList.toggle('muted', !state.ttsOn);
      $('spkBtn').setAttribute('aria-pressed', state.ttsOn ? 'true' : 'false');
      if (!state.ttsOn) stopSpeech();
    });

    $('voiceOrbBtn').addEventListener('click', startVoiceRecording);

    // Spacebar shortcut for hands-free voice orb when not typing
    document.addEventListener('keydown', function (ev) {
      if (ev.code === 'Space' && !ev.repeat) {
        var tag = (document.activeElement && document.activeElement.tagName || '').toLowerCase();
        if (tag !== 'input' && tag !== 'textarea' && tag !== 'button') {
          ev.preventDefault();
          startVoiceRecording();
        }
      } else if (ev.key === 'Escape') {
        $('receiptModal').hidden = true;
        stopBreathing();
      }
    });

    $('askForm').addEventListener('submit', function (ev) {
      ev.preventDefault();
      var input = $('askInput');
      var q = input.value.trim();
      if (!q) return;
      input.value = '';
      askAssistant(q, false);
    });

    document.querySelectorAll('.prompt-link, .q-chip').forEach(function (btn) {
      btn.addEventListener('click', function () {
        askAssistant(btn.getAttribute('data-q'), false);
      });
    });

    $('btnReceipt').addEventListener('click', openReceiptModal);
    $('receiptCloseBtn').addEventListener('click', function () { $('receiptModal').hidden = true; });
    $('receiptModal').addEventListener('click', function (ev) {
      if (ev.target === $('receiptModal')) $('receiptModal').hidden = true;
    });

    var rstBtn = $('btnResetChat');
    if (rstBtn) {
      rstBtn.addEventListener('click', function () {
        fetch('/api/chat/reset', { method: 'POST' }).catch(function () {});
        $('aiHeadline').textContent = 'Ready for a fresh check-in';
        $('aiBody').textContent = 'Conversation memory cleared. Ask any new question or tap the voice orb.';
        $('aiBadge').className = 'g-cat';
        $('aiBadge').textContent = 'READY';
        $('btnReceipt').disabled = true;
        $('btnReplay').hidden = true;
        state.lastReceipt = null;
        state.lastAnswerText = '';
      });
    }

    $('btnReplay').addEventListener('click', function () {
      if (state.lastAnswerText) speakText(state.lastAnswerText);
    });

    $('btnStartBreathe').addEventListener('click', startBreathing);
    $('btnWellAction').addEventListener('click', startBreathing);
    $('breathCloseBtn').addEventListener('click', stopBreathing);

    $('btnWellAck').addEventListener('click', function () {
      state.wellAcked = !state.wellAcked;
      $('cardWellbeing').classList.toggle('acked', state.wellAcked);
      $('btnWellAck').textContent = state.wellAcked ? 'Restore' : 'Acknowledge';
    });

    $('btnSnapAck').addEventListener('click', function () {
      state.snapAcked = !state.snapAcked;
      $('cardSnapshot').classList.toggle('acked', state.snapAcked);
      $('btnSnapAck').textContent = state.snapAcked ? 'Restore' : 'Acknowledge';
    });

    $('btnSnapSwitch').addEventListener('click', function () {
      setMode(state.mode === 'band' ? 'rtos' : 'band');
    });

    document.querySelectorAll('#rtosCmdRow .cmd-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        sendRtosCommand(btn.getAttribute('data-cmd'));
      });
    });

    $('btnFastFresh').addEventListener('click', toggleFastFreshness);
    $('btnInterlockTest').addEventListener('click', function () {
      askAssistant('', false, true);
    });
  }

  function boot() {
    initTabs();
    bindEvents();
    updateClock();
    updateGreeting();
    setInterval(updateClock, 1000);
    setInterval(tick, 1000);
    setInterval(sampleCameraMotion, 350);
    setInterval(cameraHeartbeat, 1000);
    setInterval(syncLiveMoodUI, 300);

    syncConstellationSize();
    if (typeof ResizeObserver !== 'undefined') {
      var stage = $('mirrorStage');
      if (stage) {
        var ro = new ResizeObserver(function () { syncConstellationSize(); });
        ro.observe(stage);
      }
    } else {
      window.addEventListener('resize', syncConstellationSize);
    }

    tick();
    startCamera();
    requestAnimationFrame(rafLoop);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
