/* ============================================================
   Letterboxd Wrapped — Flow Controller
   Manages Landing → Loading → Wrapped → Dashboard transitions
   ============================================================ */

const GENRE_COLORS = [
    '#00d4aa', '#6366f1', '#f97316', '#ec4899', '#38bdf8',
    '#84cc16', '#f59e0b', '#14b8a6', '#818cf8', '#fb7185',
];

// ── Page navigation ──────────────────────────────────────────
function showPage(id) {
    document.querySelectorAll('.page').forEach(p => {
        p.classList.remove('active');
    });
    const page = document.getElementById(id);
    if (page) {
        page.classList.add('active');
        if (id === 'wrapped') {
            setTimeout(initWrappedObserver, 100);
        }
        if (id === 'dashboard') {
            window.scrollTo(0, 0);
        }
    }
}

// ── Landing — ZIP Upload ─────────────────────────────────────
let selectedFile = null;

document.addEventListener('DOMContentLoaded', () => {
    const btnDiscover = document.getElementById('btn-discover');
    const fileInput = document.getElementById('file-input');
    const dropZone = document.getElementById('drop-zone');
    const fileNameEl = document.getElementById('file-name');

    // File input change
    if (fileInput) {
        fileInput.addEventListener('change', (e) => {
            if (e.target.files.length > 0) {
                selectedFile = e.target.files[0];
                fileNameEl.textContent = `✓ ${selectedFile.name}`;
                btnDiscover.disabled = false;
            }
        });
    }

    // Drag & drop
    if (dropZone) {
        ['dragenter', 'dragover'].forEach(evt => {
            dropZone.addEventListener(evt, (e) => {
                e.preventDefault();
                dropZone.classList.add('drag-over');
            });
        });
        ['dragleave', 'drop'].forEach(evt => {
            dropZone.addEventListener(evt, (e) => {
                e.preventDefault();
                dropZone.classList.remove('drag-over');
            });
        });
        dropZone.addEventListener('drop', (e) => {
            const files = e.dataTransfer.files;
            if (files.length > 0 && files[0].name.endsWith('.zip')) {
                selectedFile = files[0];
                fileNameEl.textContent = `✓ ${selectedFile.name}`;
                btnDiscover.disabled = false;
            }
        });
        // Click drop zone to open file picker
        dropZone.addEventListener('click', (e) => {
            if (e.target.tagName !== 'INPUT') {
                fileInput.click();
            }
        });
    }

    // Discover button
    if (btnDiscover) {
        btnDiscover.addEventListener('click', () => {
            if (!selectedFile) return;
            startWrappedFlow(selectedFile);
        });
    }

    // Dashboard back button
    const btnBack = document.getElementById('btn-back-wrapped');
    if (btnBack) {
        btnBack.addEventListener('click', () => {
            showPage('wrapped');
            setTimeout(initWrappedObserver, 100);
        });
    }

    // Download PNG
    const btnDownload = document.getElementById('btn-download');
    if (btnDownload) {
        btnDownload.addEventListener('click', downloadSummaryPNG);
    }

    // Go to dashboard
    const btnDashboard = document.getElementById('btn-to-dashboard');
    if (btnDashboard) {
        btnDashboard.addEventListener('click', () => {
            showPage('dashboard');
            if (typeof loadDashboard === 'function') {
                loadDashboard();
            }
        });
    }

    // DEBUG BYPASS for visual testing
    if (window.location.search.includes('debug=1')) {
        showPage('loading');
        fetch('/api/wrapped')
            .then(r => r.json())
            .then(data => {
                setTimeout(() => {
                    renderWrappedCards(data);
                    showPage('wrapped');
                }, 1000);
            });
    }
});

