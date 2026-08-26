/* ============================================================
   Letterboxd Analysis – Dashboard App (ES6)
   Fetches data from FastAPI backend and renders Chart.js charts
   ============================================================ */

// ── Chart.js global defaults ──────────────────────────────────
Chart.defaults.color = '#94a3b8';
Chart.defaults.borderColor = 'rgba(255,255,255,0.06)';
Chart.defaults.font.family = "'Inter', system-ui, sans-serif";
Chart.defaults.font.size = 12;
Chart.defaults.plugins.legend.labels.usePointStyle = true;

// ── Palette ───────────────────────────────────────────────────
const COLORS = {
    accent:   '#00d4aa',
    purple:   '#6366f1',
    orange:   '#f97316',
    pink:     '#ec4899',
    sky:      '#38bdf8',
    lime:     '#84cc16',
    red:      '#ef4444',
    amber:    '#f59e0b',
    teal:     '#14b8a6',
    indigo:   '#818cf8',
    rose:     '#fb7185',
    cyan:     '#22d3ee',
};
const PALETTE = Object.values(COLORS);

// ── DOM refs ──────────────────────────────────────────────────
const $  = (sel) => document.querySelector(sel);
const statusBadge = $('#status-badge');

// ── Chart instances (so we can destroy before re-creating) ───
let charts = {};
function getOrCreate(id, config) {
    if (charts[id]) charts[id].destroy();
    const canvas = document.getElementById(id);
    if (!canvas) return null;
    charts[id] = new Chart(canvas.getContext('2d'), config);
    return charts[id];
}

// ── API helpers ───────────────────────────────────────────────
async function api(path, opts = {}) {
    const res = await fetch(path, opts);
    return res.json();
}

// ── Set badge ─────────────────────────────────────────────────
function setBadge(text, cls) {
    if (!statusBadge) return;
    statusBadge.textContent = text;
    statusBadge.className = `badge badge-${cls}`;
}

let dashboardLoaded = false;

// ── Load all dashboard data ───────────────────────────────────
async function loadDashboard() {
    try {
        const [stats, models, recs] = await Promise.all([
            api('/api/stats'),
            api('/api/models'),
            api('/api/recommendations'),
        ]);

        if (stats.error) {
            console.warn('Stats error:', stats.error);
            return;
        }

        renderSummary(stats.summary);
        
        // Basic renderers
        renderRatingDist(stats.rating_distribution);
        renderRuntimeCounts(stats.runtime_counts);
        renderRuntimeAvg(stats.runtime_avg_rating);
        renderCorrelationMatrix(stats.correlation_matrix);
        renderGenres(stats.genre_distribution);
        renderDirectors(stats.director_analysis);
        renderChiSquare(stats.chi_square);
        renderPearson(stats.correlation_my_vs_avg);
        renderDirCorr(stats.director_correlation);
        
        // New renderers
        renderBingeHabits(stats.binge_habits);
        renderBacklog(stats.backlog);
        renderDiversity(stats.diversity);
        renderBayesianDirectors(stats.bayesian_directors);
        renderBayesianActors(stats.bayesian_actors);
        renderControversial(stats.controversial);
        renderCollabs(stats.network);
        renderTemporal(stats.temporal_evolution);

        if (!models.error) {
            renderModels(models);
            renderFeatureImportance(models.feature_importance);
        }
        if (!recs.error) {
            renderRecommendations(recs);
        }
    } catch (e) {
        console.error('Dashboard load error:', e);
    }
}

// ── Renderers ─────────────────────────────────────────────────

function renderSummary(s) {
    $('#stat-total').textContent    = s.total_movies ?? '–';
    $('#stat-avg-my').textContent   = s.avg_my_rating != null ? `${s.avg_my_rating}★` : '–';
    $('#stat-avg-user').textContent = s.avg_user_rating != null ? `${s.avg_user_rating}★` : '–';
    $('#stat-genres').textContent   = s.unique_genres ?? '–';
    $('#stat-directors').textContent = s.unique_directors ?? '–';
}

