/**
 * app.js — VN Bond Dashboard Frontend Logic
 * ==========================================
 * Xử lý: load data JSON, vẽ chart D3/Chart.js, animation, navigation.
 *
 * Kiến trúc: Static HTML + JSON data (không cần server)
 * JSON data được export từ Python pipeline mỗi lần chạy.
 */

'use strict';

/* ── Configuration ──────────────────────────────────────────────────── */
const CONFIG = {
  DATA_DIR: '../exports/data/',   // Thư mục chứa JSON data
  REFRESH_INTERVAL: 0,            // 0 = không tự refresh
  ANIMATION_DURATION: 600,        // ms
  TOOLTIP_DELAY: 50,
};

const TENOR_COLORS = {
  '3M': '#38bdf8', '6M': '#7dd3fc', '1Y': '#818cf8',
  '2Y': '#a78bfa', '3Y': '#c084fc', '5Y': '#34d399',
  '7Y': '#6ee7b7', '10Y': '#fbbf24', '15Y': '#fb923c',
  '20Y': '#ef4444', '30Y': '#dc2626',
};

/* ── State ──────────────────────────────────────────────────────────── */
const state = {
  yieldData: null,     // combined_tpcp_yields.json
  curveData: null,     // fitted_curve_ns.json
  auctionData: null,   // hnx_auctions_daily.json
  spreadData: null,    // spread_analysis.json
  foreignData: null,   // foreign_daily_flow.json
  currentDate: null,
  selectedTenors: ['2Y', '5Y', '10Y'],
};

/* ── Data Loading ───────────────────────────────────────────────────── */
async function loadJSON(filename) {
  try {
    const resp = await fetch(CONFIG.DATA_DIR + filename);
    if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
    return await resp.json();
  } catch (e) {
    console.warn(`Không tải được ${filename}:`, e.message);
    return null;
  }
}

async function loadAllData() {
  const [curve, spread, auction, foreign] = await Promise.all([
    loadJSON('fitted_curve_ns.json'),
    loadJSON('spread_analysis.json'),
    loadJSON('auction_stats.json'),
    loadJSON('foreign_flow.json'),
  ]);

  state.curveData = curve;
  state.spreadData = spread;
  state.auctionData = auction;
  state.foreignData = foreign;

  if (curve && curve.length > 0) {
    const dates = [...new Set(curve.map(d => d.date))].sort();
    state.currentDate = dates[dates.length - 1];
  }
}

/* ── Number Formatting ──────────────────────────────────────────────── */
const fmt = {
  rate: v => v != null ? v.toFixed(3) + '%' : 'N/A',
  bps: v => v != null ? (v * 100).toFixed(1) + ' bps' : 'N/A',
  bn: v => v != null ? v.toFixed(1) + ' tỷ' : 'N/A',
  pct: v => v != null ? (v * 100).toFixed(1) + '%' : 'N/A',
  date: d => d ? d.substring(0, 10) : '',
  num: (v, decimals = 2) => v != null ? v.toFixed(decimals) : 'N/A',
};

function signClass(v) {
  if (v == null) return 'neutral';
  return v > 0 ? 'up' : v < 0 ? 'down' : 'neutral';
}

function signSymbol(v) {
  if (v == null) return '';
  return v > 0 ? '▲' : v < 0 ? '▼' : '—';
}