// ── ZIP Upload Flow ──────────────────────────────────────────
function startWrappedFlow(file) {
    showPage('loading');

    const msgEl = document.getElementById('loading-message');
    const barEl = document.getElementById('loading-bar');
    const stepEl = document.getElementById('loading-step');

    msgEl.textContent = '📁 ZIP dosyası yükleniyor...';
    barEl.style.width = '20%';
    stepEl.textContent = 'Adım 1/3';

    const formData = new FormData();
    formData.append('file', file);

    fetch('/api/upload-zip', { method: 'POST', body: formData })
        .then(res => res.json())
        .then(data => {
            if (data.error) {
                msgEl.textContent = '❌ ' + data.error;
                barEl.style.width = '100%';
                barEl.style.background = '#ef4444';
                return;
            }

            // Simulate progress steps for visual effect
            msgEl.textContent = '⭐ Analiz tamamlandı!';
            barEl.style.width = '70%';
            stepEl.textContent = 'Adım 2/3';

            setTimeout(() => {
                msgEl.textContent = '✨ Wrapped hazır!';
                barEl.style.width = '100%';
                stepEl.textContent = 'Adım 3/3';

                setTimeout(() => {
                    renderWrappedCards(data.wrapped);
                    showPage('wrapped');
                }, 600);
            }, 500);
        })
        .catch(err => {
            msgEl.textContent = '❌ Bağlantı hatası: ' + err.message;
            barEl.style.background = '#ef4444';
        });
}