function renderRatingDist(data) {
    getOrCreate('chart-rating-dist', {
        type: 'bar',
        data: {
            labels: data.ratings.map(r => `${r}★`),
            datasets: [{
                label: 'Movies',
                data: data.counts,
                backgroundColor: data.ratings.map((_, i) => {
                    const t = i / (data.ratings.length - 1);
                    return `hsl(${160 + t * 60}, 70%, ${45 + t * 15}%)`;
                }),
                borderRadius: 6,
                borderSkipped: false,
            }],
        },
        options: {
            responsive: true,
            plugins: { legend: { display: false } },
            scales: { y: { beginAtZero: true, ticks: { stepSize: 1 } } },
        },
    });
}

function renderRuntimeCounts(data) {
    getOrCreate('chart-runtime-counts', {
        type: 'bar',
        data: {
            labels: data.labels,
            datasets: [{
                label: 'Movies',
                data: data.values,
                backgroundColor: [COLORS.accent, COLORS.purple, COLORS.orange, COLORS.pink],
                borderRadius: 6,
                borderSkipped: false,
            }],
        },
        options: {
            responsive: true,
            plugins: { legend: { display: false } },
            scales: { y: { beginAtZero: true } },
        },
    });
}

function renderRuntimeAvg(data) {
    getOrCreate('chart-runtime-avg', {
        type: 'bar',
        data: {
            labels: data.labels,
            datasets: [{
                label: 'Avg My Rating',
                data: data.values,
                backgroundColor: [COLORS.sky, COLORS.lime, COLORS.amber, COLORS.rose],
                borderRadius: 6,
                borderSkipped: false,
            }],
        },
        options: {
            responsive: true,
            plugins: { legend: { display: false } },
            scales: { y: { beginAtZero: true, max: 5 } },
        },
    });
}

function renderCorrelationMatrix(data) {
    const n = data.columns.length;
    const points = [];
    const colors = [];
    for (let i = 0; i < n; i++) {
        for (let j = 0; j < n; j++) {
            const val = data.matrix[i][j];
            points.push({ x: j, y: i, v: val });
            const absV = Math.abs(val);
            if (val >= 0) {
                colors.push(`rgba(0, 212, 170, ${0.2 + absV * 0.8})`);
            } else {
                colors.push(`rgba(239, 68, 68, ${0.2 + absV * 0.8})`);
            }
        }
    }

    // Render as a simple table since Chart.js doesn't have a native heatmap
    const container = $('#chart-card-corr');
    const canvas = container.querySelector('canvas');
    canvas.style.display = 'none';

    // Remove old table if any
    const old = container.querySelector('.corr-table');
    if (old) old.remove();

    let html = '<table class="corr-table model-table"><thead><tr><th></th>';
    data.columns.forEach(c => html += `<th>${c}</th>`);
    html += '</tr></thead><tbody>';
    for (let i = 0; i < n; i++) {
        html += `<tr><td style="font-weight:600;color:var(--text-muted)">${data.columns[i]}</td>`;
        for (let j = 0; j < n; j++) {
            const val = data.matrix[i][j];
            const absV = Math.abs(val);
            const bg = val >= 0
                ? `rgba(0,212,170,${0.1 + absV * 0.4})`
                : `rgba(239,68,68,${0.1 + absV * 0.4})`;
            html += `<td style="background:${bg};text-align:center;font-weight:600">${val}</td>`;
        }
        html += '</tr>';
    }
    html += '</tbody></table>';
    container.insertAdjacentHTML('beforeend', html);
}

function renderGenres(data) {
    const genres = data.genres.slice(0, 15);
    getOrCreate('chart-genres', {
        type: 'bar',
        data: {
            labels: genres.map(g => g.genre),
            datasets: [
                {
                    label: 'My Avg',
                    data: genres.map(g => g.avg_my_rating),
                    backgroundColor: COLORS.accent + '99',
                    borderRadius: 4,
                    borderSkipped: false,
                },
                {
                    label: 'Users Avg',
                    data: genres.map(g => g.avg_user_rating),
                    backgroundColor: COLORS.purple + '99',
                    borderRadius: 4,
                    borderSkipped: false,
                },
            ],
        },
        options: {
            responsive: true,
            indexAxis: 'y',
            scales: { x: { min: 0, max: 5 } },
            plugins: { legend: { position: 'top' } },
        },
    });
}

