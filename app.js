/* Sit-Rep front-end. Vanilla JS + uPlot. Hash-routed pages: #/ (overview), #/<section>. */
(function () {
  "use strict";
  var CAT = null, MAN = null, charts = {}, PAYLOADS = {}, io = null;

  /* theme */
  var pref = localStorage.getItem("gt-theme") ||
    (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.documentElement.dataset.theme = pref;
  document.getElementById("themeBtn").onclick = function () {
    pref = pref === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = pref;
    localStorage.setItem("gt-theme", pref);
    Object.keys(charts).forEach(function (id) { if (charts[id].u) redraw(id); });
  };

  function css(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }
  function ts(iso) { return Date.parse(iso + "T12:00:00Z") / 1000; }

  function fmtNum(v, dec) {
    if (v == null || isNaN(v)) return "–";
    var a = Math.abs(v);
    if (a >= 1e12) return (v / 1e12).toFixed(2) + "T";
    if (a >= 1e9) return (v / 1e9).toFixed(2) + "B";
    if (a >= 1e6) return (v / 1e6).toFixed(2) + "M";
    return v.toLocaleString("en-US", { minimumFractionDigits: dec, maximumFractionDigits: dec });
  }
  function fmtVal(v, cfg) {
    if (v == null || isNaN(v)) return "–";
    var dec = cfg.dec != null ? cfg.dec : 1;
    if (cfg.fmt === "sci") { var e = Math.floor(Math.log10(v)); return (v / Math.pow(10, e)).toFixed(1) + "e" + e; }
    if (cfg.fmt === "pct") return v.toFixed(dec) + (cfg.unit === "%" ? "%" : "");
    if (cfg.fmt === "usd") return "$" + fmtNum(v, dec);
    return fmtNum(v, dec);
  }
  function unitSuffix(cfg) {
    if (cfg.unit === "%" || cfg.fmt === "sci") return "";
    if (cfg.fmt === "usd" && cfg.unit && cfg.unit[0] === "$") return cfg.unit.slice(1);
    return " " + (cfg.unit || "");
  }

  function freshClass(cid) {
    var m = (MAN.charts || {})[cid] || {};
    if (m.status === "failed") return "failed";
    if (m.status === "manual") return "manual";
    var exp = CAT.expect_days[(CAT.charts[cid] || {}).freq] || 75;
    if (!m.last_obs) return "stale";
    var age = (Date.now() - Date.parse(m.last_obs)) / 864e5;
    return age > exp ? "stale" : "ok";
  }

  function deltaStr(s, cfg) {
    var n = s.t.length;
    if (n < 2) return "";
    var last = s.v[n - 1], lastT = Date.parse(s.t[n - 1]);
    var idx = -1;
    for (var i = n - 2; i >= 0; i--) {
      if (lastT - Date.parse(s.t[i]) >= 360 * 864e5) { idx = i; break; }
    }
    var base, lbl;
    if (idx >= 0) { base = s.v[idx]; lbl = "y/y"; }
    else { base = s.v[n - 2]; lbl = "vs prior"; }
    if (base == null) return "";
    var d;
    if (cfg.fmt === "pct") d = (last - base).toFixed(1) + " pp " + lbl;
    else if (base !== 0) d = ((last / base - 1) * 100).toFixed(1) + "% " + lbl;
    else return "";
    return (d[0] !== "-" ? "+" : "") + d;
  }

  var RANGES = [["1Y", 1], ["5Y", 5], ["10Y", 10], ["25Y", 25], ["Max", 9999]];

  function buildCard(cid) {
    var cfg = CAT.charts[cid];
    var card = document.createElement("div");
    card.className = "card";
    card.id = "c-" + cid;
    var fc = freshClass(cid);
    var m = (MAN.charts || {})[cid] || {};
    var html = '<div class="top"><h3>' + cfg.title + '</h3><span class="dot ' + fc + '" title="' +
      (fc === "failed" ? "feed error: " + (m.error || "") : fc === "manual" ? "curated series" : "last obs " + (m.last_obs || "?")) +
      '"></span></div>';
    html += '<div class="stat"><span class="val"></span><span class="unit"></span><span class="delta"></span></div>';
    html += '<div class="chart"></div>';
    if (cfg.kind !== "bars" && fc !== "failed") {
      html += '<div class="ranges">' + RANGES.map(function (r) {
        return '<button data-y="' + r[1] + '">' + r[0] + "</button>";
      }).join("") + "</div>";
    }
    if (cfg.note) html += '<div class="note">' + cfg.note + "</div>";
    html += '<div class="foot"><a href="' + cfg.source_url + '" target="_blank" rel="noopener">' + cfg.source_name +
      '</a><span>' + (m.last_obs ? "through " + m.last_obs : "") + "</span></div>";
    card.innerHTML = html;
    return card;
  }

  function seriesColors(n) {
    return n === 1 ? [css("--s1")] : [css("--s1"), css("--s2"), "#9061f9", "#0e9384"];
  }

  function render(cid, payload) {
    var cfg = CAT.charts[cid];
    var card = document.getElementById("c-" + cid);
    if (!card) return;
    var host = card.querySelector(".chart");
    if (cfg.kind === "bars") { renderBars(host, payload.bars, card); return; }
    if (!payload.series || !payload.series.length || !payload.series[0].t.length) {
      card.classList.add("err"); host.textContent = "no data"; return;
    }
    var ss = payload.series;
    var xs = {};
    ss.forEach(function (s) { s.t.forEach(function (d) { xs[d] = 1; }); });
    var dates = Object.keys(xs).sort();
    var x = dates.map(ts);
    var data = [x];
    ss.forEach(function (s) {
      var map = {};
      s.t.forEach(function (d, i) { map[d] = s.v[i]; });
      data.push(dates.map(function (d) { return map[d] != null ? map[d] : null; }));
    });
    var last = ss[0];
    card.querySelector(".val").textContent = fmtVal(last.v[last.v.length - 1], cfg);
    card.querySelector(".unit").textContent = unitSuffix(cfg);
    card.querySelector(".delta").textContent = deltaStr(last, cfg);
    charts[cid] = { data: data, cfg: cfg, host: host, names: ss.map(function (s) { return s.name; }) };
    var btns = card.querySelectorAll(".ranges button");
    btns.forEach(function (b) {
      b.onclick = function () {
        btns.forEach(function (o) { o.classList.remove("on"); });
        b.classList.add("on");
        charts[cid].years = +b.dataset.y;
        redraw(cid);
      };
    });
    var defYears = cfg.rng === "max" ? 9999 : (cfg.rng || 10);
    charts[cid].years = defYears;
    var hit = null;
    btns.forEach(function (b) { if (+b.dataset.y === defYears) hit = b; });
    if (hit) hit.classList.add("on");
    else if (btns.length) btns[btns.length - 1].classList.add("on");
    redraw(cid);
  }

  function redraw(cid) {
    var c = charts[cid];
    if (!c || !document.getElementById("c-" + cid)) return;
    if (c.u) { c.u.destroy(); c.u = null; }
    var cfg = c.cfg, data = c.data;
    var lastX = data[0][data[0].length - 1];
    var minX = c.years >= 9999 ? data[0][0] : lastX - c.years * 365.25 * 86400;
    if (minX < data[0][0]) minX = data[0][0];
    var i0 = 0;
    while (i0 < data[0].length - 1 && data[0][i0] < minX) i0++;
    var view = data.map(function (col) { return col.slice(i0); });
    var w = c.host.clientWidth || 420;
    var colors = seriesColors(data.length - 1);
    var series = [{}].concat(c.names.map(function (nm, i) {
      var s = { label: nm, stroke: colors[i], width: 1.6, spanGaps: true };
      if (cfg.kind === "scatter") {
        s.paths = function () { return null; };
        s.points = { show: true, size: 4, fill: colors[i], stroke: colors[i] };
      } else {
        s.points = { show: false };
      }
      return s;
    }));
    var scales = { x: { time: true } };
    if (cfg.log) scales.y = { distr: 3 };
    var axes = [
      { stroke: css("--muted"), grid: { stroke: css("--line") }, ticks: { stroke: css("--line") } },
      { stroke: css("--muted"), grid: { stroke: css("--line") }, ticks: { stroke: css("--line") },
        size: 56,
        values: function (u, vals) {
          return vals.map(function (v) {
            if (cfg.log) { var e = Math.round(Math.log10(v)); return "1e" + e; }
            return fmtNum(v, Math.abs(v) < 10 && cfg.dec > 0 ? 1 : 0);
          });
        } }
    ];
    var opts = {
      width: w, height: 224,
      series: series, scales: scales, axes: axes,
      legend: { show: data.length > 2 },
      cursor: { points: { size: 5 } },
      padding: [8, 8, 0, 0]
    };
    if (cfg.zero_line) {
      opts.hooks = { drawAxes: [function (u) {
        var y0 = u.valToPos(0, "y", true);
        if (y0 > u.bbox.top && y0 < u.bbox.top + u.bbox.height) {
          var ctx = u.ctx;
          ctx.save(); ctx.strokeStyle = css("--muted"); ctx.setLineDash([4, 4]); ctx.lineWidth = 1;
          ctx.beginPath(); ctx.moveTo(u.bbox.left, y0); ctx.lineTo(u.bbox.left + u.bbox.width, y0); ctx.stroke();
          ctx.restore();
        }
      }] };
    }
    c.u = new uPlot(opts, view, c.host);
  }

  function renderBars(host, bars, card) {
    var st = card.querySelector(".stat");
    if (st) st.remove();
    if (!bars || !bars.labels || !bars.labels.length) {
      card.classList.add("err"); host.textContent = "no data"; return;
    }
    var max = Math.max.apply(null, bars.pct.map(Math.abs)) || 1;
    var div = document.createElement("div");
    div.className = "bars";
    bars.labels.forEach(function (lb, i) {
      var p = bars.pct[i];
      var wpct = Math.min(Math.abs(p) / max * 50, 50);
      var row = document.createElement("div");
      row.className = "brow";
      row.innerHTML = "<span>" + lb + '</span><div class="track"><div class="mid"></div><div class="bar" style="' +
        (p < 0 ? "right:50%;background:var(--pos)" : "left:50%;background:var(--neg)") +
        ";width:" + wpct + '%"></div></div><span class="pv" style="color:var(--' + (p < 0 ? "pos" : "neg") + ')">' +
        (p > 0 ? "+" : "") + p.toFixed(1) + "%</span>";
      div.appendChild(row);
    });
    var cap = document.createElement("div");
    cap.className = "note";
    cap.textContent = "Year-to-date vs. same period last year, through " + (bars.through || "–") + ". Green = falling crime.";
    host.replaceWith(div);
    div.after(cap);
  }

  function loadChart(cid) {
    if (PAYLOADS[cid]) { render(cid, PAYLOADS[cid]); return; }
    fetch("data/" + cid + ".json").then(function (r) {
      if (!r.ok) throw new Error(r.status);
      return r.json();
    }).then(function (p) { PAYLOADS[cid] = p; render(cid, p); })
      .catch(function () {
        var card = document.getElementById("c-" + cid);
        if (!card) return;
        card.classList.add("err");
        card.querySelector(".chart").textContent = "data unavailable (feed may not have run yet)";
      });
  }

  /* ---- routing ---- */
  function pageId() {
    var h = location.hash.replace(/^#\/?/, "");
    return h && CAT.sections.some(function (s) { return s.id === h; }) ? h : "overview";
  }

  function renderPage() {
    Object.keys(charts).forEach(function (id) { if (charts[id].u) charts[id].u.destroy(); });
    charts = {};
    if (io) io.disconnect();
    var pid = pageId();
    var main = document.getElementById("main");
    main.innerHTML = "";
    document.querySelectorAll("#nav a").forEach(function (a) {
      a.classList.toggle("on", a.dataset.pg === pid);
    });
    var wrap = document.createElement("section");
    var ids;
    if (pid === "overview") {
      wrap.innerHTML = '<div class="pagehead"><h2>Overview</h2>' +
        '<p>The vital signs — the most important series from every domain. Open a domain page for the full picture.</p></div>';
      ids = CAT.overview || [];
    } else {
      var sec = CAT.sections.filter(function (s) { return s.id === pid; })[0];
      wrap.innerHTML = '<div class="pagehead"><h2>' + sec.title + "</h2><p>" + (sec.desc || "") + "</p></div>";
      ids = sec.charts;
    }
    var g = document.createElement("div");
    g.className = "grid";
    ids.forEach(function (cid) { if (CAT.charts[cid]) g.appendChild(buildCard(cid)); });
    wrap.appendChild(g);
    main.appendChild(wrap);
    io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) {
          io.unobserve(e.target);
          loadChart(e.target.id.slice(2));
        }
      });
    }, { rootMargin: "300px" });
    g.querySelectorAll(".card").forEach(function (el) { io.observe(el); });
    window.scrollTo(0, 0);
  }

  window.addEventListener("hashchange", function () { if (CAT) renderPage(); });

  var rt;
  window.addEventListener("resize", function () {
    clearTimeout(rt);
    rt = setTimeout(function () {
      Object.keys(charts).forEach(function (id) { if (charts[id].u) redraw(id); });
    }, 200);
  });

  Promise.all([
    fetch("data/catalog.json").then(function (r) { return r.json(); }),
    fetch("data/manifest.json").then(function (r) { return r.json(); }).catch(function () { return { charts: {} }; })
  ]).then(function (res) {
    CAT = res[0]; MAN = res[1];
    document.getElementById("updated").textContent = MAN.generated_at ? "data as of " + MAN.generated_at : "";
    var nav = document.getElementById("nav");
    var home = document.createElement("a");
    home.href = "#/"; home.textContent = "Overview"; home.dataset.pg = "overview";
    nav.appendChild(home);
    CAT.sections.forEach(function (sec) {
      if (!sec.charts.length) return;
      var a = document.createElement("a");
      a.href = "#/" + sec.id; a.textContent = sec.title; a.dataset.pg = sec.id;
      nav.appendChild(a);
    });
    renderPage();
  }).catch(function (e) {
    document.getElementById("main").innerHTML =
      '<p class="loading">Could not load data/catalog.json — the data pipeline may not have run yet. (' + e + ")</p>";
  });
})();