// ── Render Wrapped Cards ─────────────────────────────────────
function renderWrappedCards(w) {
    // Card 1: Opening
    document.getElementById('w-total-hours').textContent = Math.round(w.total_hours).toLocaleString();
    document.getElementById('w-total-movies').textContent = w.total_movies;
    document.getElementById('w-total-days').textContent = w.total_days;
    document.getElementById('w-avg-rating').textContent = w.avg_rating ? w.avg_rating + '★' : '—';

    // Card 2: Director
    if (w.top_director) {
        document.getElementById('w-dir-name').textContent = w.top_director.name;
        document.getElementById('w-dir-my').textContent = w.top_director.my_avg + '★';
        document.getElementById('w-dir-people').textContent = w.top_director.people_avg ? w.top_director.people_avg + '★' : '—';
        document.getElementById('w-dir-detail').textContent =
            `${w.top_director.movie_count} filmini izledin · Bayesian Ort: ${w.top_director.bayesian_avg}★`;
    }

    // Card 3: Genres
    const genreContainer = document.getElementById('w-genre-bars');
    genreContainer.innerHTML = '';
    if (w.top_genres && w.top_genres.length > 0) {
        const maxCount = w.top_genres[0].count;
        w.top_genres.forEach((g, i) => {
            const pct = Math.round((g.count / maxCount) * 100);
            const row = document.createElement('div');
            row.className = 'genre-bar-row';
            row.innerHTML = `
                <span class="genre-bar-label">${g.genre}</span>
                <div class="genre-bar-track">
                    <div class="genre-bar-fill" style="background: ${GENRE_COLORS[i % GENRE_COLORS.length]};" data-width="${pct}%">
                        ${g.count}
                    </div>
                </div>
            `;
            genreContainer.appendChild(row);
        });
    }

    // Card 4: Controversial
    const lovedContainer = document.getElementById('w-loved');
    const hatedContainer = document.getElementById('w-hated');
    lovedContainer.innerHTML = '';
    hatedContainer.innerHTML = '';

    (w.loved_by_you || []).forEach(m => {
        const posterHtml = m.poster ? `<img src="${m.poster}" class="controversial-poster" alt="poster" />` : '';
        lovedContainer.innerHTML += `
            <div class="controversial-movie">
                ${posterHtml}
                <div class="controversial-info">
                    <div class="controversial-title">${m.title}</div>
                    <div class="controversial-diff">
                        Sen: <span class="you">${m.my_rating}★</span> · Toplum: <span class="them">${m.average_rating}★</span>
                        · Fark: +${m.diff}
                    </div>
                </div>
            </div>
        `;
    });

    (w.hated_by_you || []).forEach(m => {
        const posterHtml = m.poster ? `<img src="${m.poster}" class="controversial-poster" alt="poster" />` : '';
        hatedContainer.innerHTML += `
            <div class="controversial-movie">
                ${posterHtml}
                <div class="controversial-info">
                    <div class="controversial-title">${m.title}</div>
                    <div class="controversial-diff">
                        Sen: <span class="you">${m.my_rating}★</span> · Toplum: <span class="them">${m.average_rating}★</span>
                        · Fark: ${m.diff}
                    </div>
                </div>
            </div>
        `;
    });

    // Card 5: Trend chart
    if (w.taste_evolution && w.taste_evolution.years) {
        const ctx = document.getElementById('w-chart-trend');
        if (ctx) {
            new Chart(ctx.getContext('2d'), {
                type: 'line',
                data: {
                    labels: w.taste_evolution.years,
                    datasets: [{
                        label: 'Ort. Puan',
                        data: w.taste_evolution.avg_ratings,
                        borderColor: '#00d4aa',
                        backgroundColor: 'rgba(0, 212, 170, 0.1)',
                        fill: true,
                        tension: 0.3,
                        pointRadius: 5,
                        pointHoverRadius: 8,
                        borderWidth: 3,
                    }]
                },
                options: {
                    responsive: true,
                    plugins: { legend: { display: false } },
                    scales: {
                        y: { min: 2.5, max: 5, ticks: { color: '#94a3b8' }, grid: { color: 'rgba(255,255,255,0.05)' } },
                        x: { ticks: { color: '#94a3b8' }, grid: { color: 'rgba(255,255,255,0.05)' } },
                    }
                }
            });
        }
    }

    // Card 6: Binge
    if (w.busiest_month) {
        document.getElementById('w-binge-month').textContent = w.busiest_month.month;
        document.getElementById('w-binge-count').textContent = `${w.busiest_month.count} film izledin!`;
    }
    if (w.binge_months && w.binge_months.months) {
        const ctx = document.getElementById('w-chart-binge');
        if (ctx) {
            new Chart(ctx.getContext('2d'), {
                type: 'bar',
                data: {
                    labels: w.binge_months.months,
                    datasets: [{
                        data: w.binge_months.counts,
                        backgroundColor: w.binge_months.counts.map((c, i) => {
                            const max = Math.max(...w.binge_months.counts);
                            return c === max ? '#00d4aa' : 'rgba(99, 102, 241, 0.4)';
                        }),
                        borderRadius: 4,
                    }]
                },
                options: {
                    responsive: true,
                    plugins: { legend: { display: false } },
                    scales: {
                        y: { ticks: { color: '#94a3b8' }, grid: { color: 'rgba(255,255,255,0.05)' } },
                        x: { ticks: { color: '#94a3b8', font: { size: 10 } }, grid: { display: false } },
                    }
                }
            });
        }
    }

    // Card 7: Summary / shareable
    document.getElementById('s-movies').textContent = w.total_movies;
    document.getElementById('s-hours').textContent = Math.round(w.total_hours).toLocaleString();
    document.getElementById('s-directors').textContent = w.unique_directors;
    document.getElementById('s-avg').textContent = w.avg_rating ? w.avg_rating + '★' : '—';
    document.getElementById('s-fav-dir').textContent = w.top_director ? w.top_director.name : '—';

    // Update dashboard badge
    const badge = document.getElementById('status-badge');
    if (badge) badge.textContent = `${w.total_movies} Movies`;
}

// ── IntersectionObserver for scroll animations ───────────────
function initWrappedObserver() {
    const cards = document.querySelectorAll('.wrapped-card');

    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('visible');

                // Animate genre bars when genre card becomes visible
                if (entry.target.id === 'card-genres') {
                    setTimeout(() => {
                        entry.target.querySelectorAll('.genre-bar-fill').forEach(bar => {
                            bar.style.width = bar.dataset.width;
                        });
                    }, 300);
                }
            }
        });
    }, {
        threshold: 0.15,
        rootMargin: '0px 0px -50px 0px',
    });

    cards.forEach(card => {
        card.classList.remove('visible');
        observer.observe(card);
    });
}

// ── PNG Export ────────────────────────────────────────────────
function downloadSummaryPNG() {
    const card = document.getElementById('shareable-card');
    if (!card || typeof html2canvas === 'undefined') return;

    html2canvas(card, {
        backgroundColor: '#0f172a',
        scale: 2,
        useCORS: true,
    }).then(canvas => {
        const link = document.createElement('a');
        link.download = 'letterboxd-wrapped.png';
        link.href = canvas.toDataURL('image/png');
        link.click();
    });
}
