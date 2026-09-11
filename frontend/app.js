/**
 * IceCast â€” Weddell Sea Ice Prediction Dashboard
 * Frontend application logic with mock data & visualizations
 */

(function () {
    'use strict';

    // ======================================
    // Configuration & Mock Data
    // ======================================

    const CONFIG = {
        startDate: new Date('2023-10-01'),
        endDate: new Date('2024-02-28'),
        gridRows: 73,
        gridCols: 161,
        features: [
            { name: 'siconc', label: 'Sea Ice Concentration', mean: 0.2689, std: 0.4058, min: 0, max: 1 },
            { name: 'u10', label: 'Wind U-component', mean: 0.6038, std: 5.3154, min: -18.2, max: 21.5 },
            { name: 'v10', label: 'Wind V-component', mean: 0.2691, std: 5.0204, min: -19.8, max: 20.1 },
            { name: 'uo', label: 'Current U-component', mean: 0.0025, std: 0.0386, min: -0.42, max: 0.38 },
            { name: 'vo', label: 'Current V-component', mean: 0.0049, std: 0.0352, min: -0.35, max: 0.40 },
            { name: 'wind_speed', label: 'Wind Speed', mean: 6.4612, std: 3.4853, min: 0.1, max: 25.3 },
            { name: 'current_speed', label: 'Current Speed', mean: 0.0283, std: 0.0442, min: 0, max: 0.52 },
            { name: 'wind_dir', label: 'Wind Direction', mean: 0.0114, std: 1.7628, min: -3.14, max: 3.14 },
            { name: 'current_dir', label: 'Current Direction', mean: 0.1314, std: 1.174, min: -3.14, max: 3.14 },
            { name: 'day_sin', label: 'Day of Year (sin)', mean: -0.1964, std: 0.6131, min: -1, max: 1 },
            { name: 'day_cos', label: 'Day of Year (cos)', mean: 0.7147, std: 0.2733, min: -1, max: 1 },
        ],
    };

    // Generate mock time series data
    function generateTimeSeries(numDays) {
        const data = [];
        for (let i = 0; i < numDays; i++) {
            const t = i / numDays;
            // Simulate seasonal decline (Southern summer = ice melt)
            const seasonal = 0.65 - 0.4 * Math.sin(t * Math.PI);
            const noise = (Math.random() - 0.5) * 0.08;
            const iceConc = Math.max(0.05, Math.min(0.95, seasonal + noise));

            data.push({
                date: new Date(CONFIG.startDate.getTime() + i * 86400000),
                iceExtent: (iceConc * 8.2).toFixed(2),
                avgConcentration: (iceConc * 100).toFixed(1),
                windSpeed: (6.5 + Math.sin(t * 4 * Math.PI) * 3 + (Math.random() - 0.5) * 2).toFixed(1),
                iceConc: iceConc,
            });
        }
        return data;
    }

    let totalDays = Math.ceil((CONFIG.endDate - CONFIG.startDate) / 86400000);
    const timeSeries = generateTimeSeries(totalDays);

    // ======================================
    // DOM References
    // ======================================

    const $ = (sel) => document.querySelector(sel);
    const $$ = (sel) => document.querySelectorAll(sel);

    // ======================================
    // Navigation
    // ======================================

    function initNavigation() {
        const navItems = $$('.nav-item');
        const sections = $$('.section');

        navItems.forEach((item) => {
            item.addEventListener('click', (e) => {
                e.preventDefault();
                const sectionId = item.dataset.section;

                // Update nav active state
                navItems.forEach((n) => n.classList.remove('nav-item--active'));
                item.classList.add('nav-item--active');

                // Update section visibility
                sections.forEach((s) => s.classList.remove('section--active'));
                const target = $(`#section-${sectionId}`);
                if (target) {
                    target.classList.add('section--active');
                    // Update header title
                    $('.header__title').textContent =
                        item.querySelector('span').textContent;
                }

                // Redraw canvases for the new section
                requestAnimationFrame(() => {
                    if (sectionId === 'map') drawFullMap();
                    if (sectionId === 'predictions') drawComparisonMaps();
                    if (sectionId === 'analytics') {
                        drawSeasonalChart();
                        drawCorrelationChart();
                    }
                    if (sectionId === 'navigation') {
                        initNavigationMap();
                    }
                });
            });
        });

        // Mobile menu toggle
        const menuToggle = $('#menu-toggle');
        const sidebar = $('#sidebar');
        if (menuToggle) {
            menuToggle.addEventListener('click', () => {
                sidebar.classList.toggle('sidebar--open');
            });
        }
    }

    // ======================================
    // Metrics Cards
    // ======================================

    function updateMetrics(dayIndex) {
        const data = timeSeries[dayIndex] || timeSeries[0];

        animateValue('metric-ice-extent', parseFloat(data.iceExtent));
        animateValue('metric-avg-conc', parseFloat(data.avgConcentration));
        animateValue('metric-wind', parseFloat(data.windSpeed));

        const alertEl = $('#metric-alerts');
        if (alertEl) alertEl.textContent = '3';
    }

    function animateValue(elementId, targetValue) {
        const el = $(`#${elementId}`);
        if (!el) return;

        const current = parseFloat(el.textContent) || 0;
        const diff = targetValue - current;
        const steps = 30;
        let step = 0;

        function tick() {
            step++;
            const progress = easeOutCubic(step / steps);
            const value = current + diff * progress;
            el.textContent = value.toFixed(value >= 10 ? 1 : 2);
            if (step < steps) requestAnimationFrame(tick);
        }

        requestAnimationFrame(tick);
    }

    function easeOutCubic(t) {
        return 1 - Math.pow(1 - t, 3);
    }

    // ======================================
    // Ice Map Canvas Rendering
    // ======================================

    function generateIceGrid(dayIndex) {
        const rows = 50;
        const cols = 80;
        const grid = [];
        const t = dayIndex / totalDays;

        for (let r = 0; r < rows; r++) {
            const row = [];
            for (let c = 0; c < cols; c++) {
                // Simulate Weddell Sea ice: higher at bottom (south), lower at top
                const latFactor = r / rows; // 0 = north, 1 = south
                const seasonal = 0.7 - 0.5 * Math.sin(t * Math.PI);
                const spatial = latFactor * 0.6 + 0.2;

                // Add some "coastline" shape
                const cx = cols * 0.5;
                const cy = rows * 0.3;
                const dist = Math.sqrt(Math.pow((c - cx) / cols, 2) + Math.pow((r - cy) / rows, 2));
                const coastMask = dist < 0.35 ? 0 : 1;

                // Ice edge â€” transition zone
                const edgeNoise = Math.sin(c * 0.3 + r * 0.2 + dayIndex * 0.05) * 0.15;
                const iceProb = Math.max(0, Math.min(1,
                    (spatial * seasonal + edgeNoise) * coastMask
                ));

                // Add perlin-like noise
                const noise = Math.sin(r * 0.5 + c * 0.3 + dayIndex * 0.02) *
                              Math.cos(r * 0.2 + c * 0.5 - dayIndex * 0.03) * 0.1;

                row.push(Math.max(0, Math.min(1, iceProb + noise)));
            }
            grid.push(row);
        }
        return { grid, rows, cols };
    }

    function iceColorMap(value) {
        // Dark ocean â†’ deep blue â†’ ice blue â†’ white
        if (value < 0.05) return [10, 14, 30];         // Dark ocean
        if (value < 0.15) return [20, 40, 80];          // Deep water
        if (value < 0.3)  return [30, 60, 120];         // Transitional
        if (value < 0.5)  return [37, 99, 235];         // Blue
        if (value < 0.7)  return [96, 165, 250];        // Light blue
        if (value < 0.85) return [147, 197, 253];       // Pale blue
        return [224, 242, 254];                          // Near-white ice
    }

    function drawIceMap(canvasId, dayIndex) {
        const canvas = $(`#${canvasId}`);
        if (!canvas) return;

        const container = canvas.parentElement;
        const dpr = window.devicePixelRatio || 1;
        canvas.width = container.clientWidth * dpr;
        canvas.height = container.clientHeight * dpr;

        const ctx = canvas.getContext('2d');
        ctx.scale(dpr, dpr);

        const width = container.clientWidth;
        const height = container.clientHeight;

        const { grid, rows, cols } = generateIceGrid(dayIndex);
        const cellW = width / cols;
        const cellH = height / rows;

        for (let r = 0; r < rows; r++) {
            for (let c = 0; c < cols; c++) {
                const [rv, g, b] = iceColorMap(grid[r][c]);
                ctx.fillStyle = `rgb(${rv}, ${g}, ${b})`;
                ctx.fillRect(c * cellW, r * cellH, cellW + 0.5, cellH + 0.5);
            }
        }

        // Draw grid lines (very subtle)
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.03)';
        ctx.lineWidth = 0.5;
        for (let r = 0; r < rows; r += 10) {
            ctx.beginPath();
            ctx.moveTo(0, r * cellH);
            ctx.lineTo(width, r * cellH);
            ctx.stroke();
        }
        for (let c = 0; c < cols; c += 10) {
            ctx.beginPath();
            ctx.moveTo(c * cellW, 0);
            ctx.lineTo(c * cellW, height);
            ctx.stroke();
        }

        // Label: "Weddell Sea"
        ctx.font = '600 13px Inter, sans-serif';
        ctx.fillStyle = 'rgba(255, 255, 255, 0.25)';
        ctx.fillText('Weddell Sea', width * 0.05, height * 0.12);

        // Compass indicator
        ctx.font = '500 10px Inter, sans-serif';
        ctx.fillStyle = 'rgba(255, 255, 255, 0.3)';
        ctx.fillText('N', width * 0.95, height * 0.06);
        ctx.fillText('S', width * 0.95, height * 0.96);
    }

    function drawFullMap() {
        drawIceMap('fullmap-canvas', currentDay);
    }

    function drawComparisonMaps() {
        drawIceMap('actual-canvas', currentDay);
        drawIceMap('predicted-canvas', Math.min(currentDay + 1, totalDays - 1));
    }

    // ======================================
    // Sparkline Chart
    // ======================================

    function drawSparkline() {
        const canvas = $('#sparkline-canvas');
        if (!canvas) return;

        const container = canvas.parentElement;
        const dpr = window.devicePixelRatio || 1;
        canvas.width = container.clientWidth * dpr;
        canvas.height = 60 * dpr;

        const ctx = canvas.getContext('2d');
        ctx.scale(dpr, dpr);

        const width = container.clientWidth;
        const height = 60;
        const padding = 4;

        // Use last 30 days of data
        const startIdx = Math.max(0, currentDay - 30);
        const data = timeSeries.slice(startIdx, currentDay + 1).map((d) => d.iceConc);

        if (data.length < 2) return;

        const min = Math.min(...data) * 0.95;
        const max = Math.max(...data) * 1.05;
        const range = max - min || 1;

        const stepX = (width - padding * 2) / (data.length - 1);

        // Gradient fill
        const gradient = ctx.createLinearGradient(0, 0, 0, height);
        gradient.addColorStop(0, 'rgba(96, 165, 250, 0.3)');
        gradient.addColorStop(1, 'rgba(96, 165, 250, 0)');

        // Draw fill
        ctx.beginPath();
        ctx.moveTo(padding, height);
        data.forEach((v, i) => {
            const x = padding + i * stepX;
            const y = height - padding - ((v - min) / range) * (height - padding * 2);
            if (i === 0) ctx.lineTo(x, y);
            else ctx.lineTo(x, y);
        });
        ctx.lineTo(padding + (data.length - 1) * stepX, height);
        ctx.closePath();
        ctx.fillStyle = gradient;
        ctx.fill();

        // Draw line
        ctx.beginPath();
        data.forEach((v, i) => {
            const x = padding + i * stepX;
            const y = height - padding - ((v - min) / range) * (height - padding * 2);
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        });
        ctx.strokeStyle = '#60a5fa';
        ctx.lineWidth = 1.5;
        ctx.stroke();

        // End dot
        const lastX = padding + (data.length - 1) * stepX;
        const lastY = height - padding - ((data[data.length - 1] - min) / range) * (height - padding * 2);
        ctx.beginPath();
        ctx.arc(lastX, lastY, 3, 0, Math.PI * 2);
        ctx.fillStyle = '#60a5fa';
        ctx.fill();
        ctx.strokeStyle = '#0a0e1a';
        ctx.lineWidth = 1.5;
        ctx.stroke();
    }

    // ======================================
    // Trend Chart
    // ======================================

    function drawTrendChart(rangeDays) {
        const canvas = $('#trend-chart-canvas');
        if (!canvas) return;

        const container = canvas.parentElement;
        const dpr = window.devicePixelRatio || 1;
        canvas.width = container.clientWidth * dpr;
        canvas.height = container.clientHeight * dpr;

        const ctx = canvas.getContext('2d');
        ctx.scale(dpr, dpr);

        const width = container.clientWidth;
        const height = container.clientHeight;
        const padL = 50, padR = 20, padT = 20, padB = 40;
        const chartW = width - padL - padR;
        const chartH = height - padT - padB;

        ctx.clearRect(0, 0, width, height);

        // Data
        const endIdx = Math.min(currentDay + 1, timeSeries.length);
        const startIdx = rangeDays === 'all' ? 0 : Math.max(0, endIdx - rangeDays);
        const data = timeSeries.slice(startIdx, endIdx);

        if (data.length < 2) return;

        const values = data.map((d) => d.iceConc);
        const min = Math.min(...values) * 0.9;
        const max = Math.max(...values) * 1.1;
        const range = max - min || 1;

        const stepX = chartW / (data.length - 1);

        // Y-axis grid lines
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.04)';
        ctx.lineWidth = 1;
        ctx.font = '400 10px "JetBrains Mono", monospace';
        ctx.fillStyle = 'rgba(148, 163, 184, 0.6)';
        ctx.textAlign = 'right';

        const numLines = 5;
        for (let i = 0; i <= numLines; i++) {
            const y = padT + (chartH / numLines) * i;
            const val = max - (range / numLines) * i;

            ctx.beginPath();
            ctx.moveTo(padL, y);
            ctx.lineTo(width - padR, y);
            ctx.stroke();

            ctx.fillText((val * 100).toFixed(0) + '%', padL - 8, y + 3);
        }

        // X-axis labels
        ctx.textAlign = 'center';
        const labelEvery = Math.max(1, Math.floor(data.length / 6));
        for (let i = 0; i < data.length; i += labelEvery) {
            const x = padL + i * stepX;
            const d = data[i].date;
            const label = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
            ctx.fillText(label, x, height - 10);
        }

        // Gradient fill under line
        const gradient = ctx.createLinearGradient(0, padT, 0, padT + chartH);
        gradient.addColorStop(0, 'rgba(96, 165, 250, 0.2)');
        gradient.addColorStop(1, 'rgba(96, 165, 250, 0)');

        ctx.beginPath();
        ctx.moveTo(padL, padT + chartH);
        values.forEach((v, i) => {
            const x = padL + i * stepX;
            const y = padT + chartH - ((v - min) / range) * chartH;
            ctx.lineTo(x, y);
        });
        ctx.lineTo(padL + (values.length - 1) * stepX, padT + chartH);
        ctx.closePath();
        ctx.fillStyle = gradient;
        ctx.fill();

        // Main line
        ctx.beginPath();
        values.forEach((v, i) => {
            const x = padL + i * stepX;
            const y = padT + chartH - ((v - min) / range) * chartH;
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        });

        const lineGrad = ctx.createLinearGradient(padL, 0, padL + chartW, 0);
        lineGrad.addColorStop(0, '#60a5fa');
        lineGrad.addColorStop(1, '#a78bfa');
        ctx.strokeStyle = lineGrad;
        ctx.lineWidth = 2;
        ctx.stroke();

        // Current position dot
        const lastIdx = values.length - 1;
        const dotX = padL + lastIdx * stepX;
        const dotY = padT + chartH - ((values[lastIdx] - min) / range) * chartH;

        ctx.beginPath();
        ctx.arc(dotX, dotY, 5, 0, Math.PI * 2);
        ctx.fillStyle = '#a78bfa';
        ctx.fill();
        ctx.strokeStyle = '#0a0e1a';
        ctx.lineWidth = 2;
        ctx.stroke();

        // Glow
        ctx.beginPath();
        ctx.arc(dotX, dotY, 10, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(167, 139, 250, 0.2)';
        ctx.fill();
    }

    // ======================================
    // Seasonal & Correlation Charts
    // ======================================

    function drawSeasonalChart() {
        const canvas = $('#seasonal-chart-canvas');
        if (!canvas) return;

        const container = canvas.parentElement;
        const dpr = window.devicePixelRatio || 1;
        canvas.width = container.clientWidth * dpr;
        canvas.height = container.clientHeight * dpr;

        const ctx = canvas.getContext('2d');
        ctx.scale(dpr, dpr);

        const width = container.clientWidth;
        const height = container.clientHeight;
        const padL = 50, padR = 20, padT = 30, padB = 40;
        const chartW = width - padL - padR;
        const chartH = height - padT - padB;

        ctx.clearRect(0, 0, width, height);

        // Draw month labels
        const months = ['Oct', 'Nov', 'Dec', 'Jan', 'Feb'];
        ctx.font = '400 10px "JetBrains Mono", monospace';
        ctx.fillStyle = 'rgba(148, 163, 184, 0.6)';
        ctx.textAlign = 'center';

        months.forEach((m, i) => {
            const x = padL + (chartW / (months.length - 1)) * i;
            ctx.fillText(m, x, height - 10);
        });

        // Y-axis
        ctx.textAlign = 'right';
        for (let i = 0; i <= 4; i++) {
            const y = padT + (chartH / 4) * i;
            const val = 100 - 25 * i;
            ctx.fillText(val + '%', padL - 8, y + 3);

            ctx.strokeStyle = 'rgba(255, 255, 255, 0.04)';
            ctx.beginPath();
            ctx.moveTo(padL, y);
            ctx.lineTo(width - padR, y);
            ctx.stroke();
        }

        // Simulated seasonal trend line
        const points = 100;
        ctx.beginPath();
        for (let i = 0; i < points; i++) {
            const t = i / (points - 1);
            const x = padL + t * chartW;
            const val = 0.7 - 0.45 * Math.sin(t * Math.PI);
            const y = padT + chartH - val * chartH;
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        }
        ctx.strokeStyle = '#22d3ee';
        ctx.lineWidth = 2;
        ctx.stroke();

        // Fill
        ctx.lineTo(padL + chartW, padT + chartH);
        ctx.lineTo(padL, padT + chartH);
        ctx.closePath();
        const gradient = ctx.createLinearGradient(0, padT, 0, padT + chartH);
        gradient.addColorStop(0, 'rgba(34, 211, 238, 0.15)');
        gradient.addColorStop(1, 'rgba(34, 211, 238, 0)');
        ctx.fillStyle = gradient;
        ctx.fill();

        // Title
        ctx.font = '500 11px Inter, sans-serif';
        ctx.fillStyle = 'rgba(148, 163, 184, 0.8)';
        ctx.textAlign = 'left';
        ctx.fillText('Ice Concentration Seasonal Pattern', padL, padT - 10);
    }

    function drawCorrelationChart() {
        const canvas = $('#correlation-chart-canvas');
        if (!canvas) return;

        const container = canvas.parentElement;
        const dpr = window.devicePixelRatio || 1;
        canvas.width = container.clientWidth * dpr;
        canvas.height = container.clientHeight * dpr;

        const ctx = canvas.getContext('2d');
        ctx.scale(dpr, dpr);

        const width = container.clientWidth;
        const height = container.clientHeight;
        const padL = 50, padR = 20, padT = 30, padB = 40;
        const chartW = width - padL - padR;
        const chartH = height - padT - padB;

        ctx.clearRect(0, 0, width, height);

        // Scatter plot: Wind Speed vs Ice Concentration
        ctx.font = '500 11px Inter, sans-serif';
        ctx.fillStyle = 'rgba(148, 163, 184, 0.8)';
        ctx.textAlign = 'left';
        ctx.fillText('Wind Speed vs Ice Concentration', padL, padT - 10);

        // Axes labels
        ctx.font = '400 10px "JetBrains Mono", monospace';
        ctx.fillStyle = 'rgba(148, 163, 184, 0.5)';
        ctx.textAlign = 'center';
        ctx.fillText('Wind Speed (m/s)', padL + chartW / 2, height - 8);

        ctx.save();
        ctx.translate(12, padT + chartH / 2);
        ctx.rotate(-Math.PI / 2);
        ctx.fillText('Ice Conc (%)', 0, 0);
        ctx.restore();

        // Grid
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.04)';
        for (let i = 0; i <= 4; i++) {
            const y = padT + (chartH / 4) * i;
            ctx.beginPath();
            ctx.moveTo(padL, y);
            ctx.lineTo(width - padR, y);
            ctx.stroke();
        }

        // Scatter dots
        const numDots = 200;
        for (let i = 0; i < numDots; i++) {
            const wind = 2 + Math.random() * 15;
            const iceFactor = 0.7 - wind * 0.025 + (Math.random() - 0.5) * 0.3;
            const ice = Math.max(0, Math.min(1, iceFactor));

            const x = padL + (wind / 20) * chartW;
            const y = padT + chartH - ice * chartH;

            ctx.beginPath();
            ctx.arc(x, y, 2.5, 0, Math.PI * 2);
            ctx.fillStyle = `rgba(167, 139, 250, ${0.3 + ice * 0.4})`;
            ctx.fill();
        }

        // Trend line (negative correlation)
        ctx.beginPath();
        ctx.moveTo(padL, padT + chartH * 0.2);
        ctx.lineTo(padL + chartW, padT + chartH * 0.75);
        ctx.strokeStyle = 'rgba(251, 191, 36, 0.5)';
        ctx.lineWidth = 1.5;
        ctx.setLineDash([6, 4]);
        ctx.stroke();
        ctx.setLineDash([]);

        // Axis tick labels
        ctx.fillStyle = 'rgba(148, 163, 184, 0.5)';
        ctx.textAlign = 'right';
        ['100', '75', '50', '25', '0'].forEach((label, i) => {
            const y = padT + (chartH / 4) * i;
            ctx.fillText(label, padL - 8, y + 3);
        });

        ctx.textAlign = 'center';
        ['0', '5', '10', '15', '20'].forEach((label, i) => {
            const x = padL + (chartW / 4) * i;
            ctx.fillText(label, x, padT + chartH + 18);
        });
    }

    // ======================================
    // Live Model Comparison & Backend API
    // ======================================

    let cachedPredictionData = null;
    let currentInferSampleIdx = 180;

    function renderGridOnCanvas(canvasId, gridData) {
        const canvas = $(`#${canvasId}`);
        if (!canvas || !gridData || !gridData.length) return;

        const container = canvas.parentElement;
        const dpr = window.devicePixelRatio || 1;
        const width = canvas.clientWidth || container.clientWidth || 420;
        const height = canvas.clientHeight || Math.round(width / 1.6) || 260;

        canvas.width = Math.round(width * dpr);
        canvas.height = Math.round(height * dpr);

        const ctx = canvas.getContext('2d');
        if (ctx.resetTransform) ctx.resetTransform();
        ctx.scale(dpr, dpr);
        ctx.clearRect(0, 0, width, height);

        const rows = gridData.length;
        const cols = gridData[0].length;
        const cellW = width / cols;
        const cellH = height / rows;

        for (let r = 0; r < rows; r++) {
            for (let c = 0; c < cols; c++) {
                const val = gridData[r][c];
                const [rv, g, b] = iceColorMap(val);
                ctx.fillStyle = `rgb(${rv}, ${g}, ${b})`;
                ctx.fillRect(c * cellW, r * cellH, cellW + 0.5, cellH + 0.5);
            }
        }
    }

    function drawComparisonMaps() {
        if (cachedPredictionData) {
            renderGridOnCanvas('actual-canvas', cachedPredictionData.actual);
            renderGridOnCanvas('predicted-canvas', cachedPredictionData.predicted);
        } else {
            fetchLiveInference(currentInferSampleIdx);
        }
    }

    async function fetchLiveInference(sampleIdx = null) {
        if (sampleIdx !== null) {
            currentInferSampleIdx = parseInt(sampleIdx, 10);
        }
        const statusEl = $('#infer-status');
        const maeEl = $('#infer-mae');
        const rmseEl = $('#infer-rmse');
        const actualSicEl = $('#infer-actual-sic');
        const predSicEl = $('#infer-pred-sic');
        const dateEl = $('#infer-target-date');
        const btn = $('#btn-run-inference');
        const selectEl = $('#infer-step-select');

        if (statusEl) statusEl.textContent = 'Running PyTorch ConvLSTM on CPU...';
        if (btn) btn.disabled = true;

        try {
            const url = `/api/predict/step?idx=${currentInferSampleIdx}`;
            const res = await fetch(url);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();

            if (data.status === 'success') {
                cachedPredictionData = data;
                currentInferSampleIdx = data.sample_index;

                renderGridOnCanvas('actual-canvas', data.actual);
                renderGridOnCanvas('predicted-canvas', data.predicted);

                if (statusEl) statusEl.textContent = 'PyTorch ConvLSTM Inference Active';
                if (dateEl && data.target_date) dateEl.textContent = data.target_date;
                if (maeEl) maeEl.textContent = `${data.metrics.mean_absolute_error.toFixed(4)} (${(data.metrics.mean_absolute_error * 100).toFixed(2)}%)`;
                if (rmseEl) rmseEl.textContent = data.metrics.root_mean_squared_error.toFixed(4);
                if (actualSicEl) actualSicEl.textContent = `${data.metrics.actual_percentage}%`;
                if (predSicEl) predSicEl.textContent = `${data.metrics.predicted_percentage}%`;

                if (selectEl) {
                    const matchOption = Array.from(selectEl.options).find(opt => parseInt(opt.value, 10) === currentInferSampleIdx);
                    if (matchOption) {
                        selectEl.value = String(currentInferSampleIdx);
                    }
                }
            } else {
                if (statusEl) statusEl.textContent = 'Inference Note: ' + (data.error || 'Ready');
            }
        } catch (err) {
            console.warn('Backend inference fetch note:', err);
            if (statusEl) statusEl.textContent = 'Local Simulation Mode Active';
            const simActual = generateIceGrid(30).grid;
            const simPred = generateIceGrid(31).grid;
            renderGridOnCanvas('actual-canvas', simActual);
            renderGridOnCanvas('predicted-canvas', simPred);
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    async function fetchBackendMetadata() {
        try {
            const res = await fetch('/api/model/info');
            if (!res.ok) return;
            const data = await res.json();

            const modelType = $('#stat-model-type');
            const trainSamples = $('#stat-train-samples');
            const testRmse = $('#stat-test-rmse');
            const testMae = $('#stat-test-mae');
            const params = $('#stat-params');
            const grid = $('#stat-grid');

            if (modelType) modelType.textContent = data.model_name || 'ConvLSTM';
            if (trainSamples) trainSamples.textContent = '841 sequences';
            if (testRmse) testRmse.textContent = `${data.best_metrics.test_rmse_original} (0.60%)`;
            if (testMae) testMae.textContent = `${data.best_metrics.test_mae_original} (0.35%)`;
            if (params) params.textContent = data.parameters ? data.parameters.toLocaleString() : '76,833';
            if (grid) grid.textContent = `${data.output_shape[0]}Ã—${data.output_shape[1]} (11 ch)`;

            const sidebarStatus = $('.sidebar__status span');
            if (sidebarStatus) sidebarStatus.textContent = 'ConvLSTM Online (CPU)';
        } catch (e) {
            console.log('Metadata fetch note:', e);
        }
    }

    async function fetchTimeSeriesData() {
        try {
            const res = await fetch('/api/timeseries');
            if (!res.ok) return;
            const json = await res.json();
            if (json.status === 'success' && json.data && json.data.length > 0) {
                timeSeries.length = 0;
                json.data.forEach((item) => {
                    timeSeries.push({
                        date: new Date(item.date),
                        iceExtent: item.iceExtent.toString(),
                        avgConcentration: item.avgConcentration.toString(),
                        windSpeed: item.windSpeed.toString(),
                        iceConc: item.iceConc,
                    });
                });
                totalDays = timeSeries.length;
                const timeRange = $('#time-range');
                if (timeRange) {
                    timeRange.max = totalDays - 1;
                }
                updateMetrics(0);
                drawIceMap('ice-map-canvas', 0);
            }
        } catch (e) {
            console.log('Timeseries fetch note:', e);
        }
    }

    // ======================================
    // Feature Table
    // ======================================

    function populateFeatureTable() {
        const tbody = $('#feature-table tbody');
        if (!tbody) return;

        tbody.innerHTML = CONFIG.features
            .map((f) => {
                const barWidth = Math.min(100, (f.std / (f.max - f.min + 0.01)) * 200);
                return `
                <tr>
                    <td>${f.label}</td>
                    <td>${f.mean.toFixed(4)}</td>
                    <td>${f.std.toFixed(4)}</td>
                    <td>${f.min.toFixed(2)}</td>
                    <td>${f.max.toFixed(2)}</td>
                    <td>
                        <div class="dist-bar-wrap">
                            <div class="dist-bar" style="width: ${barWidth}%"></div>
                        </div>
                    </td>
                </tr>
            `;
            })
            .join('');
    }

    // ======================================
    // Alert History
    // ======================================

    function populateAlertHistory() {
        const container = $('#alert-history');
        if (!container) return;

        const alerts = [
            { type: 'critical', title: 'Rapid Ice Loss Detected', desc: 'Sector NW-3: 18% decrease in 48h. Concentration dropped from 72% to 54%.', time: '2 hours ago' },
            { type: 'warning', title: 'High Wind Warning', desc: 'Wind speeds exceeding 15 m/s in sector SE-1. Ice drift rate may increase.', time: '5 hours ago' },
            { type: 'info', title: 'Model Retrained', desc: 'ConvLSTM updated with latest 7-day window. Test RMSE: 0.042 (-3% improvement).', time: '12 hours ago' },
            { type: 'warning', title: 'Unusual Current Pattern', desc: 'Southern sector showing reversed current flow. Potential upwelling event.', time: '1 day ago' },
            { type: 'critical', title: 'Ice Extent Below Threshold', desc: 'Total extent dropped below 5M kmÂ² seasonal benchmark.', time: '1 day ago' },
            { type: 'info', title: 'Data Ingestion Complete', desc: 'ERA5 and Copernicus data synced. 1,212 time steps available.', time: '2 days ago' },
            { type: 'warning', title: 'Prediction Confidence Drop', desc: 'Model confidence for 7-day forecast dropped to 68% (below 75% threshold).', time: '3 days ago' },
            { type: 'info', title: 'NSIDC Calibration Update', desc: 'Passive microwave sensor calibration applied. Data quality improved.', time: '4 days ago' },
        ];

        container.innerHTML = alerts
            .map(
                (a) => `
            <div class="alert-item alert-item--${a.type}">
                <div class="alert-item__indicator"></div>
                <div class="alert-item__content">
                    <span class="alert-item__title">${a.title}</span>
                    <span class="alert-item__desc">${a.desc}</span>
                    <span class="alert-item__time">${a.time}</span>
                </div>
            </div>
        `
            )
            .join('');
    }

    // ======================================
    // Time Slider & Playback
    // ======================================

    let currentDay = 0;
    let isPlaying = false;
    let playInterval = null;

    function initTimeSlider() {
        const slider = $('#time-range');
        const label = $('#time-label');
        const progress = $('#time-progress');
        const playBtn = $('#btn-play-pause');
        const playIcon = $('#icon-play');
        const pauseIcon = $('#icon-pause');

        if (!slider) return;

        slider.max = totalDays - 1;

        slider.addEventListener('input', () => {
            currentDay = parseInt(slider.value);
            updateTimeDisplay();
            updateMetrics(currentDay);
            drawIceMap('ice-map-canvas', currentDay);
            drawSparkline();
        });

        playBtn.addEventListener('click', () => {
            isPlaying = !isPlaying;
            playIcon.style.display = isPlaying ? 'none' : 'block';
            pauseIcon.style.display = isPlaying ? 'block' : 'none';

            if (isPlaying) {
                playInterval = setInterval(() => {
                    currentDay = (currentDay + 1) % totalDays;
                    slider.value = currentDay;
                    updateTimeDisplay();
                    updateMetrics(currentDay);
                    drawIceMap('ice-map-canvas', currentDay);
                    drawSparkline();
                }, 200);
            } else {
                clearInterval(playInterval);
            }
        });

        function updateTimeDisplay() {
            const date = timeSeries[currentDay]?.date;
            if (date && label) {
                label.textContent = date.toLocaleDateString('en-US', {
                    month: 'short',
                    day: 'numeric',
                    year: 'numeric',
                });
            }
            if (progress) {
                const pct = (currentDay / (totalDays - 1)) * 100;
                progress.style.width = pct + '%';
            }
        }

        updateTimeDisplay();
    }

    // ======================================
    // Prediction Horizon Buttons
    // ======================================

    function initPredictionButtons() {
        const buttons = $$('.horizon-btn');
        const predValue = $('#pred-value');

        buttons.forEach((btn) => {
            btn.addEventListener('click', () => {
                buttons.forEach((b) => b.classList.remove('horizon-btn--active'));
                btn.classList.add('horizon-btn--active');

                const days = parseInt(btn.dataset.days);
                const current = timeSeries[currentDay]?.iceConc || 0.5;
                const predicted = Math.max(0.1, current - days * 0.03 + (Math.random() - 0.5) * 0.05);

                if (predValue) {
                    predValue.textContent = (predicted * 8.2).toFixed(2);
                }
            });
        });
    }

    // ======================================
    // Trend Range Tabs
    // ======================================

    function initTrendTabs() {
        const tabs = $$('.tab-btn');

        tabs.forEach((tab) => {
            tab.addEventListener('click', () => {
                tabs.forEach((t) => t.classList.remove('tab-btn--active'));
                tab.classList.add('tab-btn--active');

                const range = tab.dataset.range;
                drawTrendChart(range === 'all' ? 'all' : parseInt(range));
            });
        });
    }

    // ======================================
    // Map Coordinate Tracking
    // ======================================

    function initMapInteraction() {
        const container = $('#map-container');
        const coordsEl = $('#map-coords');

        if (!container || !coordsEl) return;

        container.addEventListener('mousemove', (e) => {
            const rect = container.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const y = e.clientY - rect.top;

            // Map pixel to lat/lon (Weddell Sea approx bounds)
            const lon = -60 + (x / rect.width) * 40;  // -60 to -20
            const lat = -60 - (y / rect.height) * 20;  // -60 to -80

            coordsEl.innerHTML = `
                <span>Lat: ${lat.toFixed(2)}Â°</span>
                <span>Lon: ${lon.toFixed(2)}Â°</span>
            `;
        });

        container.addEventListener('mouseleave', () => {
            coordsEl.innerHTML = '<span>Lat: â€”</span><span>Lon: â€”</span>';
        });
    }

    // ======================================
    // Date Display
    // ======================================

    function updateHeaderDate() {
        const el = $('#current-date');
        if (el) {
            el.textContent = new Date().toLocaleDateString('en-US', {
                weekday: 'short',
                month: 'short',
                day: 'numeric',
            });
        }
    }

    // ======================================
    // Alert Filter
    // ======================================

    function initAlertFilter() {
        const select = $('#alert-filter');
        if (!select) return;

        select.addEventListener('change', () => {
            const filter = select.value;
            const items = $$('#alert-history .alert-item');

            items.forEach((item) => {
                if (filter === 'all') {
                    item.style.display = '';
                } else {
                    const isMatch = item.classList.contains(`alert-item--${filter}`);
                    item.style.display = isMatch ? '' : 'none';
                }
            });
        });
    }

    // ======================================
    // Window Resize Handler
    // ======================================

    function initResizeHandler() {
        let resizeTimer;
        window.addEventListener('resize', () => {
            clearTimeout(resizeTimer);
            resizeTimer = setTimeout(() => {
                drawIceMap('ice-map-canvas', currentDay);
                drawSparkline();
                drawTrendChart(30);

                const activeSection = $('.section--active');
                if (activeSection) {
                    const id = activeSection.id;
                    if (id === 'section-map') drawFullMap();
                    if (id === 'section-predictions') drawComparisonMaps();
                    if (id === 'section-analytics') {
                        drawSeasonalChart();
                        drawCorrelationChart();
                    }
                }
            }, 150);
        });
    }

    // ======================================
    // Initialize Everything
    // ======================================

    function init() {
        updateHeaderDate();
        initNavigation();
        initTimeSlider();
        initPredictionButtons();
        initTrendTabs();
        initMapInteraction();
        initAlertFilter();
        initResizeHandler();

        // Initial renders
        updateMetrics(0);
        populateFeatureTable();
        populateAlertHistory();
        // Live backend integration
        const runInferBtn = $('#btn-run-inference');
        if (runInferBtn) {
            runInferBtn.addEventListener('click', () => fetchLiveInference(currentInferSampleIdx));
        }
        const inferStepSelect = $('#infer-step-select');
        if (inferStepSelect) {
            inferStepSelect.addEventListener('change', (e) => {
                fetchLiveInference(parseInt(e.target.value, 10));
            });
        }
        const prevSampleBtn = $('#btn-prev-sample');
        if (prevSampleBtn) {
            prevSampleBtn.addEventListener('click', () => {
                fetchLiveInference(Math.max(0, currentInferSampleIdx - 30));
            });
        }
        const nextSampleBtn = $('#btn-next-sample');
        if (nextSampleBtn) {
            nextSampleBtn.addEventListener('click', () => {
                fetchLiveInference(Math.min(590, currentInferSampleIdx + 30));
            });
        }
        fetchBackendMetadata();
        fetchTimeSeriesData();

        // Defer canvas renders to next frame for proper sizing
        requestAnimationFrame(() => {
            drawIceMap('ice-map-canvas', 0);
            drawSparkline();
            drawTrendChart(30);
        });
    }

    // Start when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // ======================================================================
    // NAVIGATION â€” Google Maps-style A* Route Planning + Animated Ship
    // ======================================================================

    // Compact MinHeap for A* priority queue
    function NavMinHeap() { this.d = []; }
    NavMinHeap.prototype.push = function(item) {
        this.d.push(item);
        let i = this.d.length - 1;
        while (i > 0) { const p = (i-1)>>1; if (this.d[p].f <= this.d[i].f) break; const t=this.d[p]; this.d[p]=this.d[i]; this.d[i]=t; i=p; }
    };
    NavMinHeap.prototype.pop = function() {
        if (!this.d.length) return null;
        const top = this.d[0], last = this.d.pop();
        if (this.d.length) {
            this.d[0] = last;
            let i = 0;
            while (true) { let m=i,l=2*i+1,r=2*i+2; if(l<this.d.length&&this.d[l].f<this.d[m].f)m=l; if(r<this.d.length&&this.d[r].f<this.d[m].f)m=r; if(m===i)break; const t=this.d[m];this.d[m]=this.d[i];this.d[i]=t;i=m; }
        }
        return top;
    };
    NavMinHeap.prototype.empty = function() { return !this.d.length; };

    // ---- State ----
    const NAV = {
        GPS_BOUNDS: { lat_min:-78.0, lat_max:-60.0, lon_min:-60.0, lon_max:-20.0 },
        ship: { lat:-70.5, lon:-42.0, heading:0 },
        wakePoints: [],          // recent positions for trail
        plannedRoute: [],        // [{lat,lon}] from A*
        routeSegDists: [],       // cumulative km per segment
        routeTotalKm: 0,
        routeCompletedKm: 0,
        destination: null,       // {lat,lon}
        icebergs: [],
        sicGrid: null,           // 2-D SIC array [row][col]
        gridLats: null,
        gridLons: null,
        navState: 'IDLE',        // IDLE | PLANNED | NAVIGATING | ARRIVED
        navSpeed: 12,            // knots (visual)
        showIceLayer: true,
        showGrid: false,
        zoom: 1.0,
        animFrame: null,
        lastTime: 0,
        pulsePhase: 0,
        pinPulse: 0,
        icebergPollTimer: null,
        icebergInterval: 5000,
        totalDistKm: 0,
        initialized: false,
    };

    // ---- Coordinate helpers ----
    function navClamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }

    function ll2px(lat, lon, W, H) {
        const b = NAV.GPS_BOUNDS;
        return { x: (lon - b.lon_min) / (b.lon_max - b.lon_min) * W,
                 y: (b.lat_max - lat) / (b.lat_max - b.lat_min) * H };
    }

    function px2ll(x, y, W, H) {
        const b = NAV.GPS_BOUNDS;
        return { lat: b.lat_max - (y / H) * (b.lat_max - b.lat_min),
                 lon: b.lon_min + (x / W) * (b.lon_max - b.lon_min) };
    }

    // Apply zoom transform â€” returns adjusted pixel pos when zoom is active
    function applyZoom(px, shipPx, W, H) {
        if (NAV.zoom <= 1.0) return px;
        const cx = navClamp(shipPx.x, W * 0.2, W * 0.8);
        const cy = navClamp(shipPx.y, H * 0.2, H * 0.8);
        return { x: cx + (px.x - cx) * NAV.zoom, y: cy + (px.y - cy) * NAV.zoom };
    }

    // Inverse: canvas event coords â†’ logical (pre-zoom) coords
    function unzoom(ex, ey, W, H) {
        if (NAV.zoom <= 1.0) return { x: ex, y: ey };
        const sp = ll2px(NAV.ship.lat, NAV.ship.lon, W, H);
        const cx = navClamp(sp.x, W * 0.2, W * 0.8);
        const cy = navClamp(sp.y, H * 0.2, H * 0.8);
        return { x: (ex - cx) / NAV.zoom + cx, y: (ey - cy) / NAV.zoom + cy };
    }

    function navHaversine(lat1, lon1, lat2, lon2) {
        const R=6371, dLat=(lat2-lat1)*Math.PI/180, dLon=(lon2-lon1)*Math.PI/180;
        const a=Math.sin(dLat/2)**2+Math.cos(lat1*Math.PI/180)*Math.cos(lat2*Math.PI/180)*Math.sin(dLon/2)**2;
        return R*2*Math.atan2(Math.sqrt(a),Math.sqrt(1-a));
    }

    function routeDist(route) {
        let d=0; const segs=[0];
        for (let i=1;i<route.length;i++) { d+=navHaversine(route[i-1].lat,route[i-1].lon,route[i].lat,route[i].lon); segs.push(d); }
        return { total:d, segs };
    }

    // ---- A* Pathfinding on SIC grid ----
    function astarRoute(sicGrid, lats, lons, icebergs, sLat, sLon, dLat, dLon) {
        if (!sicGrid || !lats || !lons || lats.length < 2 || lons.length < 2) return null;
        const rows = lats.length, cols = lons.length;
        const latStep = (lats[rows-1] - lats[0]) / (rows-1);
        const lonStep = (lons[cols-1] - lons[0]) / (cols-1);
        const r2i = lat => navClamp(Math.round((lat - lats[0]) / latStep), 0, rows-1);
        const c2i = lon => navClamp(Math.round((lon - lons[0]) / lonStep), 0, cols-1);

        const sR=r2i(sLat), sC=c2i(sLon), dR=r2i(dLat), dC=c2i(dLon);
        if (sR===dR && sC===dC) return [{ lat:dLat, lon:dLon }];

        // Build iceberg penalty map
        const penalty = Array.from({length:rows}, () => new Float32Array(cols));
        icebergs.forEach(icb => {
            const ir=r2i(icb.lat), ic=c2i(icb.lon);
            const rad = Math.max(2, Math.ceil(icb.size_km / 25));
            for (let r=Math.max(0,ir-rad); r<=Math.min(rows-1,ir+rad); r++)
                for (let c=Math.max(0,ic-rad); c<=Math.min(cols-1,ic+rad); c++) {
                    const d=Math.sqrt((r-ir)**2+(c-ic)**2);
                    if (d<rad) penalty[r][c]=Math.max(penalty[r][c], 10*(1-d/rad));
                }
        });

        // Cell traversal cost
        const cost = (r, c) => {
            const sic = sicGrid[r][c] || 0;
            let base;
            if (sic > 0.92) return Infinity;   // Dense pack ice â€” wall
            else if (sic > 0.80) base = 25;
            else if (sic > 0.65) base = 12;
            else if (sic > 0.50) base = 6;
            else if (sic > 0.30) base = 3;
            else if (sic > 0.15) base = 2;
            else base = 1;                      // Open ocean
            return base + penalty[r][c];
        };

        const h = (r, c) => Math.sqrt((r-dR)**2+(c-dC)**2);
        const INF = 1e9;
        const G = Array.from({length:rows}, () => new Float32Array(cols).fill(INF));
        const P = Array.from({length:rows}, () => new Int32Array(cols).fill(-1));
        G[sR][sC] = 0;
        const open = new NavMinHeap();
        open.push({ r:sR, c:sC, f:h(sR,sC) });

        const DIRS=[[-1,0,1],[1,0,1],[0,-1,1],[0,1,1],[-1,-1,1.414],[-1,1,1.414],[1,-1,1.414],[1,1,1.414]];
        let found = false;

        while (!open.empty()) {
            const { r, c } = open.pop();
            if (r===dR && c===dC) { found=true; break; }
            const curG = G[r][c];
            for (const [dr, dc, diagW] of DIRS) {
                const nr=r+dr, nc=c+dc;
                if (nr<0||nr>=rows||nc<0||nc>=cols) continue;
                const cc=cost(nr,nc);
                if (!isFinite(cc)) continue;
                const ng = curG + cc * diagW;
                if (ng < G[nr][nc]) {
                    G[nr][nc] = ng;
                    P[nr][nc] = r * cols + c;
                    open.push({ r:nr, c:nc, f:ng+h(nr,nc) });
                }
            }
        }

        // If destination unreachable, find closest reachable cell
        if (!found) {
            let bestR=dR, bestC=dC, bestD=Infinity;
            for (let r=0;r<rows;r++) for (let c=0;c<cols;c++) {
                if (G[r][c]<INF) { const d=Math.sqrt((r-dR)**2+(c-dC)**2); if(d<bestD){bestD=d;bestR=r;bestC=c;} }
            }
            if (bestD===Infinity) return null;
            dR=bestR; dC=bestC;
        }

        // Reconstruct path
        const path = [];
        let cr=dR, cc2=dC;
        while (!(cr===sR && cc2===sC)) {
            path.unshift({ lat:lats[cr], lon:lons[cc2] });
            const p=P[cr][cc2]; if(p<0)break;
            cr=Math.floor(p/cols); cc2=p%cols;
        }
        path.unshift({ lat:lats[sR], lon:lons[sC] });
        return path;
    }

    // Moving-average path smoothing
    function smoothPath(route, iters=3) {
        if (route.length <= 2) return route;
        let pts = route.slice();
        for (let iter=0; iter<iters; iter++) {
            const s = [pts[0]];
            for (let i=1; i<pts.length-1; i++)
                s.push({ lat:0.25*pts[i-1].lat+0.5*pts[i].lat+0.25*pts[i+1].lat,
                          lon:0.25*pts[i-1].lon+0.5*pts[i].lon+0.25*pts[i+1].lon });
            s.push(pts[pts.length-1]);
            pts = s;
        }
        return pts.filter((_,i) => i%2===0 || i===pts.length-1);
    }

    // ---- Ice colour for nav canvas ----
    function sicColor(sic) {
        if (sic < 0.001) return null;
        if (sic < 0.15)  return `rgba(10,14,30,${(sic/0.15*0.5).toFixed(2)})`;
        if (sic < 0.5)  { const t=(sic-0.15)/0.35; return `rgb(${Math.round(10+t*27)},${Math.round(14+t*84)},${Math.round(30+t*205)})`; }
        if (sic < 0.85) { const t=(sic-0.5)/0.35;  return `rgb(${Math.round(37+t*59)},${Math.round(98+t*67)},${Math.round(235+t*15)})`; }
        const t=(sic-0.85)/0.15;
        return `rgb(${Math.round(96+t*128)},${Math.round(165+t*77)},${Math.round(250+t*4)})`;
    }

    // ---- Main render loop ----
    function renderNavCanvas(ts) {
        const canvas = document.getElementById('nav-canvas');
        const wrap   = document.getElementById('nav-map-wrap');
        if (!canvas || !wrap) return;

        const dt = (ts && NAV.lastTime) ? Math.min((ts - NAV.lastTime) / 1000, 0.12) : 0.016;
        NAV.lastTime = ts || 0;
        NAV.pulsePhase += 0.07;
        NAV.pinPulse   += 0.06;

        if (NAV.navState === 'NAVIGATING') advanceShip(dt);

        const dpr = window.devicePixelRatio || 1;
        const W = wrap.clientWidth, H = wrap.clientHeight;
        if (W < 10 || H < 10) { NAV.animFrame = requestAnimationFrame(renderNavCanvas); return; }

        if (canvas.width !== Math.round(W*dpr) || canvas.height !== Math.round(H*dpr)) {
            canvas.width = Math.round(W*dpr); canvas.height = Math.round(H*dpr);
            canvas.style.width = W+'px'; canvas.style.height = H+'px';
        }

        const ctx = canvas.getContext('2d');
        ctx.save();
        ctx.scale(dpr, dpr);
        ctx.clearRect(0, 0, W, H);

        // Apply zoom centred on ship
        const shipBase = ll2px(NAV.ship.lat, NAV.ship.lon, W, H);
        const zcx = navClamp(shipBase.x, W*0.2, W*0.8);
        const zcy = navClamp(shipBase.y, H*0.2, H*0.8);
        if (NAV.zoom !== 1.0) { ctx.translate(zcx, zcy); ctx.scale(NAV.zoom, NAV.zoom); ctx.translate(-zcx, -zcy); }

        const Z = NAV.zoom;
        const zpt = (lat, lon) => applyZoom(ll2px(lat, lon, W, H), shipBase, W, H);

        // 1. Ocean background
        ctx.fillStyle = '#060a12'; ctx.fillRect(-W, -H, W*3, H*3);

        // 2. Ice layer & 37x81 Cell Mesh Grid
        if (NAV.showIceLayer && NAV.sicGrid && NAV.gridLats && NAV.gridLons) {
            const g=NAV.sicGrid, gl=NAV.gridLats, gll=NAV.gridLons;
            const rows=g.length, cols=g[0].length;
            const cW=W/cols, cH=H/rows;
            for (let r=0;r<rows;r++) for (let c=0;c<cols;c++) {
                const sic=g[r][c]; const col=sicColor(sic); if(!col) continue;
                const pt=zpt(gl[r],gll[c]);
                ctx.fillStyle=col;
                ctx.fillRect(pt.x - cW*Z/2, pt.y - cH*Z/2, cW*Z+1, cH*Z+1);

                // Draw model cell outline if Grid is enabled
                if (NAV.showGrid) {
                    ctx.strokeStyle='rgba(148,163,184,0.18)';
                    ctx.lineWidth=0.8/Z;
                    ctx.strokeRect(pt.x - cW*Z/2, pt.y - cH*Z/2, cW*Z, cH*Z);
                }
            }
        } else if (NAV.showIceLayer) {
            const gr=ctx.createLinearGradient(0,0,0,H);
            gr.addColorStop(0,'rgba(96,165,250,0.15)'); gr.addColorStop(0.6,'rgba(30,58,138,0.08)'); gr.addColorStop(1,'rgba(10,14,30,0.02)');
            ctx.fillStyle=gr; ctx.fillRect(0,0,W,H);
        }

        // 3. High-visibility GPS grid lines & lat/lon labels
        if (NAV.showGrid) {
            const b=NAV.GPS_BOUNDS;
            
            // Major Lat/Lon Grid lines
            ctx.strokeStyle='rgba(56,189,248,0.40)';
            ctx.lineWidth=1.2/Z;
            ctx.setLineDash([4,4]);
            
            ctx.fillStyle='rgba(56,189,248,0.70)';
            ctx.font=`${Math.max(10, Math.round(11/Z))}px JetBrains Mono, monospace`;
            
            // Latitude lines (-78° to -60°)
            for (let lat=Math.ceil(b.lat_min); lat<=b.lat_max; lat+=2) {
                const p=zpt(lat, b.lon_min);
                ctx.beginPath(); ctx.moveTo(0, p.y); ctx.lineTo(W, p.y); ctx.stroke();
            }
            
            // Longitude lines (-60° to -20°)
            for (let lon=Math.ceil(b.lon_min/5)*5; lon<=b.lon_max; lon+=5) {
                const p=zpt(b.lat_min, lon);
                ctx.beginPath(); ctx.moveTo(p.x, 0); ctx.lineTo(p.x, H); ctx.stroke();
            }
            ctx.setLineDash([]);
        }

        // 4. Iceberg halos
        NAV.icebergs.forEach(icb => {
            const pt=zpt(icb.lat,icb.lon);
            const r=Math.max(6,icb.size_km*0.3)*Z;
            const gr=ctx.createRadialGradient(pt.x,pt.y,0,pt.x,pt.y,r*3.5);
            gr.addColorStop(0,'rgba(245,158,11,0.18)'); gr.addColorStop(1,'rgba(245,158,11,0)');
            ctx.fillStyle=gr; ctx.beginPath(); ctx.arc(pt.x,pt.y,r*3.5,0,Math.PI*2); ctx.fill();
        });

        // 5. Planned route
        if (NAV.plannedRoute.length > 1) {
            const seg = NAV.routeTotalKm > 0
                ? navClamp(Math.round((NAV.routeCompletedKm / NAV.routeTotalKm) * (NAV.plannedRoute.length-1)), 0, NAV.plannedRoute.length-2)
                : 0;

            // Completed portion (faint cyan)
            if (seg > 0) {
                ctx.strokeStyle='rgba(34,211,238,0.35)'; ctx.lineWidth=2*Z; ctx.setLineDash([]);
                ctx.beginPath();
                for (let i=0;i<=seg;i++) { const p=zpt(NAV.plannedRoute[i].lat,NAV.plannedRoute[i].lon); i?ctx.lineTo(p.x,p.y):ctx.moveTo(p.x,p.y); }
                ctx.stroke();
            }

            // Upcoming portion (bright green dashed)
            ctx.strokeStyle='rgba(34,197,94,0.9)'; ctx.lineWidth=2.5*Z; ctx.setLineDash([8*Z,4*Z]);
            ctx.beginPath(); let moved=false;
            for (let i=seg;i<NAV.plannedRoute.length;i++) {
                const p=zpt(NAV.plannedRoute[i].lat,NAV.plannedRoute[i].lon);
                moved ? ctx.lineTo(p.x,p.y) : (ctx.moveTo(p.x,p.y), moved=true);
            }
            ctx.stroke(); ctx.setLineDash([]);

            // Waypoint dots along upcoming route
            ctx.fillStyle='rgba(34,197,94,0.7)';
            const step=Math.max(1,Math.floor(NAV.plannedRoute.length/12));
            for (let i=seg;i<NAV.plannedRoute.length;i+=step) {
                const p=zpt(NAV.plannedRoute[i].lat,NAV.plannedRoute[i].lon);
                ctx.beginPath(); ctx.arc(p.x,p.y,3*Z,0,Math.PI*2); ctx.fill();
            }
        }

        // 6. Wake trail
        if (NAV.wakePoints.length > 1) {
            for (let i=1;i<NAV.wakePoints.length;i++) {
                const a=zpt(NAV.wakePoints[i-1].lat,NAV.wakePoints[i-1].lon);
                const b=zpt(NAV.wakePoints[i].lat,NAV.wakePoints[i].lon);
                const alpha=(0.06 + 0.4*(i/NAV.wakePoints.length)).toFixed(2);
                const width=(0.8+3.5*(i/NAV.wakePoints.length))*Z;
                ctx.strokeStyle=`rgba(34,211,238,${alpha})`; ctx.lineWidth=width;
                ctx.beginPath(); ctx.moveTo(a.x,a.y); ctx.lineTo(b.x,b.y); ctx.stroke();
            }
        }

        // 7. Icebergs (triangles)
        NAV.icebergs.forEach(icb => {
            const pt=zpt(icb.lat,icb.lon);
            const sz=Math.max(5,Math.min(14,icb.size_km*0.4))*Z;
            ctx.fillStyle='#f59e0b'; ctx.strokeStyle='rgba(251,191,36,0.7)'; ctx.lineWidth=1*Z;
            ctx.beginPath(); ctx.moveTo(pt.x,pt.y-sz); ctx.lineTo(pt.x+sz*0.75,pt.y+sz*0.55); ctx.lineTo(pt.x-sz*0.75,pt.y+sz*0.55); ctx.closePath(); ctx.fill(); ctx.stroke();
            if (icb.size_km > 10 && Z > 1.1) {
                ctx.fillStyle='rgba(251,191,36,0.85)'; ctx.font=`${8*Z}px JetBrains Mono,monospace`; ctx.textAlign='center';
                ctx.fillText(`${icb.size_km.toFixed(0)}km`, pt.x, pt.y+sz+9*Z);
            }
        });

        // 8. Destination pin (animated bounce)
        if (NAV.destination) {
            const dp=zpt(NAV.destination.lat,NAV.destination.lon);
            const bounce=Math.abs(Math.sin(NAV.pinPulse))*5*Z;
            const pulseR=(8+Math.sin(NAV.pinPulse*0.7)*4)*Z;

            // Pulse ring
            ctx.strokeStyle=`rgba(34,197,94,${(0.25+0.25*Math.sin(NAV.pinPulse)).toFixed(2)})`; ctx.lineWidth=1.5*Z;
            ctx.beginPath(); ctx.arc(dp.x, dp.y, pulseR, 0, Math.PI*2); ctx.stroke();

            // Shadow
            ctx.fillStyle='rgba(0,0,0,0.25)'; ctx.beginPath(); ctx.ellipse(dp.x, dp.y+1*Z, 6*Z, 3*Z, 0, 0, Math.PI*2); ctx.fill();

            // Pin body
            const py = dp.y - bounce - 12*Z;
            ctx.fillStyle='#22c55e'; ctx.strokeStyle='rgba(255,255,255,0.9)'; ctx.lineWidth=1.5*Z;
            ctx.beginPath(); ctx.arc(dp.x, py, 7*Z, 0, Math.PI*2); ctx.fill(); ctx.stroke();
            // Pin tail
            ctx.fillStyle='#22c55e';
            ctx.beginPath(); ctx.moveTo(dp.x, dp.y-bounce); ctx.lineTo(dp.x-5*Z, py+4*Z); ctx.lineTo(dp.x+5*Z, py+4*Z); ctx.fill();
            // Inner dot
            ctx.fillStyle='#fff'; ctx.beginPath(); ctx.arc(dp.x, py, 3*Z, 0, Math.PI*2); ctx.fill();
            // Coord label
            if (Z >= 0.8) {
                ctx.fillStyle='rgba(34,197,94,0.9)'; ctx.font=`bold ${8*Z}px JetBrains Mono,monospace`; ctx.textAlign='left';
                ctx.fillText(`${NAV.destination.lat.toFixed(2)}Â°, ${NAV.destination.lon.toFixed(2)}Â°`, dp.x+10*Z, py-4*Z);
            }
        }

        // 9. Ship marker
        const shipPx = zpt(NAV.ship.lat, NAV.ship.lon);
        const pulse = 0.5 + 0.5*Math.sin(NAV.pulsePhase);

        // Outer glow
        const gg=ctx.createRadialGradient(shipPx.x,shipPx.y,0,shipPx.x,shipPx.y,(20+pulse*9)*Z);
        gg.addColorStop(0,`rgba(34,211,238,${(0.28*pulse).toFixed(2)})`); gg.addColorStop(1,'rgba(34,211,238,0)');
        ctx.fillStyle=gg; ctx.beginPath(); ctx.arc(shipPx.x,shipPx.y,(20+pulse*9)*Z,0,Math.PI*2); ctx.fill();

        // Pulse ring
        ctx.strokeStyle=`rgba(34,211,238,${(0.5*pulse).toFixed(2)})`; ctx.lineWidth=1.5*Z;
        ctx.beginPath(); ctx.arc(shipPx.x,shipPx.y,(11+pulse*6)*Z,0,Math.PI*2); ctx.stroke();

        // Ship body â€” triangle rotated to heading
        const heading = NAV.ship.heading * Math.PI / 180;
        const sz = 9*Z;
        ctx.save();
        ctx.translate(shipPx.x, shipPx.y); ctx.rotate(heading);
        ctx.fillStyle='#22d3ee'; ctx.strokeStyle='#fff'; ctx.lineWidth=1.5*Z;
        ctx.beginPath(); ctx.moveTo(0,-sz*1.7); ctx.lineTo(sz*0.85,sz*1.0); ctx.lineTo(-sz*0.85,sz*1.0); ctx.closePath();
        ctx.fill(); ctx.stroke();
        ctx.restore();

        // GPS label
        if (Z >= 0.9) {
            ctx.fillStyle='rgba(34,211,238,0.9)'; ctx.font=`bold ${8.5*Z}px JetBrains Mono,monospace`; ctx.textAlign='left';
            ctx.fillText(`${NAV.ship.lat.toFixed(3)}Â°, ${NAV.ship.lon.toFixed(3)}Â°`, shipPx.x+13*Z, shipPx.y-5*Z);
        }

        // 10. HUD overlays (NOT zoomed â€” reset transform)
        ctx.restore();
        ctx.save();
        drawCompassRose(ctx, W-54, 54, 30);
        drawScaleBar(ctx, 18, H-26, W, H);
        ctx.restore();

        NAV.animFrame = requestAnimationFrame(renderNavCanvas);
    }

    function drawCompassRose(ctx, cx, cy, r) {
        ctx.save();
        ctx.fillStyle='rgba(6,10,18,0.82)'; ctx.strokeStyle='rgba(148,163,184,0.35)'; ctx.lineWidth=1;
        ctx.beginPath(); ctx.arc(cx,cy,r,0,Math.PI*2); ctx.fill(); ctx.stroke();

        const dirs=[['N',0,'#22d3ee',true],['E',90,'#94a3b8',false],['S',180,'#94a3b8',false],['W',270,'#94a3b8',false]];
        dirs.forEach(([lbl,ang,col,bold]) => {
            const rad=ang*Math.PI/180;
            const x1=cx+Math.sin(rad)*r*0.28, y1=cy-Math.cos(rad)*r*0.28;
            const x2=cx+Math.sin(rad)*r*0.82, y2=cy-Math.cos(rad)*r*0.82;
            ctx.strokeStyle=col; ctx.lineWidth=bold?2:1;
            ctx.beginPath(); ctx.moveTo(x1,y1); ctx.lineTo(x2,y2); ctx.stroke();
            ctx.fillStyle=col; ctx.font=`${bold?'bold ':''} ${bold?9:7}px Inter,sans-serif`;
            ctx.textAlign='center'; ctx.textBaseline='middle';
            ctx.fillText(lbl, cx+Math.sin(rad)*(r-6), cy-Math.cos(rad)*(r-6));
        });
        ctx.fillStyle='#22d3ee'; ctx.beginPath(); ctx.arc(cx,cy,3,0,Math.PI*2); ctx.fill();
        ctx.restore();
    }

    function drawScaleBar(ctx, x, y, W, H) {
        const b=NAV.GPS_BOUNDS;
        const midLat=(b.lat_min+b.lat_max)/2;
        const fullKm=navHaversine(midLat,b.lon_min,midLat,b.lon_max);
        const kmPx=fullKm/W;
        const niceKm=[50,100,200,500];
        const barKm=niceKm.find(k=>k/kmPx>55&&k/kmPx<W/3)||100;
        const barPx=barKm/kmPx;
        ctx.strokeStyle='rgba(148,163,184,0.7)'; ctx.lineWidth=1.5;
        ctx.beginPath(); ctx.moveTo(x,y); ctx.lineTo(x+barPx,y); ctx.moveTo(x,y-4); ctx.lineTo(x,y+4); ctx.moveTo(x+barPx,y-4); ctx.lineTo(x+barPx,y+4); ctx.stroke();
        ctx.fillStyle='rgba(148,163,184,0.85)'; ctx.font='8.5px JetBrains Mono,monospace'; ctx.textAlign='center'; ctx.textBaseline='bottom';
        ctx.fillText(`${barKm} km`, x+barPx/2, y-4);
    }

    // ---- Navigation animation ----
    function advanceShip(dt) {
        if (NAV.plannedRoute.length < 2 || NAV.routeTotalKm <= 0) return;
        const speedKmS = NAV.navSpeed * 1.852 / 3600 * 220; // 220Ã— time compression
        NAV.routeCompletedKm = Math.min(NAV.routeTotalKm, NAV.routeCompletedKm + speedKmS * dt);

        const segs = NAV.routeSegDists;
        let segIdx = 0;
        while (segIdx < segs.length-1 && segs[segIdx+1] <= NAV.routeCompletedKm) segIdx++;

        if (segIdx >= NAV.plannedRoute.length-1) {
            NAV.ship.lat = NAV.destination.lat; NAV.ship.lon = NAV.destination.lon;
            NAV.navState = 'ARRIVED';
            updateNavHUD(); updateRouteStatus('âœ… Arrived at destination!');
            const btn=document.getElementById('btn-start-nav');
            if (btn) { btn.textContent='âœ… Arrived'; btn.disabled=true; }
            return;
        }

        const segLen = segs[segIdx+1] - segs[segIdx];
        const t = segLen > 0 ? (NAV.routeCompletedKm - segs[segIdx]) / segLen : 0;
        const ptA = NAV.plannedRoute[segIdx], ptB = NAV.plannedRoute[segIdx+1];
        const prevLat=NAV.ship.lat, prevLon=NAV.ship.lon;

        NAV.ship.lat = ptA.lat + (ptB.lat - ptA.lat) * t;
        NAV.ship.lon = ptA.lon + (ptB.lon - ptA.lon) * t;

        // Update heading (bearing in canvas space: dLon east = right, dLat north = up)
        const dLat=NAV.ship.lat-prevLat, dLon=NAV.ship.lon-prevLon;
        if (Math.abs(dLat)>1e-7||Math.abs(dLon)>1e-7)
            NAV.ship.heading = Math.atan2(dLon, dLat) * 180 / Math.PI;

        // Wake points
        const d = navHaversine(prevLat,prevLon,NAV.ship.lat,NAV.ship.lon);
        if (d > 0.008) {
            NAV.wakePoints.push({ lat:NAV.ship.lat, lon:NAV.ship.lon });
            if (NAV.wakePoints.length > 80) NAV.wakePoints.shift();
            NAV.totalDistKm += d;
        }
        updateNavHUD();
    }

    function updateNavHUD() {
        const $ = id => document.getElementById(id);
        const set = (id, v) => { const el=$(id); if(el) el.textContent=v; };
        set('ship-lat', `${NAV.ship.lat.toFixed(4)}Â°`);
        set('ship-lon', `${NAV.ship.lon.toFixed(4)}Â°`);
        set('ship-path-count', NAV.wakePoints.length);
        set('ship-dist', `${NAV.totalDistKm.toFixed(1)} km`);

        const eta = $('nav-eta');
        if (eta) {
            if (NAV.navState==='NAVIGATING' && NAV.routeTotalKm>0) {
                const rem=NAV.routeTotalKm-NAV.routeCompletedKm;
                const sKmS=NAV.navSpeed*1.852/3600*220;
                const pct=Math.round((NAV.routeCompletedKm/NAV.routeTotalKm)*100);
                eta.textContent=`${pct}% Â· ETA ${Math.ceil(rem/sKmS)}s`;
            } else if (NAV.navState==='ARRIVED') {
                eta.textContent='âœ… Arrived!';
            } else if (NAV.navState==='PLANNED') {
                eta.textContent=`${NAV.routeTotalKm.toFixed(0)} km route`;
            } else { eta.textContent='â€”'; }
        }
    }

    function updateRouteStatus(msg) {
        const el = document.getElementById('nav-route-status');
        if (el) el.textContent = msg;
    }

    function setDestination(lat, lon) {
        const b = NAV.GPS_BOUNDS;
        lat = navClamp(lat, b.lat_min, b.lat_max);
        lon = navClamp(lon, b.lon_min, b.lon_max);
        NAV.destination = { lat, lon };
        NAV.navState = 'IDLE';
        NAV.plannedRoute = []; NAV.routeTotalKm = 0; NAV.routeCompletedKm = 0;
        const dd=document.getElementById('nav-dest-display');
        if (dd) dd.textContent=`${lat.toFixed(3)}Â°, ${lon.toFixed(3)}Â°`;
        const nb=document.getElementById('btn-start-nav');
        if (nb) { nb.disabled=true; nb.textContent='â–¶ \u00A0Auto-Navigate'; }
        updateNavHUD();
        updateRouteStatus(`ðŸ“ Destination: ${lat.toFixed(3)}Â°, ${lon.toFixed(3)}Â° â€” click Calculate Route`);
    }

    function calculateRoute() {
        if (!NAV.destination) { updateRouteStatus('âš ï¸ Double-click the map to set a destination first'); return; }
        if (!NAV.sicGrid)     { updateRouteStatus('â³ Ice data loading â€” try again in a moment'); fetchIcePredictionForNav(); return; }

        updateRouteStatus('ðŸ”„ Calculating optimal low-ice routeâ€¦');
        const btn = document.getElementById('btn-calc-route');
        if (btn) btn.disabled = true;

        setTimeout(() => {
            const raw = astarRoute(NAV.sicGrid, NAV.gridLats, NAV.gridLons, NAV.icebergs,
                                   NAV.ship.lat, NAV.ship.lon, NAV.destination.lat, NAV.destination.lon);
            if (btn) btn.disabled = false;

            if (!raw || raw.length < 2) {
                updateRouteStatus('âŒ Route blocked â€” destination may be in dense pack ice'); return;
            }
            const smoothed = smoothPath(raw, 4);
            NAV.plannedRoute = smoothed;
            const { total, segs } = routeDist(smoothed);
            NAV.routeTotalKm = total; NAV.routeSegDists = segs; NAV.routeCompletedKm = 0;
            NAV.navState = 'PLANNED';
            updateRouteStatus(`âœ… Route: ${total.toFixed(0)} km Â· ${smoothed.length} waypoints via open water`);
            updateNavHUD();
            const nb = document.getElementById('btn-start-nav');
            if (nb) { nb.disabled=false; nb.textContent='â–¶ \u00A0Auto-Navigate'; }
        }, 0);
    }

    function startNavigation() {
        if (NAV.navState !== 'PLANNED' || NAV.plannedRoute.length < 2) return;
        NAV.navState = 'NAVIGATING';
        NAV.routeCompletedKm = 0; NAV.wakePoints = [];
        updateRouteStatus('ðŸš¢ Navigating along low-ice routeâ€¦');
    }

    function stopNavigation() {
        if (NAV.navState === 'NAVIGATING') { NAV.navState='PLANNED'; updateRouteStatus('â¸ Navigation paused'); }
    }

    function setRandomDestination() {
        const b=NAV.GPS_BOUNDS;
        const lat = b.lat_min + Math.random()*(b.lat_max-b.lat_min);
        const lon = b.lon_min + Math.random()*(b.lon_max-b.lon_min);
        setDestination(lat, lon);
        updateRouteStatus(`ðŸŽ² Random destination: ${lat.toFixed(3)}Â°, ${lon.toFixed(3)}Â° â€” click Calculate Route`);
    }

    function fetchIcebergsForNav() {
        fetch('/api/icebergs').then(r=>r.json()).then(data=>{
            if (data.status==='success') {
                NAV.icebergs=data.icebergs;
                renderIcebergList(data.icebergs);
                const ts=new Date().toLocaleTimeString();
                const el=document.getElementById('nav-last-update'); if(el) el.textContent=`Updated ${ts}`;
                const cnt=document.getElementById('iceberg-count'); if(cnt) cnt.textContent=`${data.icebergs.length} tracked`;
            }
        }).catch(()=>{});
    }

    function renderIcebergList(icebergs) {
        const list=document.getElementById('iceberg-list'); if(!list) return;
        list.innerHTML = icebergs.map(icb=>`
            <div class="iceberg-item" data-lat="${icb.lat}" data-lon="${icb.lon}">
                <span class="iceberg-item__icon">â–²</span>
                <span class="iceberg-item__name">${icb.name}</span>
                <span class="iceberg-item__coords">${icb.lat.toFixed(2)}Â°, ${icb.lon.toFixed(2)}Â°</span>
                <span class="iceberg-item__size">${icb.size_km.toFixed(1)} km</span>
            </div>`).join('');
    }

    function fetchIcePredictionForNav() {
        fetch('/api/predict/latest').then(r=>r.json()).then(data=>{
            if (data.status==='success') {
                NAV.sicGrid=data.predicted; NAV.gridLats=data.latitudes; NAV.gridLons=data.longitudes;
            }
        }).catch(()=>{});
    }

    function fetchHazard(lat, lon) {
        const badge=document.getElementById('nav-hazard-badge');
        if (badge) { badge.textContent='CHECKINGâ€¦'; badge.className='nav-hazard-badge'; }
        fetch(`/api/navigation/hazards?lat=${lat.toFixed(4)}&lon=${lon.toFixed(4)}`)
            .then(r=>r.json()).then(data=>{
                if (badge) { badge.textContent=data.level||'?'; badge.className=`nav-hazard-badge nav-hazard-badge--${(data.level||'').toLowerCase()}`; }
                const s=document.getElementById('hazard-sic'); if(s) s.textContent=data.ice_concentration!=null?`${data.ice_concentration}%`:'â€”';
                const m=document.getElementById('hazard-msg'); if(m) m.textContent=data.message||'â€”';
            }).catch(()=>{});
    }

    function buildAxisLabels() {
        const axX=document.getElementById('nav-axis-x'), axY=document.getElementById('nav-axis-y');
        if (!axX||!axY) return;
        const b=NAV.GPS_BOUNDS;
        axX.innerHTML=''; for(let lon=b.lon_min;lon<=b.lon_max;lon+=10){const s=document.createElement('span');s.textContent=`${lon}Â°`;axX.appendChild(s);}
        axY.innerHTML=''; for(let lat=b.lat_max;lat>=b.lat_min;lat-=3){const s=document.createElement('span');s.textContent=`${lat}Â°`;axY.appendChild(s);}
    }

    function updateZoomDisplay() {
        const el=document.getElementById('nav-zoom-level'); if(el) el.textContent=`${NAV.zoom.toFixed(1)}Ã—`;
    }

    function initNavigationMap() {
        if (NAV.initialized) { if(!NAV.animFrame) requestAnimationFrame(renderNavCanvas); return; }
        NAV.initialized = true;

        buildAxisLabels();
        fetchIcePredictionForNav();
        fetchIcebergsForNav();
        NAV.icebergPollTimer = setInterval(fetchIcebergsForNav, NAV.icebergInterval);

        fetch('/api/ship/position').then(r=>r.json()).then(data=>{
            if (data.status==='success') { NAV.ship.lat=data.position.lat; NAV.ship.lon=data.position.lon; updateNavHUD(); fetchHazard(NAV.ship.lat,NAV.ship.lon); }
        }).catch(()=>{});

        fetchHazard(NAV.ship.lat, NAV.ship.lon);
        updateRouteStatus('ðŸ—ºï¸ Double-click the map to drop a destination pin');
        if (NAV.animFrame) cancelAnimationFrame(NAV.animFrame);
        NAV.lastTime = 0;
        requestAnimationFrame(renderNavCanvas);

        // === Event Listeners ===
        const canvas=document.getElementById('nav-canvas');
        const wrap=document.getElementById('nav-map-wrap');
        if (canvas && wrap) {

            // Double-click â†’ set destination
            canvas.addEventListener('dblclick', e => {
                const rect=wrap.getBoundingClientRect();
                const { x, y } = unzoom(e.clientX-rect.left, e.clientY-rect.top, wrap.clientWidth, wrap.clientHeight);
                const { lat, lon } = px2ll(x, y, wrap.clientWidth, wrap.clientHeight);
                const b=NAV.GPS_BOUNDS;
                if (lat>=b.lat_min&&lat<=b.lat_max&&lon>=b.lon_min&&lon<=b.lon_max) setDestination(lat, lon);
            });

            // Single click â†’ move ship (when not navigating)
            canvas.addEventListener('click', e => {
                if (NAV.navState==='NAVIGATING') return;
                const rect=wrap.getBoundingClientRect();
                const { x, y } = unzoom(e.clientX-rect.left, e.clientY-rect.top, wrap.clientWidth, wrap.clientHeight);
                const { lat, lon } = px2ll(x, y, wrap.clientWidth, wrap.clientHeight);
                const b=NAV.GPS_BOUNDS;
                if (lat<b.lat_min||lat>b.lat_max||lon<b.lon_min||lon>b.lon_max) return;
                const dLat=lat-NAV.ship.lat, dLon=lon-NAV.ship.lon;
                if(Math.abs(dLat)>1e-5||Math.abs(dLon)>1e-5) NAV.ship.heading=Math.atan2(dLon,dLat)*180/Math.PI;
                const d=navHaversine(NAV.ship.lat,NAV.ship.lon,lat,lon);
                NAV.wakePoints.push({lat:NAV.ship.lat,lon:NAV.ship.lon}); if(NAV.wakePoints.length>80)NAV.wakePoints.shift();
                NAV.totalDistKm+=d; NAV.ship.lat=lat; NAV.ship.lon=lon;
                updateNavHUD(); fetchHazard(lat,lon);
                fetch('/api/ship/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({lat,lon})}).catch(()=>{});
            });

            // Hover â†’ GPS readout + iceberg tooltip
            canvas.addEventListener('mousemove', e => {
                const rect=wrap.getBoundingClientRect();
                const { x, y } = unzoom(e.clientX-rect.left, e.clientY-rect.top, wrap.clientWidth, wrap.clientHeight);
                const { lat, lon } = px2ll(x, y, wrap.clientWidth, wrap.clientHeight);
                const ce=document.getElementById('nav-cursor-coords'), latEl=document.getElementById('nav-cursor-lat'), lonEl=document.getElementById('nav-cursor-lon');
                if(ce) ce.style.display='block';
                if(latEl) latEl.textContent=`${lat.toFixed(3)}Â°S`;
                if(lonEl) lonEl.textContent=`${Math.abs(lon).toFixed(3)}Â°W`;

                let nearby=null, minD=Infinity;
                NAV.icebergs.forEach(icb=>{
                    const shipPx=ll2px(NAV.ship.lat,NAV.ship.lon,wrap.clientWidth,wrap.clientHeight);
                    const pt=applyZoom(ll2px(icb.lat,icb.lon,wrap.clientWidth,wrap.clientHeight),shipPx,wrap.clientWidth,wrap.clientHeight);
                    const d=Math.hypot(pt.x-(e.clientX-rect.left), pt.y-(e.clientY-rect.top));
                    if(d<36&&d<minD){minD=d;nearby=icb;}
                });
                const tip=document.getElementById('nav-tooltip');
                if(tip){
                    if(nearby){
                        tip.style.display='block'; tip.style.left=(e.clientX-rect.left+14)+'px'; tip.style.top=(e.clientY-rect.top-10)+'px';
                        tip.innerHTML=`<strong style="color:#f59e0b;">â–² ${nearby.name}</strong><br>${nearby.lat.toFixed(3)}Â°, ${nearby.lon.toFixed(3)}Â°<br>Size: ${nearby.size_km.toFixed(1)} km<br>Heading: ${nearby.heading.toFixed(0)}Â°`;
                    } else { tip.style.display='none'; }
                }
            });
            canvas.addEventListener('mouseleave', ()=>{
                const ce=document.getElementById('nav-cursor-coords'); if(ce) ce.style.display='none';
                const tip=document.getElementById('nav-tooltip'); if(tip) tip.style.display='none';
            });

            // Scroll â†’ zoom
            canvas.addEventListener('wheel', e=>{
                e.preventDefault();
                NAV.zoom = navClamp(NAV.zoom + (e.deltaY>0?-0.15:0.15), 0.8, 4.0);
                updateZoomDisplay();
            }, { passive:false });
        }

        // Buttons
        const on = (id, fn) => { const el=document.getElementById(id); if(el) el.addEventListener('click',fn); };

        on('btn-calc-route', calculateRoute);

        on('btn-start-nav', () => {
            const btn=document.getElementById('btn-start-nav');
            if (NAV.navState==='NAVIGATING') {
                stopNavigation(); if(btn) btn.textContent='â–¶ \u00A0Auto-Navigate';
            } else {
                startNavigation(); if(btn) btn.textContent='â¸ \u00A0Pause';
            }
        });

        on('btn-random-dest', () => { setRandomDestination(); });

        on('btn-nav-clear-path', () => {
            NAV.wakePoints=[]; NAV.totalDistKm=0; NAV.plannedRoute=[]; NAV.destination=null;
            NAV.navState='IDLE'; NAV.routeTotalKm=0; NAV.routeCompletedKm=0;
            const dd=document.getElementById('nav-dest-display'); if(dd) dd.textContent='â€”';
            const nb=document.getElementById('btn-start-nav'); if(nb){nb.disabled=true;nb.textContent='â–¶ \u00A0Auto-Navigate';}
            updateNavHUD(); updateRouteStatus('ðŸ—ºï¸ Double-click the map to drop a destination pin');
        });

        on('btn-check-hazard', () => fetchHazard(NAV.ship.lat, NAV.ship.lon));
        on('btn-refresh-icebergs', fetchIcebergsForNav);

        on('btn-zoom-in',  () => { NAV.zoom=navClamp(NAV.zoom+0.3,0.8,4.0); updateZoomDisplay(); });
        on('btn-zoom-out', () => { NAV.zoom=navClamp(NAV.zoom-0.3,0.8,4.0); updateZoomDisplay(); });

        // Speed slider
        const spd=document.getElementById('nav-speed'), spdV=document.getElementById('nav-speed-val');
        if(spd&&spdV) spd.addEventListener('input',()=>{ NAV.navSpeed=parseInt(spd.value,10); spdV.textContent=NAV.navSpeed+' kt'; });

        // Iceberg interval slider
        const itvl=document.getElementById('nav-iceberg-interval'), itvlV=document.getElementById('nav-interval-val');
        if(itvl&&itvlV) itvl.addEventListener('input',()=>{
            const sec=parseInt(itvl.value,10); itvlV.textContent=sec+'s'; NAV.icebergInterval=sec*1000;
            if(NAV.icebergPollTimer) clearInterval(NAV.icebergPollTimer);
            NAV.icebergPollTimer=setInterval(fetchIcebergsForNav, NAV.icebergInterval);
        });

        // Ice / grid toggles
        const iceChk=document.getElementById('nav-show-ice');  if(iceChk)  iceChk.addEventListener('change',()=>NAV.showIceLayer=iceChk.checked);
        const gridChk=document.getElementById('nav-show-grid'); if(gridChk) gridChk.addEventListener('change',()=>NAV.showGrid=gridChk.checked);

        // Manual position
        on('btn-nav-set-pos', () => {
            const mi=document.getElementById('nav-manual-inputs');
            if(mi) mi.style.display=mi.style.display==='flex'?'none':'flex';
        });
        on('btn-nav-go', () => {
            const latI=parseFloat(document.getElementById('manual-lat')?.value);
            const lonI=parseFloat(document.getElementById('manual-lon')?.value);
            const b=NAV.GPS_BOUNDS;
            if(!isNaN(latI)&&!isNaN(lonI)){
                NAV.ship.lat=navClamp(latI,b.lat_min,b.lat_max);
                NAV.ship.lon=navClamp(lonI,b.lon_min,b.lon_max);
                updateNavHUD();
                const mi=document.getElementById('nav-manual-inputs'); if(mi) mi.style.display='none';
            }
        });

        window.addEventListener('resize', buildAxisLabels);
    }

    // ==========================================================
    //  Custom Scenario Tester
    // ==========================================================
    (function initCustomTester() {

        // --- Preset scenario definitions ---
        const PRESETS = {
            open_ocean: {
                siconc: 0.03, u10: 3.0, v10: 1.5, uo: 0.12, vo: 0.06,
                wind_speed: 5.5, current_speed: 0.13, wind_dir: 0.5, current_dir: 0.3,
                day_of_year: 30   // Late January (Antarctic summer)
            },
            dense_pack: {
                siconc: 0.92, u10: -2.0, v10: -1.0, uo: -0.01, vo: -0.005,
                wind_speed: 2.5, current_speed: 0.01, wind_dir: -1.2, current_dir: 0.1,
                day_of_year: 212  // Early August (Antarctic winter peak)
            },
            storm: {
                siconc: 0.45, u10: -22.0, v10: 18.0, uo: 0.9, vo: -0.7,
                wind_speed: 31.0, current_speed: 1.15, wind_dir: 2.6, current_dir: -1.5,
                day_of_year: 319  // Mid-November
            },
            marginal: {
                siconc: 0.38, u10: 6.5, v10: -3.2, uo: 0.04, vo: 0.02,
                wind_speed: 9.0, current_speed: 0.045, wind_dir: -0.5, current_dir: 0.4,
                day_of_year: 290  // Mid-October
            }
        };

        const SEASON_LABELS = [
            [1,  19,  'Early January (Antarctic Summer)'],
            [20,  50, 'Late January'],
            [51,  80, 'February'],
            [81, 110, 'March (Early Autumn)'],
            [111,141, 'April'],
            [142,172, 'May'],
            [173,203, 'June (Antarctic Winter)'],
            [204,233, 'July (Peak Ice Season)'],
            [234,264, 'August'],
            [265,294, 'September (Spring onset)'],
            [295,324, 'October–November (Antarctic Spring)'],
            [325,365, 'December (Antarctic Summer)'],
        ];
        function getSeasonLabel(doy) {
            for (const [s, e, label] of SEASON_LABELS) if (doy >= s && doy <= e) return label;
            return 'Antarctic Season';
        }

        // --- Paired slider ↔ number inputs ---
        const PAIRS = [
            ['fi-siconc','fi-siconc-range'], ['fi-u10','fi-u10-range'],
            ['fi-v10','fi-v10-range'], ['fi-wind-speed','fi-wind-speed-range'],
            ['fi-wind-dir','fi-wind-dir-range'], ['fi-uo','fi-uo-range'],
            ['fi-vo','fi-vo-range'], ['fi-current-speed','fi-current-speed-range'],
            ['fi-current-dir','fi-current-dir-range'], ['fi-doy','fi-doy-range'],
        ];
        PAIRS.forEach(([numId, rangeId]) => {
            const num = document.getElementById(numId);
            const rng = document.getElementById(rangeId);
            if (!num || !rng) return;
            num.addEventListener('input', () => { rng.value = num.value; if (numId === 'fi-doy') updateDoyDisplay(); });
            rng.addEventListener('input', () => { num.value = rng.value; if (rangeId === 'fi-doy-range') updateDoyDisplay(); });
        });

        // --- DOY season label and derived sin/cos ---
        function updateDoyDisplay() {
            const doy = parseInt(document.getElementById('fi-doy')?.value || 319, 10);
            const sin = Math.sin(2 * Math.PI * doy / 365.25);
            const cos = Math.cos(2 * Math.PI * doy / 365.25);
            const sinEl = document.getElementById('fi-day-sin');
            const cosEl = document.getElementById('fi-day-cos');
            const lbl   = document.getElementById('fi-season-label');
            if (sinEl) sinEl.textContent = (sin >= 0 ? '+' : '') + sin.toFixed(3);
            if (cosEl) cosEl.textContent = (cos >= 0 ? '+' : '') + cos.toFixed(3);
            if (lbl)   lbl.textContent   = getSeasonLabel(doy);
        }
        updateDoyDisplay();

        // --- Preset button handler ---
        document.querySelectorAll('.preset-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const key = btn.dataset.preset;
                const vals = PRESETS[key];
                if (!vals) return;

                // Highlight active preset
                document.querySelectorAll('.preset-btn').forEach(b => b.classList.remove('btn--active-preset'));
                btn.classList.add('btn--active-preset');

                const setVal = (numId, rangeId, v) => {
                    const num = document.getElementById(numId);
                    const rng = document.getElementById(rangeId);
                    if (num) num.value = v;
                    if (rng) rng.value = v;
                };
                setVal('fi-siconc','fi-siconc-range', vals.siconc);
                setVal('fi-u10','fi-u10-range', vals.u10);
                setVal('fi-v10','fi-v10-range', vals.v10);
                setVal('fi-wind-speed','fi-wind-speed-range', vals.wind_speed);
                setVal('fi-wind-dir','fi-wind-dir-range', vals.wind_dir);
                setVal('fi-uo','fi-uo-range', vals.uo);
                setVal('fi-vo','fi-vo-range', vals.vo);
                setVal('fi-current-speed','fi-current-speed-range', vals.current_speed);
                setVal('fi-current-dir','fi-current-dir-range', vals.current_dir);
                setVal('fi-doy','fi-doy-range', vals.day_of_year);
                updateDoyDisplay();
            });
        });

        // --- Collect current form values ---
        function getFeatureValues() {
            const g = id => parseFloat(document.getElementById(id)?.value || 0);
            return {
                siconc:        g('fi-siconc'),
                u10:           g('fi-u10'),
                v10:           g('fi-v10'),
                uo:            g('fi-uo'),
                vo:            g('fi-vo'),
                wind_speed:    g('fi-wind-speed'),
                current_speed: g('fi-current-speed'),
                wind_dir:      g('fi-wind-dir'),
                current_dir:   g('fi-current-dir'),
                day_of_year:   g('fi-doy'),
            };
        }

        // --- SIC color function (matches navigation map palette) ---
        function sicColor(sic) {
            if (sic <= 0) return null;
            const t = Math.min(sic, 1.0);
            if (t < 0.15) return `rgba(10,14,30,${(t/0.15).toFixed(2)})`;
            if (t < 0.50) { const f=(t-0.15)/0.35; return `rgb(${Math.round(30+f*66)},${Math.round(58+f*107)},${Math.round(138+f*112)})`; }
            if (t < 0.85) { const f=(t-0.50)/0.35; return `rgb(${Math.round(96+f*128)},${Math.round(165+f*77)},${Math.round(250+f*4)})`; }
            const f=(t-0.85)/0.15; return `rgb(${Math.round(224+f*31)},${Math.round(242+f*13)},254)`;
        }

        // --- Render predicted SIC grid on canvas at full display resolution ---
        function renderCustomCanvas(predicted, rows, cols) {
            const canvas = document.getElementById('custom-pred-canvas');
            if (!canvas || !predicted || !predicted.length) return;

            // Render at the actual display size so each grid cell is a real rectangle
            const dpr = window.devicePixelRatio || 1;
            const container = canvas.parentElement;
            const displayW = container ? Math.max(container.clientWidth, 200) : 600;
            const displayH = Math.round(displayW * rows / cols);

            canvas.width  = Math.round(displayW * dpr);
            canvas.height = Math.round(displayH * dpr);
            canvas.style.width  = displayW + 'px';
            canvas.style.height = displayH + 'px';

            const ctx = canvas.getContext('2d');
            ctx.save();
            ctx.scale(dpr, dpr);

            // Dark ocean background
            ctx.fillStyle = '#060a12';
            ctx.fillRect(0, 0, displayW, displayH);

            const cellW = displayW / cols;
            const cellH = displayH / rows;

            for (let r = 0; r < rows; r++) {
                for (let c = 0; c < cols; c++) {
                    const sic = predicted[r][c] || 0;
                    const color = sicColor(sic);
                    if (!color) continue;
                    ctx.fillStyle = color;
                    // +0.5px overlap prevents hairline gaps between cells
                    ctx.fillRect(
                        Math.floor(c * cellW),
                        Math.floor(r * cellH),
                        Math.ceil(cellW) + 1,
                        Math.ceil(cellH) + 1
                    );
                }
            }

            ctx.restore();

            // Hide the "click Run" placeholder
            const placeholder = document.getElementById('custom-canvas-placeholder');
            if (placeholder) placeholder.style.display = 'none';
        }


        // --- Update result stats and breakdown bars ---
        function updateCustomResults(metrics) {
            const dangerColors = { CLEAR: '#34D399', ADVISORY: '#38BDF8', CAUTION: '#F59E0B', DANGER: '#EF4444' };
            const setEl = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };

            setEl('cr-mean-sic', metrics.mean_sic_pct.toFixed(1) + '%');
            setEl('cr-extent', metrics.ice_extent_mkm2 + ' M km²');
            setEl('cr-danger', metrics.danger_level);

            const dangerEl = document.getElementById('cr-danger');
            if (dangerEl) dangerEl.style.color = dangerColors[metrics.danger_level] || '#38BDF8';

            // Animate breakdown bars
            const setBar = (barId, pctId, frac) => {
                const pct = Math.round(frac * 100);
                const bar = document.getElementById(barId);
                const label = document.getElementById(pctId);
                if (bar) bar.style.width = pct + '%';
                if (label) label.textContent = pct + '%';
            };
            setBar('br-open', 'br-open-pct', metrics.open_ocean_frac);
            setBar('br-marginal', 'br-marginal-pct', metrics.marginal_frac);
            setBar('br-pack', 'br-pack-pct', metrics.pack_ice_frac);
            setBar('br-dense', 'br-dense-pct', metrics.dense_pack_frac);

            // Enable stats and breakdown panels
            const statsEl = document.getElementById('custom-result-stats');
            const bkEl    = document.getElementById('custom-breakdown');
            if (statsEl) { statsEl.style.opacity = '1'; statsEl.style.pointerEvents = 'auto'; }
            if (bkEl)    { bkEl.style.opacity    = '1'; bkEl.style.pointerEvents    = 'auto'; }
        }

        // --- Run prediction ---
        async function runCustomPrediction() {
            const btn = document.getElementById('btn-run-custom');
            if (!btn) return;
            btn.disabled = true;
            btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18" style="animation:spin 1s linear infinite"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg> Running…';

            const features = getFeatureValues();

            try {
                const resp = await fetch('/api/predict/custom', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(features),
                });
                if (!resp.ok) throw new Error('Server error ' + resp.status);
                const data = await resp.json();

                if (data.status === 'success' && data.predicted) {
                    const shape = data.shape || [37, 81];
                    renderCustomCanvas(data.predicted, shape[0], shape[1]);
                    updateCustomResults(data.metrics);
                } else {
                    alert('Prediction error: ' + (data.error || data.message || 'Unknown error'));
                }
            } catch (e) {
                alert('Failed to call prediction API: ' + e.message);
            } finally {
                btn.disabled = false;
                btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18"><polygon points="5 3 19 12 5 21 5 3"/></svg> Run Custom Prediction';
            }
        }

        const runBtn = document.getElementById('btn-run-custom');
        if (runBtn) runBtn.addEventListener('click', runCustomPrediction);

    })();

})();