function renderDirectors(data) {
    const dirs = data.directors;
    if (!dirs || dirs.length === 0) return;

    getOrCreate('chart-directors', {
        type: 'scatter',
        data: {
            datasets: [{
                label: 'Directors',
                data: dirs.map(d => ({
                    x: d.my_avg,
                    y: d.people_avg,
                    director: d.director,
                    count: d.movie_count,
                })),
                backgroundColor: dirs.map((_, i) => PALETTE[i % PALETTE.length] + 'cc'),
                pointRadius: dirs.map(d => 5 + d.movie_count * 1.5),
                pointHoverRadius: dirs.map(d => 8 + d.movie_count * 1.5),
            }],
        },
        options: {
            responsive: true,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: (ctx) => {
                            const p = ctx.raw;
                            return `${p.director} (${p.count} films) — You: ${p.x}★  Users: ${p.y}★`;
                        },
                    },
                },
            },
            scales: {
                x: { title: { display: true, text: 'My Average Rating' }, min: 2.5, max: 5 },
                y: { title: { display: true, text: 'Users Average Rating' }, min: 2.5, max: 5 },
            },
        },
        plugins: [{
            id: 'diagonalLine',
            afterDraw(chart) {
                const { ctx, scales: { x, y } } = chart;
                const low = Math.max(x.min, y.min);
                const high = Math.min(x.max, y.max);
                ctx.save();
                ctx.strokeStyle = 'rgba(255,255,255,0.15)';
                ctx.lineWidth = 1;
                ctx.setLineDash([6, 4]);
                ctx.beginPath();
                ctx.moveTo(x.getPixelForValue(low), y.getPixelForValue(low));
                ctx.lineTo(x.getPixelForValue(high), y.getPixelForValue(high));
                ctx.stroke();
                ctx.restore();
            },
        }],
    });
}

function renderTemporal(data) {
    if (!data || data.error) return;
    getOrCreate('chart-temporal', {
        type: 'line',
        data: {
            labels: data.years,
            datasets: [
                {
                    label: 'Avg Rating',
                    data: data.avg_ratings,
                    borderColor: COLORS.accent,
                    backgroundColor: 'rgba(0, 212, 170, 0.1)',
                    pointRadius: 4,
                    pointHoverRadius: 6,
                    borderWidth: 2,
                    fill: true,
                    tension: 0.3
                }
            ],
        },
        options: {
            responsive: true,
            scales: {
                y: { title: { display: true, text: 'Avg Rating' }, min: 2.5, max: 5 },
            },
        },
    });
}

function renderBingeHabits(data) {
    if (!data || data.error) return;
    getOrCreate('chart-binge', {
        type: 'bar',
        data: {
            labels: data.months,
            datasets: [{
                label: 'Movies Watched',
                data: data.counts,
                backgroundColor: COLORS.indigo + 'aa',
                borderRadius: 4
            }]
        },
        options: {
            responsive: true,
            plugins: { legend: { display: false } }
        }
    });
}

function renderBacklog(data) {
    if (!data || data.error) return;
    getOrCreate('chart-backlog', {
        type: 'doughnut',
        data: {
            labels: data.categories,
            datasets: [{
                data: data.counts,
                backgroundColor: [COLORS.accent, COLORS.sky, COLORS.purple, COLORS.orange, COLORS.rose],
                borderWidth: 1,
                borderColor: '#1e293b'
            }]
        },
        options: {
            responsive: true,
            plugins: {
                legend: { position: 'right' }
            },
            cutout: '60%'
        }
    });
}