/* ── Yield Curve Chart (D3.js) ──────────────────────────────────────── */
function drawYieldCurve(containerId, data, selectedDate, compareData = null) {
  const container = document.getElementById(containerId);
  if (!container || !data || data.length === 0) return;

  const dayData = data.filter(d => d.date === selectedDate).sort((a,b) => a.tenor_yr - b.tenor_yr);
  if (!dayData.length) {
    container.innerHTML = '<div style="padding: 2rem; color: var(--text-muted); text-align:center;">Không có dữ liệu cho ngày này</div>';
    return;
  }

  // Xóa SVG cũ
  container.innerHTML = '';

  const margin = { top: 20, right: 30, bottom: 50, left: 55 };
  const width = container.clientWidth - margin.left - margin.right;
  const height = container.clientHeight - margin.top - margin.bottom;

  const svg = d3.select(`#${containerId}`)
    .append('svg')
    .attr('width', '100%')
    .attr('height', '100%')
    .attr('viewBox', `0 0 ${width + margin.left + margin.right} ${height + margin.top + margin.bottom}`)
    .append('g')
    .attr('transform', `translate(${margin.left},${margin.top})`);

  // Scales
  const minTenor = d3.min(dayData, d => d.tenor_yr) || 0.25;
  const maxTenor = d3.max(dayData, d => d.tenor_yr) || 30;
  const x = d3.scaleSqrt()
    .domain([minTenor * 0.6, maxTenor])
    .range([0, width]);

  const allYData = compareData ? [...dayData, ...compareData] : dayData;
  const yExtent = d3.extent(allYData, d => d.yield_pct);
  const yPad = (yExtent[1] - yExtent[0]) * 0.15 || 0.5;
  const y = d3.scaleLinear()
    .domain([yExtent[0] - yPad, yExtent[1] + yPad])
    .range([height, 0]);

  // Gradient fill
  const defs = svg.append('defs');
  const gradient = defs.append('linearGradient')
    .attr('id', 'curve-gradient')
    .attr('x1', '0%').attr('y1', '0%')
    .attr('x2', '0%').attr('y2', '100%');
  gradient.append('stop').attr('offset', '0%')
    .attr('stop-color', '#3b82f6').attr('stop-opacity', 0.3);
  gradient.append('stop').attr('offset', '100%')
    .attr('stop-color', '#3b82f6').attr('stop-opacity', 0.02);

  // Grid lines
  svg.append('g')
    .attr('class', 'grid')
    .call(d3.axisLeft(y).tickSize(-width).tickFormat(''))
    .call(g => g.select('.domain').remove())
    .call(g => g.selectAll('line')
      .attr('stroke', 'var(--border-subtle)').attr('stroke-dasharray', '3,3'));

  // Area fill
  const area = d3.area()
    .x(d => x(d.tenor_yr))
    .y0(height)
    .y1(d => y(d.yield_pct))
    .curve(d3.curveCatmullRom.alpha(0.5));

  svg.append('path')
    .datum(dayData)
    .attr('fill', 'url(#curve-gradient)')
    .attr('d', area);

  // Main Line
  const line = d3.line()
    .x(d => x(d.tenor_yr))
    .y(d => y(d.yield_pct))
    .curve(d3.curveCatmullRom.alpha(0.5));

  // Compare Curve
  if (compareData && compareData.length > 0) {
    const compSorted = compareData.sort((a,b) => a.tenor_yr - b.tenor_yr);
    
    svg.append('path')
      .datum(compSorted)
      .attr('fill', 'none')
      .attr('stroke', 'var(--text-muted)')
      .attr('stroke-width', 2)
      .attr('stroke-dasharray', '5,5')
      .attr('d', line);
      
    svg.selectAll('.curve-point-comp')
      .data(compSorted)
      .enter().append('circle')
      .attr('class', 'curve-point-comp')
      .attr('cx', d => x(d.tenor_yr))
      .attr('cy', d => y(d.yield_pct))
      .attr('r', 3)
      .attr('fill', 'var(--bg-card)')
      .attr('stroke', 'var(--text-muted)')
      .attr('stroke-width', 1.5)
      .style('cursor', 'pointer')
      .on('mouseover', function(event, d) {
        d3.select(this).attr('r', 5).attr('fill', 'var(--text-muted)');
        showTooltip(event, `
          <div class="tooltip-title">${tenorLabel(d.tenor_yr)} (So sánh)</div>
          <div class="tooltip-row">
            <span>Ngày</span>
            <span class="tooltip-value">${d.date}</span>
          </div>
          <div class="tooltip-row">
            <span>Lợi suất</span>
            <span class="tooltip-value">${fmt.rate(d.yield_pct)}</span>
          </div>
        `);
      })
      .on('mousemove', (event) => moveTooltip(event))
      .on('mouseout', function() {
        d3.select(this).attr('r', 3).attr('fill', 'var(--bg-card)');
        hideTooltip();
      });
  }

  const path = svg.append('path')
    .datum(dayData)
    .attr('class', 'curve-line')
    .attr('stroke', '#3b82f6')
    .attr('stroke-width', 2.5)
    .attr('fill', 'none')
    .attr('d', line);

  // Animate path draw
  const totalLength = path.node().getTotalLength();
  path
    .attr('stroke-dasharray', `${totalLength} ${totalLength}`)
    .attr('stroke-dashoffset', totalLength)
    .transition().duration(CONFIG.ANIMATION_DURATION)
    .ease(d3.easeQuadOut)
    .attr('stroke-dashoffset', 0);

  // Data points
  const tooltip = d3.select('body').select('.tooltip');

  svg.selectAll('.curve-point')
    .data(dayData)
    .enter().append('circle')
    .attr('class', 'curve-point')
    .attr('cx', d => x(d.tenor_yr))
    .attr('cy', d => y(d.yield_pct))
    .attr('r', 4)
    .attr('fill', '#3b82f6')
    .attr('stroke', '#0f172a')
    .attr('stroke-width', 1.5)
    .style('cursor', 'pointer')
    .on('mouseover', function(event, d) {
      d3.select(this).attr('r', 7).attr('fill', '#60a5fa');
      showTooltip(event, `
        <div class="tooltip-title">${tenorLabel(d.tenor_yr)}</div>
        <div class="tooltip-row">
          <span>Lợi suất</span>
          <span class="tooltip-value">${fmt.rate(d.yield_pct)}</span>
        </div>
      `);
    })
    .on('mousemove', (event) => moveTooltip(event))
    .on('mouseout', function() {
      d3.select(this).attr('r', 4).attr('fill', '#3b82f6');
      hideTooltip();
    });

  // Axes
  const tenorTicks = [0.25, 0.5, 1, 2, 3, 5, 7, 10, 15, 20, 30]
    .filter(t => t <= d3.max(dayData, d => d.tenor_yr));

  svg.append('g')
    .attr('transform', `translate(0,${height})`)
    .call(d3.axisBottom(x)
      .tickValues(tenorTicks)
      .tickFormat(t => tenorLabel(t)))
    .call(g => g.select('.domain').attr('stroke', 'var(--border-subtle)'))
    .call(g => g.selectAll('text').attr('fill', 'var(--text-secondary)').attr('font-size', '11px'))
    .call(g => g.selectAll('line').attr('stroke', 'var(--border-subtle)'));

  svg.append('g')
    .call(d3.axisLeft(y).ticks(5).tickFormat(v => v.toFixed(2) + '%'))
    .call(g => g.select('.domain').attr('stroke', 'var(--border-subtle)'))
    .call(g => g.selectAll('text').attr('fill', 'var(--text-secondary)').attr('font-size', '11px'))
    .call(g => g.selectAll('line').attr('stroke', 'var(--border-subtle)'));

  // X axis label
  svg.append('text')
    .attr('x', width / 2).attr('y', height + 45)
    .attr('text-anchor', 'middle')
    .attr('fill', '#64748b').attr('font-size', '11px')
    .text('Kỳ hạn (năm)');
}

