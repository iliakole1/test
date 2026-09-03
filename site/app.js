/* AI Water Meter -- app logic.
 *
 * All state lives on the device: chrome.storage.sync inside the extension (so it
 * follows the browser profile), localStorage on the web. Nothing is ever sent
 * anywhere, which is also why importing has to happen in the browser rather than
 * on a server.
 */
(function () {
  "use strict";

  var W = window.WaterModel;
  var STORE_KEY = "ai-water-meter";
  var CONSTANTS = null;
  var state = null;

  /* ---------- storage ---------- */

  var inExtension =
    typeof chrome !== "undefined" && chrome.storage && chrome.storage.sync;

  function load() {
    return new Promise(function (resolve) {
      if (inExtension) {
        chrome.storage.sync.get(STORE_KEY, function (got) {
          resolve((got && got[STORE_KEY]) || null);
        });
        return;
      }
      try {
        resolve(JSON.parse(localStorage.getItem(STORE_KEY)));
      } catch (e) {
        resolve(null);
      }
    });
  }

  function save() {
    if (inExtension) {
      var payload = {};
      payload[STORE_KEY] = state;
      chrome.storage.sync.set(payload);
      return;
    }
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify(state));
    } catch (e) {
      /* private mode, or the quota is full -- the meter still works this session */
    }
  }

  function blankState() {
    return { v: 1, imports: {}, entries: [], presets: {}, overrides: {}, demo: false };
  }

  /* A first visit with an empty meter shows nothing about what the tool does, so
   * seed a few plausible days, clearly flagged and one click from gone. */
  function demoState() {
    var s = blankState();
    s.demo = true;
    var days = {};
    var entries = [];
    var today = new Date();
    for (var d = 13; d >= 0; d--) {
      var day = new Date(today.getTime() - d * 86400000).toISOString().slice(0, 10);
      var weekday = new Date(day).getUTCDay();
      if (weekday === 0 || weekday === 6) continue;
      var busy = 0.4 + ((d * 37) % 100) / 100;
      days[day] = {
        input: Math.round(40 * busy),
        output: Math.round(24000 * busy),
        cache_write: Math.round(140000 * busy),
        cache_read: Math.round(900000 * busy),
      };
      if (d % 4 === 0) {
        entries.push({ id: "demo-i" + d, service: "weavy", label: "Image generation", units: 9, count: 6, date: day });
      }
      if (d % 7 === 0) {
        entries.push({ id: "demo-v" + d, service: "weavy", label: "Video generation", units: 164, count: 1, date: day });
      }
      if (d % 3 === 0) {
        entries.push({ id: "demo-g" + d, service: "gemini-pro", label: "Request", units: 1, count: 25, date: day });
      }
    }
    var tokens = W.emptyTokens();
    var calls = 0;
    Object.keys(days).forEach(function (k) {
      tokens = W.addTokens(tokens, days[k]);
      calls += 40;
    });
    s.imports["claude-code"] = { calls: calls, tokens: tokens, days: days, importedAt: null };
    s.entries = entries;
    return s;
  }

  /* ---------- model access, with user overrides applied ---------- */

  function rate(path, fallback) {
    var v = state.overrides[path];
    return typeof v === "number" && isFinite(v) && v >= 0 ? v : fallback;
  }

  function mlPerWh() {
    return rate("ml_per_wh", CONSTANTS.water.ml_per_wh);
  }

  function tokenRates() {
    var base = CONSTANTS.claude_tokens_wh;
    return {
      output: rate("wh.output", base.output),
      input: rate("wh.input", base.input),
      cache_write: rate("wh.cache_write", base.cache_write),
      cache_read: rate("wh.cache_read", base.cache_read),
    };
  }

  function serviceWhPerUnit(id) {
    return rate("svc." + id, CONSTANTS.services[id].wh_per_unit);
  }

  function presetsFor(id) {
    var base = CONSTANTS.services[id].operations || [];
    return base.concat(state.presets[id] || []);
  }

  /* ---------- aggregation ---------- */

  /* Everything -- tokens, credits, requests -- reduces to watt-hours per day per
   * service, which is the only shape the rest of the page needs. */
  function aggregate() {
    var rates = tokenRates();
    var byService = {};
    var byDay = {};
    var calls = 0;

    function bump(service, day, wh) {
      if (!wh) return;
      byService[service] = (byService[service] || 0) + wh;
      if (!byDay[day]) byDay[day] = {};
      byDay[day][service] = (byDay[day][service] || 0) + wh;
    }

    Object.keys(state.imports).forEach(function (id) {
      var imported = state.imports[id];
      calls += imported.calls || 0;
      Object.keys(imported.days || {}).forEach(function (day) {
        bump(id, day, W.tokenEnergyWh(imported.days[day], rates));
      });
    });

    state.entries.forEach(function (e) {
      bump(e.service, e.date, e.count * e.units * serviceWhPerUnit(e.service));
    });

    var days = Object.keys(byDay).sort();
    var totalWh = 0;
    Object.keys(byService).forEach(function (k) {
      totalWh += byService[k];
    });

    return {
      totalWh: totalWh,
      totalMl: totalWh * mlPerWh(),
      byService: byService,
      byDay: byDay,
      days: days,
      calls: calls,
      first: days[0] || null,
      last: days[days.length - 1] || null,
    };
  }

  /* ---------- small DOM helpers ---------- */

  function el(id) {
    return document.getElementById(id);
  }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function serviceLabel(id) {
    return (CONSTANTS.services[id] && CONSTANTS.services[id].label) || id;
  }

  function serviceAccent(id) {
    return (CONSTANTS.services[id] && CONSTANTS.services[id].accent) || "var(--water)";
  }

  /* ---------- meter face ---------- */

  function renderRegister(ml) {
    var value = ml, unit = "mL";
    if (ml >= 1e7) { value = ml / 1e6; unit = "m³"; }
    else if (ml >= 1e4) { value = ml / 1000; unit = "LITRES"; }

    var whole = Math.floor(value);
    var frac = Math.round((value - whole) * 100);
    if (frac === 100) { whole += 1; frac = 0; }
    var text = String(whole).padStart(5, "0").slice(-5);

    var cells = text.split("").map(function (d) {
      return '<span class="digit">' + d + "</span>";
    }).join("");
    cells += '<span class="point">.</span>';
    cells += String(frac).padStart(2, "0").split("").map(function (d) {
      return '<span class="digit frac">' + d + "</span>";
    }).join("");

    el("register").innerHTML =
      '<div class="register" role="img" aria-label="Meter reading ' +
      value.toFixed(2) + " " + unit + '">' + cells +
      '<span class="unit">' + unit + "</span></div>";
  }

  function renderDial(ml) {
    var ref = W.fillReference(ml, CONSTANTS.comparisons);
    var fraction = ref.ml ? Math.min(ml / ref.ml, 1) : 0;
    var r = 52, cx = 66, cy = 66, circumference = 2 * Math.PI * r;
    var angle = fraction * 2 * Math.PI - Math.PI / 2;
    var nx = cx + Math.cos(angle) * (r - 11);
    var ny = cy + Math.sin(angle) * (r - 11);

    var ticks = "";
    for (var i = 0; i < 12; i++) {
      var a = (i * Math.PI) / 6 - Math.PI / 2;
      var inner = r - (i % 3 === 0 ? 9 : 5);
      ticks +=
        '<line x1="' + (cx + Math.cos(a) * inner).toFixed(1) + '" y1="' + (cy + Math.sin(a) * inner).toFixed(1) +
        '" x2="' + (cx + Math.cos(a) * r).toFixed(1) + '" y2="' + (cy + Math.sin(a) * r).toFixed(1) +
        '" stroke="var(--rule)" stroke-width="' + (i % 3 === 0 ? 2 : 1) + '" />';
    }

    el("dial").innerHTML =
      '<svg viewBox="0 0 132 132" width="132" height="132" role="img" aria-label="' +
      Math.round(fraction * 100) + " percent of " + esc(ref.label) + '">' +
      '<circle cx="66" cy="66" r="52" fill="var(--surface)" stroke="var(--rule)" stroke-width="1" />' + ticks +
      '<circle cx="66" cy="66" r="52" fill="none" stroke="var(--water)" stroke-width="5" stroke-linecap="round"' +
      ' stroke-dasharray="' + (fraction * circumference).toFixed(1) + " " + circumference.toFixed(1) +
      '" transform="rotate(-90 66 66)" />' +
      '<line x1="66" y1="66" x2="' + nx.toFixed(1) + '" y2="' + ny.toFixed(1) +
      '" stroke="var(--signal)" stroke-width="2" stroke-linecap="round" />' +
      '<circle cx="66" cy="66" r="3.5" fill="var(--signal)" />' +
      '<text x="66" y="96" text-anchor="middle" fill="var(--ink-soft)" font-family="IBM Plex Mono, monospace"' +
      ' font-size="12">' + (fraction * 100).toFixed(1) + "%</text></svg>";

    return ref;
  }

  function renderRail(ml) {
    var marks = CONSTANTS.rail_marks;
    var lo = 0, hi = Math.log10(2e6);
    var w = 900, h = 78, padL = 14, padR = 14;
    var span = w - padL - padR;
    var axisY = 30;

    function xOf(v) {
      return padL + ((Math.log10(Math.max(v, 1)) - lo) / (hi - lo)) * span;
    }

    var parts = ['<line x1="' + padL + '" y1="' + axisY + '" x2="' + (w - padR) + '" y2="' + axisY +
      '" stroke="var(--rule)" stroke-width="1.5" />'];

    marks.forEach(function (m) {
      var x = xOf(m.ml).toFixed(1);
      parts.push('<line x1="' + x + '" y1="' + (axisY - 5) + '" x2="' + x + '" y2="' + (axisY + 5) +
        '" stroke="var(--rule)" stroke-width="1.5" />');
      parts.push('<text x="' + x + '" y="' + (axisY + 20) + '" text-anchor="middle" fill="var(--ink-faint)"' +
        ' font-family="IBM Plex Sans Condensed, sans-serif" font-size="10.5" letter-spacing=".05em">' +
        esc(m.label) + "</text>");
      parts.push('<text x="' + x + '" y="' + (axisY + 33) + '" text-anchor="middle" fill="var(--ink-faint)"' +
        ' font-family="IBM Plex Mono, monospace" font-size="9.5">' + W.formatVolume(m.ml) + "</text>");
    });

    var mx = xOf(Math.max(ml, 1));
    parts.push('<line x1="' + padL + '" y1="' + axisY + '" x2="' + mx.toFixed(1) + '" y2="' + axisY +
      '" stroke="var(--water)" stroke-width="4" stroke-linecap="round" />');
    parts.push('<circle cx="' + mx.toFixed(1) + '" cy="' + axisY +
      '" r="6" fill="var(--water)" stroke="var(--surface)" stroke-width="2" />');
    var anchor = mx < w * 0.75 ? "start" : "end";
    parts.push('<text x="' + (anchor === "start" ? mx + 11 : mx - 11).toFixed(1) + '" y="' + (axisY - 12) +
      '" text-anchor="' + anchor + '" fill="var(--water)" font-family="IBM Plex Mono, monospace"' +
      ' font-size="13" font-weight="600">you: ' + W.formatVolume(ml) + "</text>");

    el("rail").innerHTML =
      '<svg viewBox="0 0 ' + w + " " + h + '" width="100%" height="' + h +
      '" preserveAspectRatio="xMinYMid meet" role="img" aria-label="Your total, ' + W.formatVolume(ml) +
      ', against everyday water volumes on a logarithmic scale">' + parts.join("") + "</svg>";
  }

  /* ---------- panels ---------- */

  function renderSources(agg) {
    var ids = Object.keys(agg.byService).filter(function (id) {
      return agg.byService[id] > 0;
    }).sort(function (a, b) {
      return agg.byService[b] - agg.byService[a];
    });

    if (!ids.length) {
      el("sources").innerHTML =
        '<p class="empty">Nothing logged yet. Import your Claude usage below, or add a Weavy or Gemini entry above.</p>';
      return;
    }

    var bar = ids.map(function (id) {
      var pct = (agg.byService[id] / agg.totalWh) * 100;
      return '<span style="width:' + pct.toFixed(2) + "%;background:" + serviceAccent(id) +
        '" title="' + esc(serviceLabel(id)) + '"></span>';
    }).join("");

    var key = ids.map(function (id) {
      var wh = agg.byService[id];
      return '<div><i style="background:' + serviceAccent(id) + '"></i><span>' + esc(serviceLabel(id)) +
        '</span><span class="v">' + ((wh / agg.totalWh) * 100).toFixed(1) + "% &middot; " +
        W.formatVolume(wh * mlPerWh()) + "</span></div>";
    }).join("");

    el("sources").innerHTML = '<div class="comp">' + bar + '</div><div class="comp-key">' + key + "</div>";
  }

  function renderReading(agg) {
    var days = agg.days.length;
    var perDay = days ? agg.totalMl / days : 0;
    var rows = [
      ["Active days", W.formatInt(days)],
      ["Claude API calls", agg.calls ? W.formatInt(agg.calls) : "&mdash;"],
      ["Logged operations", W.formatInt(state.entries.reduce(function (n, e) { return n + e.count; }, 0))],
      ["Estimated energy", agg.totalWh.toFixed(1) + " Wh"],
      ["Per active day", W.formatVolume(perDay)],
      ["At this rate, one year", W.formatVolume(perDay * 365)],
    ];
    el("reading-rows").innerHTML =
      rows.map(function (r) {
        return '<div class="row"><dt>' + r[0] + "</dt><dd>" + r[1] + "</dd></div>";
      }).join("") +
      '<div class="row total"><dt>Total water</dt><dd>' + W.formatVolume(agg.totalMl) + "</dd></div>";
  }

  function renderDaily(agg) {
    var section = el("daily-section");
    if (agg.days.length < 2) {
      section.hidden = true;
      return;
    }
    section.hidden = false;

    var days = agg.days.slice(-90);
    var ids = Object.keys(agg.byService).sort(function (a, b) {
      return agg.byService[b] - agg.byService[a];
    });
    var totals = days.map(function (d) {
      var sum = 0;
      ids.forEach(function (id) { sum += agg.byDay[d][id] || 0; });
      return sum * mlPerWh();
    });
    var peak = Math.max.apply(null, totals);

    var w = 900, h = 210, padL = 62, padR = 12, padT = 16, padB = 34;
    var plotW = w - padL - padR, plotH = h - padT - padB;
    var slot = plotW / days.length;
    var barW = Math.min(slot * 0.72, 46);
    var parts = [];

    [0, 0.5, 1].forEach(function (f) {
      var y = padT + plotH * (1 - f);
      parts.push('<line x1="' + padL + '" y1="' + y.toFixed(1) + '" x2="' + (w - padR) + '" y2="' + y.toFixed(1) +
        '" stroke="var(--rule)" stroke-width="1" />');
      parts.push('<text x="' + (padL - 9) + '" y="' + (y + 4).toFixed(1) +
        '" text-anchor="end" fill="var(--ink-faint)" font-family="IBM Plex Mono, monospace" font-size="10.5">' +
        W.formatVolume(peak * f) + "</text>");
    });

    var step = Math.max(1, Math.floor(days.length / 8));
    days.forEach(function (day, i) {
      var x = padL + slot * i + (slot - barW) / 2;
      var y = padT + plotH;
      // Stack the services so a day shows both its size and its mix.
      ids.forEach(function (id) {
        var ml = (agg.byDay[day][id] || 0) * mlPerWh();
        if (!ml) return;
        var barH = peak ? (ml / peak) * plotH : 0;
        y -= barH;
        parts.push('<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + barW.toFixed(1) +
          '" height="' + Math.max(barH, 0.8).toFixed(1) + '" fill="' + serviceAccent(id) + '"><title>' +
          esc(day) + " &middot; " + esc(serviceLabel(id)) + ": " + W.formatVolume(ml) + "</title></rect>");
      });
      if (i % step === 0 || i === days.length - 1) {
        parts.push('<text x="' + (x + barW / 2).toFixed(1) + '" y="' + (h - 12) +
          '" text-anchor="middle" fill="var(--ink-faint)" font-family="IBM Plex Mono, monospace" font-size="10">' +
          esc(day.slice(5)) + "</text>");
      }
    });

    el("daily").innerHTML =
      '<svg viewBox="0 0 ' + w + " " + h + '" width="100%" height="' + h +
      '" preserveAspectRatio="xMinYMid meet" role="img" aria-label="Water drawn per day by service">' +
      parts.join("") + "</svg>";
  }

  function renderEntries() {
    var section = el("entries-section");
    if (!state.entries.length) {
      section.hidden = true;
      el("entries").innerHTML = "";  // don't leave deleted rows in the DOM
      return;
    }
    section.hidden = false;
    var sorted = state.entries.slice().sort(function (a, b) {
      return a.date < b.date ? 1 : a.date > b.date ? -1 : 0;
    });
    el("entries").innerHTML =
      '<dl class="rows">' + sorted.map(function (e) {
        var ml = e.count * e.units * serviceWhPerUnit(e.service) * mlPerWh();
        return '<div class="row entry"><dt><span class="chip" style="background:' + serviceAccent(e.service) +
          '"></span>' + esc(e.date) + " &middot; " + esc(serviceLabel(e.service)) + " &middot; " + esc(e.label) +
          ' <span class="sub">&times;' + e.count + "</span></dt><dd>" + W.formatVolume(ml) +
          '<button type="button" class="del" data-id="' + esc(e.id) + '" aria-label="Delete entry">&times;</button></dd></div>';
      }).join("") + "</dl>";
  }

  function renderQuickAdd() {
    var html = "";
    Object.keys(CONSTANTS.services).forEach(function (id) {
      var svc = CONSTANTS.services[id];
      if (svc.kind === "tokens") return;
      presetsFor(id).forEach(function (op) {
        html += '<button type="button" class="preset" data-service="' + esc(id) + '" data-label="' +
          esc(op.label) + '" data-units="' + op.units + '"><span class="chip" style="background:' +
          serviceAccent(id) + '"></span>' + esc(op.label) +
          '<span class="preset-cost">' + op.units + " " + esc(svc.unit) + (op.units === 1 ? "" : "s") + "</span></button>";
      });
    });
    el("quickadd").innerHTML = html;

    var options = "";
    Object.keys(CONSTANTS.services).forEach(function (id) {
      var svc = CONSTANTS.services[id];
      if (svc.kind === "tokens") return;
      options += '<optgroup label="' + esc(svc.label) + '">';
      presetsFor(id).forEach(function (op) {
        options += '<option value="' + esc(id) + "|" + esc(op.label) + "|" + op.units + '">' +
          esc(op.label) + " (" + op.units + " " + esc(svc.unit) + (op.units === 1 ? "" : "s") + ")</option>";
      });
      options += "</optgroup>";
    });
    el("f-op").innerHTML = options;

    var svcOptions = Object.keys(CONSTANTS.services).filter(function (id) {
      return CONSTANTS.services[id].kind !== "tokens";
    }).map(function (id) {
      return '<option value="' + esc(id) + '">' + esc(CONSTANTS.services[id].label) + "</option>";
    }).join("");
    el("c-service").innerHTML = svcOptions;
  }

  function renderSettings() {
    var base = CONSTANTS.claude_tokens_wh;
    var fields = [
      { path: "ml_per_wh", label: "Water per energy", unit: "mL/Wh", value: mlPerWh(), step: 0.01 },
      { path: "wh.output", label: "Claude output token", unit: "Wh", value: tokenRates().output, step: 0.00001 },
      { path: "wh.input", label: "Claude input token", unit: "Wh", value: tokenRates().input, step: 0.00001 },
      { path: "wh.cache_write", label: "Claude cache write", unit: "Wh", value: tokenRates().cache_write, step: 0.00001 },
      { path: "wh.cache_read", label: "Claude cache read", unit: "Wh", value: tokenRates().cache_read, step: 0.000001 },
    ];
    Object.keys(CONSTANTS.services).forEach(function (id) {
      var svc = CONSTANTS.services[id];
      if (svc.kind === "tokens") return;
      fields.push({
        path: "svc." + id,
        label: svc.label + " per " + svc.unit,
        unit: "Wh",
        value: serviceWhPerUnit(id),
        step: 0.01,
      });
    });

    el("settings-grid").innerHTML = fields.map(function (f) {
      return '<div class="field"><label for="set-' + esc(f.path) + '">' + esc(f.label) +
        ' <span class="sub">' + esc(f.unit) + '</span></label><input type="number" id="set-' + esc(f.path) +
        '" data-path="' + esc(f.path) + '" value="' + f.value + '" step="' + f.step + '" min="0"></div>';
    }).join("");

    el("note-rate").textContent = mlPerWh() + " mL/Wh";
    el("note-out").textContent = (tokenRates().output * 1000).toFixed(2) + " mWh";
  }

  /* ---------- render everything ---------- */

  function render() {
    var agg = aggregate();
    renderRegister(agg.totalMl);
    var ref = renderDial(agg.totalMl);
    renderRail(agg.totalMl);
    renderSources(agg);
    renderReading(agg);
    renderDaily(agg);
    renderEntries();
    renderSettings();

    el("demo-banner").hidden = !state.demo;

    if (agg.totalMl > 0) {
      var near = W.nearestComparison(agg.totalMl, CONSTANTS.comparisons);
      el("reading-note").innerHTML =
        "Estimated water consumed by the datacenters running your AI usage: <strong>" +
        W.formatVolume(agg.totalMl) + "</strong>, or about <strong>" +
        Math.round((agg.totalMl / ref.ml) * 100) + "% of " + esc(ref.label) + "</strong>" +
        (near.count >= 1 ? " &mdash; " + near.count.toFixed(1) + "&times; " + esc(near.label) : "") + ".";
      el("period-range").textContent = agg.first === agg.last ? agg.first : agg.first + " – " + agg.last;
      el("period-detail").textContent =
        agg.days.length + (agg.days.length === 1 ? " active day" : " active days");
    } else {
      el("reading-note").innerHTML =
        "Nothing measured yet. Import your Claude usage, or log a Weavy or Gemini operation, and the meter starts turning.";
      el("period-range").textContent = "—";
      el("period-detail").textContent = "no usage logged yet";
    }
  }

  /* ---------- actions ---------- */

  /* Seeded example figures must never end up mixed into a real reading, so the
   * first genuine import or entry discards them entirely rather than merely
   * hiding the banner. */
  function dropDemo() {
    if (!state.demo) return;
    state.imports = {};
    state.entries = [];
    state.demo = false;
  }

  function addEntry(service, label, units, count, date) {
    dropDemo();
    // Fold into an existing entry for the same operation and day, so tapping a
    // preset five times reads as one line of five rather than five lines.
    var match = state.entries.filter(function (e) {
      return e.service === service && e.label === label && e.units === units && e.date === date;
    })[0];
    if (match) {
      match.count += count;
    } else {
      state.entries.push({
        id: "e" + Date.now() + Math.random().toString(36).slice(2, 7),
        service: service, label: label, units: units, count: count, date: date,
      });
    }
    save();
    render();
  }

  function today() {
    var d = new Date();
    return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  }

  /* ---------- imports ---------- */

  function status(msg, isError) {
    var node = el("drop-status");
    node.hidden = false;
    node.textContent = msg;
    node.classList.toggle("error", !!isError);
  }

  function readFile(file) {
    return new Promise(function (resolve) {
      var reader = new FileReader();
      reader.onload = function () { resolve(String(reader.result || "")); };
      reader.onerror = function () { resolve(""); };
      reader.readAsText(file);
    });
  }

  /** Expand any archives, then route every member by what it actually is. */
  async function collectMembers(files) {
    var members = [];
    var failures = [];
    for (var i = 0; i < files.length; i++) {
      var file = files[i];
      if (/\.zip$/i.test(file.name)) {
        try {
          var entries = await W.readZip(await file.arrayBuffer());
          entries.forEach(function (entry) { members.push(entry); });
        } catch (e) {
          failures.push(file.name + ": " + e.message);
        }
      } else {
        members.push({ name: file.name, text: await readFile(file) });
      }
    }
    return { members: members, failures: failures };
  }

  async function importFiles(files) {
    var found = await collectMembers(Array.prototype.slice.call(files));
    var members = found.members;

    var transcripts = members.filter(function (m) { return /\.jsonl$/i.test(m.name); });
    var jsons = members.filter(function (m) { return /\.json$/i.test(m.name); });

    if (!transcripts.length && !jsons.length) {
      status(found.failures.length
        ? found.failures.join("; ")
        : "No transcripts or export found in that selection.", true);
      return;
    }

    status("Reading " + members.length + " file(s)…");
    dropDemo();

    if (transcripts.length) {
      var seen = new Set();
      var out = W.emptyImport();
      transcripts.forEach(function (m) { W.parseTranscript(m.text, seen, out); });
      if (out.calls) {
        out.importedAt = new Date().toISOString();
        state.imports["claude-code"] = out;
      }
    }

    // The chat export splits across several files, so accumulate rather than
    // letting the last one win.
    var chat = W.emptyImport();
    var exportedCalls = 0;

    for (var j = 0; j < jsons.length; j++) {
      var parsed;
      try {
        parsed = JSON.parse(jsons[j].text);
      } catch (e) {
        continue;
      }

      // A summary from `water_meter.py --export`: already counted and deduped.
      if (parsed && parsed.format === "ai-water-meter-export") {
        state.imports[parsed.source || "claude-code"] = {
          calls: parsed.calls || 0,
          tokens: W.addTokens(W.emptyTokens(), parsed.tokens || {}),
          days: parsed.days || {},
          importedAt: new Date().toISOString(),
        };
        exportedCalls += parsed.calls || 0;
        continue;
      }

      W.parseConversations(parsed, chat);
    }

    if (chat.calls) {
      chat.importedAt = new Date().toISOString();
      state.imports["claude-chat"] = chat;
    }

    save();
    render();

    var parts = [];
    var cc = state.imports["claude-code"];
    if (cc && transcripts.length) parts.push(W.formatInt(cc.calls) + " Claude Code API calls");
    if (exportedCalls) parts.push(W.formatInt(exportedCalls) + " API calls from an export");
    if (chat.calls) parts.push(W.formatInt(chat.calls) + " chat responses");
    var message = parts.length
      ? "Imported " + parts.join(" and ") + "."
      : "Nothing countable found in those files.";
    if (found.failures.length) message += " (" + found.failures.join("; ") + ")";
    status(message, !parts.length);
  }

  /* ---------- wiring ---------- */

  function wire() {
    el("f-date").value = today();

    el("quickadd").addEventListener("click", function (ev) {
      var btn = ev.target.closest(".preset");
      if (!btn) return;
      addEntry(btn.dataset.service, btn.dataset.label, Number(btn.dataset.units), 1, today());
    });

    el("addform").addEventListener("submit", function (ev) {
      ev.preventDefault();
      var parts = el("f-op").value.split("|");
      var count = Math.max(1, Math.round(Number(el("f-count").value) || 1));
      addEntry(parts[0], parts[1], Number(parts[2]), count, el("f-date").value || today());
    });

    el("c-service").addEventListener("change", function () {
      var svc = CONSTANTS.services[el("c-service").value];
      el("c-units-label").textContent = svc.unit.charAt(0).toUpperCase() + svc.unit.slice(1) + "s each";
    });

    el("customform").addEventListener("submit", function (ev) {
      ev.preventDefault();
      var service = el("c-service").value;
      var label = el("c-label").value.trim();
      var units = Number(el("c-units").value);
      if (!label || !isFinite(units) || units < 0) return;
      if (!state.presets[service]) state.presets[service] = [];
      state.presets[service].push({ label: label, units: units });
      save();
      renderQuickAdd();
      el("c-label").value = "";
    });

    el("entries").addEventListener("click", function (ev) {
      var btn = ev.target.closest(".del");
      if (!btn) return;
      state.entries = state.entries.filter(function (e) { return e.id !== btn.dataset.id; });
      save();
      render();
    });

    el("settings-grid").addEventListener("change", function (ev) {
      var input = ev.target.closest("input[data-path]");
      if (!input) return;
      var value = Number(input.value);
      if (!isFinite(value) || value < 0) return;
      state.overrides[input.dataset.path] = value;
      save();
      render();
    });

    el("reset-settings").addEventListener("click", function () {
      state.overrides = {};
      save();
      render();
    });

    el("clear-demo").addEventListener("click", function () {
      state = blankState();
      save();
      render();
    });

    el("pick-dir").addEventListener("change", function (ev) { importFiles(ev.target.files); });
    el("pick-file").addEventListener("change", function (ev) { importFiles(ev.target.files); });

    var drop = el("drop");
    ["dragenter", "dragover"].forEach(function (type) {
      drop.addEventListener(type, function (ev) {
        ev.preventDefault();
        drop.classList.add("over");
      });
    });
    ["dragleave", "drop"].forEach(function (type) {
      drop.addEventListener(type, function (ev) {
        ev.preventDefault();
        if (type === "dragleave" && drop.contains(ev.relatedTarget)) return;
        drop.classList.remove("over");
      });
    });
    drop.addEventListener("drop", function (ev) {
      if (ev.dataTransfer && ev.dataTransfer.files.length) importFiles(ev.dataTransfer.files);
    });
  }

  /* ---------- boot ---------- */

  async function boot() {
    // The single-file build inlines the constants; the served app fetches them.
    CONSTANTS = window.WATER_CONSTANTS ||
      (await fetch("constants.json").then(function (r) { return r.json(); }));
    var stored = await load();
    state = stored && stored.v === 1 ? stored : demoState();
    if (!state.presets) state.presets = {};
    if (!state.overrides) state.overrides = {};
    if (!state.entries) state.entries = [];
    if (!state.imports) state.imports = {};
    renderQuickAdd();
    wire();
    render();

    if ("serviceWorker" in navigator && location.protocol.startsWith("http")) {
      navigator.serviceWorker.register("sw.js").catch(function () {
        /* offline support is a bonus, not a requirement */
      });
    }
  }

  boot();
})();