function renderDiversity(data) {
    if (!data) return;
    
    // Countries
    if (data.top_countries) {
        getOrCreate('chart-countries', {
            type: 'pie',
            data: {
                labels: data.top_countries.map(d => d.name),
                datasets: [{
                    data: data.top_countries.map(d => d.count),
                    backgroundColor: PALETTE.slice(0, data.top_countries.length).map(c => c + 'aa')
                }]
            },
            options: { responsive: true, plugins: { legend: { position: 'bottom', labels: { boxWidth: 12 } } } }
        });
    }
    
    // Languages
    if (data.top_languages) {
        getOrCreate('chart-languages', {
            type: 'pie',
            data: {
                labels: data.top_languages.map(d => d.name),
                datasets: [{
                    data: data.top_languages.map(d => d.count),
                    backgroundColor: PALETTE.slice(5, 5 + data.top_languages.length).map(c => c + 'aa')
                }]
            },
            options: { responsive: true, plugins: { legend: { position: 'bottom', labels: { boxWidth: 12 } } } }
        });
    }
}

function renderBayesianDirectors(data) {
    if (!data || !data.directors) return;
    const dirs = data.directors.slice(0, 15);
    getOrCreate('chart-bayesian-dir', {
        type: 'bar',
        data: {
            labels: dirs.map(d => d.director),
            datasets: [{
                label: 'Bayesian Avg',
                data: dirs.map(d => d.bayesian_avg),
                backgroundColor: COLORS.teal + 'aa',
                borderRadius: 4
            }]
        },
        options: {
            responsive: true,
            indexAxis: 'y',
            scales: { x: { min: 2.5, max: 5 } }
        }
    });
}

function renderBayesianActors(data) {
    if (!data || !data.actors) return;
    const acts = data.actors.slice(0, 15);
    getOrCreate('chart-bayesian-act', {
        type: 'bar',
        data: {
            labels: acts.map(d => d.actor),
            datasets: [{
                label: 'Bayesian Avg',
                data: acts.map(d => d.bayesian_avg),
                backgroundColor: COLORS.rose + 'aa',
                borderRadius: 4
            }]
        },
        options: {
            responsive: true,
            indexAxis: 'y',
            scales: { x: { min: 2.5, max: 5 } }
        }
    });
}

function renderControversial(data) {
    if (!data || !data.controversial) return;
    const items = data.controversial;
    getOrCreate('chart-controversial', {
        type: 'bar',
        data: {
            labels: items.map(d => d.title),
            datasets: [{
                label: 'Rating Difference (You - Users)',
                data: items.map(d => d.diff),
                backgroundColor: items.map(d => d.diff > 0 ? COLORS.accent + 'aa' : COLORS.red + 'aa'),
                borderRadius: 4
            }]
        },
        options: {
            responsive: true,
            plugins: { legend: { display: false } }
        }
    });
}

function renderCollabs(data) {
    if (!data || !data.top_collaborations) return;
    const items = data.top_collaborations;
    getOrCreate('chart-collabs', {
        type: 'bar',
        data: {
            labels: items.map(d => d.pair),
            datasets: [{
                label: 'Movies Watched',
                data: items.map(d => d.count),
                backgroundColor: COLORS.amber + 'aa',
                borderRadius: 4
            }]
        },
        options: {
            responsive: true,
            indexAxis: 'y'
        }
    });
}

// ── Statistical detail renderers ──────────────────────────────

function statRow(key, val, cls = '') {
    return `<div class="stat-row"><span class="stat-key">${key}</span><span class="stat-val ${cls}">${val}</span></div>`;
}

function renderChiSquare(data) {
    const el = $('#chi-square-content');
    el.innerHTML =
        statRow('χ² Value', data.chi2) +
        statRow('p-value', data.p_value) +
        statRow('Degrees of Freedom', data.dof) +
        statRow('Significant (α=0.05)', data.significant ? 'Yes ✓' : 'No ✗',
            data.significant ? 'positive' : 'neutral');
}

function renderPearson(data) {
    const el = $('#pearson-content');
    if (data.r == null) { el.innerHTML = '<p class="placeholder-text">Not enough data</p>'; return; }
    el.innerHTML =
        statRow('r (Pearson)', data.r) +
        statRow('p-value', data.p) +
        statRow('n', data.n) +
        statRow('Significant', data.significant ? 'Yes ✓' : 'No ✗',
            data.significant ? 'positive' : 'neutral');
}