function tenorLabel(yr) {
  const map = {
    0.08: '1M', 0.17: '2M', 0.25: '3M', 0.5: '6M', 0.75: '9M',
    1: '1Y', 1.5: '18M', 2: '2Y', 3: '3Y', 4: '4Y',
    5: '5Y', 6: '6Y', 7: '7Y', 8: '8Y', 9: '9Y',
    10: '10Y', 15: '15Y', 20: '20Y', 30: '30Y',
  };
  return map[yr] || (yr < 1 ? `${Math.round(yr * 12)}M` : `${yr}Y`);
}

/* ── Spread Line Chart ──────────────────────────────────────────────── */
function drawSpreadChart(containerId, spreadData, colKey = 'spread_10y_2y') {
  const container = document.getElementById(containerId);
  if (!container || !spreadData) return;

  const data = spreadData
    .filter(d => d[colKey] != null)
    .map(d => ({ date: new Date(d.date), value: d[colKey] * 100 }))
    .sort((a, b) => a.date - b.date);

  if (data.length === 0) return;

  container.innerHTML = '';

  const margin = { top: 15, right: 20, bottom: 40, left: 55 };
  const width = container.clientWidth - margin.left - margin.right;
  const height = container.clientHeight - margin.top - margin.bottom;

  const svg = d3.select(`#${containerId}`)
    .append('svg').attr('width', '100%').attr('height', '100%')
    .attr('viewBox', `0 0 ${width + margin.left + margin.right} ${height + margin.top + margin.bottom}`)
    .append('g').attr('transform', `translate(${margin.left},${margin.top})`);

  const x = d3.scaleTime().domain(d3.extent(data, d => d.date)).range([0, width]);
  const y = d3.scaleLinear().domain(d3.extent(data, d => d.value)).nice().range([height, 0]);

  // Grid
  svg.append('g')
    .call(d3.axisLeft(y).tickSize(-width).tickFormat(''))
    .call(g => g.select('.domain').remove())
    .call(g => g.selectAll('line').attr('stroke', 'var(--border-subtle)'));

  // Zero line
  if (y.domain()[0] < 0 && y.domain()[1] > 0) {
    svg.append('line')
      .attr('x1', 0).attr('x2', width)
      .attr('y1', y(0)).attr('y2', y(0))
      .attr('stroke', 'rgba(255,255,255,0.25)')
      .attr('stroke-width', 1)
      .attr('stroke-dasharray', '4,2');
  }

  // Colored area: green above 0, red below 0
  const area = d3.area()
    .x(d => x(d.date))
    .y0(y(0))
    .y1(d => y(d.value))
    .curve(d3.curveMonotoneX);

  svg.append('clipPath').attr('id', 'clip-above')
    .append('rect').attr('width', width).attr('height', height);

  svg.append('path')
    .datum(data.filter(d => d.value >= 0))
    .attr('fill', 'rgba(34,197,94,0.12)')
    .attr('clip-path', 'url(#clip-above)')
    .attr('d', area);

  svg.append('path')
    .datum(data.filter(d => d.value < 0))
    .attr('fill', 'rgba(239,68,68,0.15)')
    .attr('d', area);

  // Line
  const line = d3.line()
    .x(d => x(d.date)).y(d => y(d.value))
    .curve(d3.curveMonotoneX);

  svg.append('path')
    .datum(data)
    .attr('fill', 'none')
    .attr('stroke', d3.extent(data, d => d.value)[1] > 0 ? '#22c55e' : '#ef4444')
    .attr('stroke-width', 1.5)
    .attr('d', line);

  // Axes
  svg.append('g').attr('transform', `translate(0,${height})`)
    .call(d3.axisBottom(x).ticks(6))
    .call(g => g.selectAll('text').attr('fill', 'var(--text-secondary)').attr('font-size', '10px'))
    .call(g => g.select('.domain').attr('stroke', 'var(--border-subtle)'));

  svg.append('g')
    .call(d3.axisLeft(y).ticks(5).tickFormat(v => v.toFixed(0) + ' bps'))
    .call(g => g.selectAll('text').attr('fill', 'var(--text-secondary)').attr('font-size', '10px'))
    .call(g => g.select('.domain').attr('stroke', 'var(--border-subtle)'));
}

/* ── Metric Cards ───────────────────────────────────────────────────── */
function updateMetricCard(id, value, change, unit = '%') {
  const card = document.getElementById(id);
  if (!card) return;

  const valueEl = card.querySelector('.metric-value');
  const changeEl = card.querySelector('.metric-change');

  if (valueEl && value != null) {
    valueEl.textContent = value.toFixed(3) + unit;
    valueEl.className = 'metric-value num-mono ' + signClass(change);
  }

  if (changeEl && change != null) {
    changeEl.textContent = signSymbol(change) + ' ' + Math.abs(change * 100).toFixed(1) + ' bps';
    changeEl.className = 'metric-change ' + signClass(change);
  }
}

/* ── Tooltip Helpers ────────────────────────────────────────────────── */
function showTooltip(event, html) {
  let tooltip = document.querySelector('.tooltip');
  if (!tooltip) {
    tooltip = document.createElement('div');
    tooltip.className = 'tooltip';
    document.body.appendChild(tooltip);
  }
  tooltip.innerHTML = html;
  tooltip.style.display = 'block';
  moveTooltip(event);
}

function moveTooltip(event) {
  const tooltip = document.querySelector('.tooltip');
  if (!tooltip) return;
  const x = event.pageX + 12;
  const y = event.pageY - 28;
  tooltip.style.left = Math.min(x, window.innerWidth - 160) + 'px';
  tooltip.style.top = Math.max(y, 8) + 'px';
}