function renderDirCorr(data) {
    const el = $('#dir-corr-content');
    if (data.r == null) { el.innerHTML = '<p class="placeholder-text">Not enough data</p>'; return; }
    el.innerHTML =
        statRow('r (Pearson)', data.r) +
        statRow('p-value', data.p) +
        statRow('n (directors)', data.n) +
        statRow('Significant', data.significant ? 'Yes ✓' : 'No ✗',
            data.significant ? 'positive' : 'neutral');
}

// ── ML Renderers ──────────────────────────────────────────────

function renderModels(data) {
    const el = $('#ml-models-content');
    let html = '<table class="model-table"><thead><tr><th>Model</th><th>MSE</th><th>R²</th></tr></thead><tbody>';
    for (const m of data.models) {
        const best = m.model === data.best_model ? ' class="best"' : '';
        html += `<tr${best}><td>${m.model}</td><td>${m.mse}</td><td>${m.r2}</td></tr>`;
    }
    html += '</tbody></table>';
    html += `<p style="margin-top:0.8rem;color:var(--text-muted);font-size:0.82rem">
        Best: <strong style="color:var(--accent)">${data.best_model}</strong> &nbsp;|&nbsp;
        Train: ${data.n_train} &nbsp;|&nbsp; Test: ${data.n_test}</p>`;
    el.innerHTML = html;
}

function renderFeatureImportance(features) {
    if (!features || features.length === 0) return;
    const top = features.slice(0, 10);
    getOrCreate('chart-feature-importance', {
        type: 'bar',
        data: {
            labels: top.map(f => f.feature.replace('genre_', '🎭 ').replace('_', ' ')),
            datasets: [{
                label: 'Importance',
                data: top.map(f => f.importance),
                backgroundColor: top.map((_, i) => PALETTE[i % PALETTE.length] + 'bb'),
                borderRadius: 6,
                borderSkipped: false,
            }],
        },
        options: {
            responsive: true,
            indexAxis: 'y',
            plugins: { legend: { display: false } },
            scales: { x: { beginAtZero: true } },
        },
    });
}

function renderRecommendations(data) {
    const el = $('#recommendations-content');
    if (!data.recommendations || data.recommendations.length === 0) {
        el.innerHTML = '<p class="placeholder-text">No recommendations available.</p>';
        return;
    }
    let html = '<div class="rec-list">';
    for (const r of data.recommendations) {
        html += `
        <div class="rec-item">
            <span class="rec-rating">${r.predicted_rating}★</span>
            <div class="rec-info">
                <div class="rec-director">${r.director}</div>
                <div class="rec-reason">${r.reason}</div>
            </div>
        </div>`;
    }
    html += '</div>';
    html += `<p style="margin-top:0.8rem;color:var(--text-muted);font-size:0.78rem">
        Model: ${data.model_used}</p>`;
    el.innerHTML = html;
}

// ── Scatter chart (My Rating vs Avg Rating) ───────────────────
// This is rendered after stats come in, using raw data from temporal_trend
// which contains individual movie ratings and titles.

function renderScatterFromStats(stats) {
    // We need per-movie my_rating vs average_rating
    // The stats endpoint doesn't return raw scatter data, so we'll use
    // correlation info and temporal data to create a scatter proxy.
    // For a proper scatter, we'd need a dedicated endpoint.
    // For now, render a placeholder message.
    const canvas = document.getElementById('chart-scatter');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = '#64748b';
    ctx.font = '14px Inter';
    ctx.textAlign = 'center';
    ctx.fillText('Scatter plot requires raw data – run scrape to populate', canvas.width / 2, canvas.height / 2);
}

// ── Init ──────────────────────────────────────────────────────
// Dashboard loads on-demand when user clicks "Dashboard'a Geç"
// No auto-init needed; Wrapped flow handles everything.