function hideTooltip() {
  const tooltip = document.querySelector('.tooltip');
  if (tooltip) tooltip.style.display = 'none';
}

/* ── Date Slider ─────────────────────────────────────────────────────── */
function initDateSlider(sliderId, curveData, onDateChange) {
  const slider = document.getElementById(sliderId);
  if (!slider || !curveData) return;

  const dates = [...new Set(curveData.map(d => d.date))].sort();
  slider.min = 0;
  slider.max = dates.length - 1;
  slider.value = dates.length - 1;

  slider.addEventListener('input', () => {
    const selectedDate = dates[parseInt(slider.value)];
    onDateChange(selectedDate);
  });

  return dates;
}

/* ── Active Navigation ─────────────────────────────────────────────── */
function setActiveNav() {
  const path = window.location.pathname;
  document.querySelectorAll('.nav-item a').forEach(link => {
    const href = link.getAttribute('href');
    const isActive = href && path.includes(href.replace('..', '').replace('.html', ''));
    link.classList.toggle('active', isActive);
  });
}

/* ── Format date for display ─────────────────────────────────────────── */
function formatDateVN(dateStr) {
  if (!dateStr) return '';
  const d = new Date(dateStr);
  return d.toLocaleDateString('vi-VN', {
    day: '2-digit', month: '2-digit', year: 'numeric'
  });
}

/* ── Live clock ─────────────────────────────────────────────────────── */
function startClock(elementId) {
  const el = document.getElementById(elementId);
  if (!el) return;
  function update() {
    const now = new Date();
    el.textContent = now.toLocaleTimeString('vi-VN', {
      hour: '2-digit', minute: '2-digit', second: '2-digit',
      timeZone: 'Asia/Ho_Chi_Minh'
    }) + ' ICT';
  }
  update();
  setInterval(update, 1000);
}

/* ── Export JSON helper ──────────────────────────────────────────────── */
function downloadJSON(data, filename) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/* ── Resize observer ─────────────────────────────────────────────────── */
function onResize(elementId, callback) {
  const el = document.getElementById(elementId);
  if (!el) return;
  const observer = new ResizeObserver(() => callback());
  observer.observe(el);
}

/* ── Init ─────────────────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
  setActiveNav();
  startClock('live-clock');
  createTooltipContainer();
});

function createTooltipContainer() {
  if (!document.querySelector('.tooltip')) {
    const t = document.createElement('div');
    t.className = 'tooltip';
    t.style.display = 'none';
    document.body.appendChild(t);
  }
}

/* ── Theme Toggle ────────────────────────────────────────────────────── */
(function initTheme() {
  const saved = localStorage.getItem('vnbond-theme') || 'light';
  document.documentElement.setAttribute('data-theme', saved);
  window.addEventListener('DOMContentLoaded', () => {
    const btn = document.getElementById('theme-toggle');
    if (btn) btn.innerHTML = (saved === 'light' ? 'Light mode ' : 'Dark mode ') + '🌓';
  });
})();

function toggleTheme() {
  const html = document.documentElement;
  const current = html.getAttribute('data-theme') || 'light';
  const next = current === 'dark' ? 'light' : 'dark';
  html.setAttribute('data-theme', next);
  localStorage.setItem('vnbond-theme', next);
  const btn = document.getElementById('theme-toggle');
  if (btn) btn.innerHTML = (next === 'light' ? 'Light mode ' : 'Dark mode ') + '🌓';
}

/* ── Export for page scripts ─────────────────────────────────────────── */
window.VNBond = {
  loadAllData,
  loadJSON,
  drawYieldCurve,
  drawSpreadChart,
  updateMetricCard,
  initDateSlider,
  formatDateVN,
  tenorLabel,
  fmt,
  signClass,
  signSymbol,
  TENOR_COLORS,
  state,
  showTooltip,
  moveTooltip,
  hideTooltip,
  downloadJSON,
  onResize,
  toggleTheme,
};
