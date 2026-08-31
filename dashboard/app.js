/* ============================================================
   Orchestration: upload → quiz → result → dashboard
   ============================================================ */

const KEY = 'lbxw';
const $ = (id) => document.getElementById(id);

let session = null;
let wrapped = null;
let quizResult = null;
let charts = {};

/* ── helpers ─────────────────────────────────────────────────── */

function show(page) {
    document.querySelectorAll('.page').forEach(p => p.classList.toggle('active', p.id === page));
    window.scrollTo({ top: 0 });
}

function toast(message, isError = false) {
    const el = $('toast');
    el.textContent = message;
    el.classList.toggle('err', isError);
    el.classList.add('show');
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.remove('show'), 4200);
}

async function api(path) {
    const res = await fetch(path);
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
    return body;
}

const save = () => {
    try {
        localStorage.setItem(KEY, JSON.stringify({ session, quizResult }));
    } catch { /* private mode — the session just won't survive a reload */ }
};

const clear = () => { try { localStorage.removeItem(KEY); } catch {} };

/* ── landing ─────────────────────────────────────────────────── */

async function paintPosterWall() {
    try {
        const { posters } = await api('/api/posters?n=48');
        if (!posters?.length) return;
        $('poster-wall').innerHTML = posters
            .map(url => `<img src="${url}" alt="" loading="lazy" />`).join('');
    } catch { /* decorative only */ }
}

function wireLanding() {
    const drop = $('drop');
    const input = $('file');
    let chosen = null;

    const pick = (file) => {
        if (!file) return;
        if (!file.name.toLowerCase().endsWith('.zip')) {
            toast('Lütfen Letterboxd export ZIP dosyasını seç', true);
            return;
        }
        chosen = file;
        $('file-name').textContent = file.name;
        drop.classList.add('filled');
        $('go').disabled = false;
    };

    input.addEventListener('change', e => pick(e.target.files[0]));

    ['dragenter', 'dragover'].forEach(type =>
        drop.addEventListener(type, e => { e.preventDefault(); drop.classList.add('over'); }));
    ['dragleave', 'drop'].forEach(type =>
        drop.addEventListener(type, e => { e.preventDefault(); drop.classList.remove('over'); }));
    drop.addEventListener('drop', e => pick(e.dataTransfer.files[0]));

    $('go').addEventListener('click', () => chosen && upload(chosen));
}

/* ── upload & quiz run ───────────────────────────────────────── */

async function upload(file) {
    $('go').disabled = true;
    $('go').textContent = 'Yükleniyor…';

    try {
        const form = new FormData();
        form.append('file', file);
        const res = await fetch('/api/upload-zip', { method: 'POST', body: form });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Yükleme başarısız');

        session = data.session_id;
        quizResult = null;
        save();
        startQuiz(data.scrape);
    } catch (err) {
        toast(err.message, true);
        $('go').disabled = false;
        $('go').textContent = 'Başla';
    }
}

async function startQuiz(scrapeState) {
    show('play');
    Quiz.reset(finishQuiz);

    // Phase 1 is answerable straight away — no scraping needed.
    try {
        const { questions } = await api(`/api/quiz?session=${session}&phase=instant`);
        Quiz.add(questions);
    } catch (err) {
        toast(err.message, true);
        return;
    }

    const pending = scrapeState?.total ?? 0;
    if (pending > 0) {
        Quiz.expect(6);                       // roughly what phase 2 adds
        setScrapePill(`${pending} film taranıyor`, false);
        followScrape();
    } else {
        setScrapePill('hazır', true);
        loadFullPhase();
    }
}

function setScrapePill(text, done) {
    $('scrape-text').textContent = text;
    $('scrape-pill').classList.toggle('done', done);
}

function followScrape() {
    const stream = new EventSource(`/api/progress?session=${session}`);

    stream.onmessage = (event) => {
        const state = JSON.parse(event.data);
        if (state.status === 'running') {
            const left = Math.max(state.total - state.done, 0);
            setScrapePill(`${left} film kaldı`, false);
        } else {
            stream.close();
            setScrapePill(state.status === 'done' ? 'hazır' : 'bazı filmler eksik', true);
            loadFullPhase();
            refreshAfterScrape();
        }
    };
    stream.onerror = () => { stream.close(); setScrapePill('bağlantı koptu', true); loadFullPhase(); };
}

async function loadFullPhase() {
    try {
        const { questions } = await api(`/api/quiz?session=${session}&phase=full`);
        Quiz.add(questions);
    } catch {
        Quiz.expect(0);                       // let the quiz end gracefully
    }
}

/* ── result ──────────────────────────────────────────────────── */

async function finishQuiz(score, total, skipped = false) {
    quizResult = { score, total, skipped };
    save();
    try {
        wrapped = await api(`/api/wrapped?session=${session}`);
    } catch (err) {
        toast(err.message, true);
        return;
    }
    renderResult();
    show('wrapped');
    loadAnalysis();          // ready by the time they scroll down to it
}

/**
 * Re-pull everything once the background scrape lands. Skipping the quiz can
 * put a reader on the results while films are still being fetched, and a
 * half-scraped summary would quietly under-report hours, directors and genres.
 */
async function refreshAfterScrape() {
    if (!session || !document.getElementById('wrapped').classList.contains('active')) return;
    try {
        wrapped = await api(`/api/wrapped?session=${session}`);
        renderResult();
        loadAnalysis.done = false;
        await loadAnalysis();
        toast('Film bilgileri tamamlandı, analiz güncellendi');
    } catch { /* keep what's on screen */ }
}

function renderResult() {
    const { score, total, skipped } = quizResult || { score: 0, total: 0 };
    const pct = total ? Math.round((score / total) * 100) : 0;

    // Someone who skipped never claimed to know anything — scoring them 0/0
    // and calling it a failure would be both wrong and rude.
    const noScore = skipped && total === 0;
    $('r-score').hidden = noScore;
    $('quiz-row').hidden = noScore;

    if (noScore) {
        $('r-verdict').textContent = 'İşte kütüphanen.';
        $('r-note').textContent = 'Testi atladın — istersen aşağıda detaylı analiz var.';
    } else {
        $('r-score').innerHTML = `${score}<small>/${total}</small>`;
        $('r-verdict').textContent =
            pct >= 80 ? 'Kendini iyi tanıyorsun.' :
            pct >= 55 ? 'Fena değil — ama birkaç sürpriz vardı.' :
            pct >= 30 ? 'Zevkin seni yanıltıyor.' :
                        'Kendi kütüphaneni tanımıyorsun.';
        $('r-note').textContent = skipped
            ? `Yarıda bıraktın — ${total} sorudan ${score} doğru.`
            : `${total} sorudan ${score} doğru.`;
    }

    const fav = wrapped.top_director;
    const most = wrapped.most_watched_director;
    const actor = wrapped.most_watched_actor;

    $('s-films').textContent = wrapped.total_movies ?? '—';
    $('s-hours').textContent = Math.round(wrapped.total_hours ?? 0);
    $('s-dirs').textContent = wrapped.unique_directors ?? '—';
    $('s-avg').textContent = wrapped.avg_rating ?? '—';
    $('s-fav').textContent = fav ? `${fav.name} (${fav.my_avg})` : '—';
    $('s-most').textContent = most ? `${most.name} · ${most.movie_count} film` : '—';
    $('s-actor').textContent = actor ? `${actor.name} · ${actor.movie_count} film` : '—';
    $('s-quiz').textContent = `${score}/${total}`;
}

/* ── dashboard ───────────────────────────────────────────────── */

const AXIS = { color: '#8E8EA3', font: { family: 'Inter', size: 11 } };
const GRID = { color: 'rgba(255,255,255,0.06)' };

function chart(id, config) {
    charts[id]?.destroy();
    const el = $(id);
    if (!el) return;
    // Spread the caller's options first: the merged `plugins` and `scales`
    // below must win, or a config that sets either one would drop the
    // defaults (and re-enable the legend with an "undefined" label).
    charts[id] = new Chart(el, {
        ...config,
        options: {
            ...config.options,
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false }, ...(config.options?.plugins || {}) },
            scales: config.options?.scales ?? {
                x: { ticks: AXIS, grid: GRID }, y: { ticks: AXIS, grid: GRID },
            },
        },
    });
}

const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/** Ranked list; `max` draws a proportional bar under each row. */
function ranking(el, rows, max = null) {
    const node = $(el);
    if (!node) return;
    if (!rows.length) {
        node.innerHTML = '<p style="color:var(--muted);font-size:.9rem">Yeterli veri yok</p>';
        return;
    }
    const peak = max ?? (Math.max(...rows.map(r => Number(r.bar) || 0)) || 1);
    node.innerHTML = rows.map((r, i) => `
        <div class="rank-row">
            <span class="n">${i + 1}</span>
            <span class="name">${esc(r.name)}${r.sub ? `<small>${esc(r.sub)}</small>` : ''}</span>
            <span class="val">${esc(r.value)}</span>
            ${r.bar != null ? `<span class="track"><i style="width:${(r.bar / peak) * 100}%"></i></span>` : ''}
        </div>`).join('');
}

/**
 * Fill the analysis chapters. They live on the same page as the summary, so
 * this only loads data — the reader reaches them by scrolling.
 */
async function loadAnalysis() {
    if (loadAnalysis.done) return;

    let stats, explain;
    try {
        [stats, explain] = await Promise.all([
            api(`/api/stats?session=${session}`),
            api(`/api/explain?session=${session}`).catch(() => null),
        ]);
    } catch (err) { toast(err.message, true); return; }

    paintDashBg();
    chapterOverview(stats);
    chapterRatings(stats);
    chapterPeople(stats);
    chapterWhat(stats);
    chapterWhen(stats);
    chapterWhere(stats);
    chapterDrivers(explain);
    loadAnalysis.done = true;
}

async function paintDashBg() {
    if ($('dash-bg').childElementCount) return;
    try {
        const { posters } = await api('/api/posters?n=60');
        $('dash-bg').innerHTML = (posters || []).map(u => `<img src="${u}" alt="" loading="lazy" />`).join('');
    } catch { /* decorative */ }
}

/* 01 — Künye */
function chapterOverview(stats) {
    const s = stats.summary;
    const hours = wrapped ? Math.round(wrapped.total_hours || 0) : null;
    const cells = [
        ['Film', s.total_movies],
        ['Puanladığın', s.rated_movies],
        ...(hours ? [['Saat', hours.toLocaleString('tr')], ['Tam gün', Math.round(hours / 24)]] : []),
        ['Yönetmen', s.unique_directors],
        ['Tür', s.unique_genres],
        ['Ülke', s.unique_countries],
        ['Dil', s.unique_languages],
    ].filter(([, value]) => value != null);

    $('big-stats').innerHTML = cells
        .map(([l, v]) => `<div class="big-stat"><span class="v">${esc(v)}</span><span class="l">${esc(l)}</span></div>`)
        .join('');
    $('ch1-sub').textContent =
        `${s.total_movies} film, ${s.rated_movies} tanesi puanlanmış. Ortalaman ${s.avg_my_rating ?? '—'}.`;
}

/* 02 — Nasıl puanlıyorsun */
function chapterRatings(stats) {
    const dist = stats.rating_distribution;
    const peak = dist.counts.indexOf(Math.max(...dist.counts));
    $('ch2-sub').textContent =
        `En sık ${dist.ratings[peak]} veriyorsun — ${dist.counts[peak]} film bu puanda.`;

    chart('c-ratings', {
        type: 'bar',
        data: { labels: dist.ratings, datasets: [{ data: dist.counts, backgroundColor: '#8B5CF6', borderRadius: 6 }] },
    });

    const points = stats.scatter?.points || [];
    chart('c-scatter', {
        type: 'scatter',
        data: {
            datasets: [{
                data: points,
                backgroundColor: 'rgba(139,92,246,0.45)',
                pointRadius: 3.5, pointHoverRadius: 6,
            }],
        },
        options: {
            plugins: {
                tooltip: {
                    callbacks: {
                        label: (ctx) => `${ctx.raw.t} — sen ${ctx.raw.y}, kitle ${ctx.raw.x}`,
                    },
                },
            },
            scales: {
                x: { title: { display: true, text: 'Kitlenin puanı', color: '#8E8EA3' },
                     ticks: AXIS, grid: GRID, min: 0, max: 5 },
                y: { title: { display: true, text: 'Senin puanın', color: '#8E8EA3' },
                     ticks: AXIS, grid: GRID, min: 0, max: 5 },
            },
        },
    });

    const c = stats.correlation_my_vs_avg;
    const cmp = stats.crowd_comparison || {};
    $('corr-note').textContent = c?.r != null
        ? `Kitleyle uyumun r = ${c.r} (${c.n} film). Senin ortalaman ${cmp.yours}, kitlenin ${cmp.crowd}.`
        : '';

    $('r-contro').innerHTML = (stats.controversial?.controversial || []).slice(0, 8).map(m => `
        <div class="film-card">
            <img src="${esc(m.poster || '')}" alt="" loading="lazy" />
            <div class="t">${esc(m.title)}</div>
            <div class="r"><b>${m.my_rating}</b> <s>/ ${m.average_rating}</s></div>
        </div>`).join('');
}

/* 03 — Kimleri izliyorsun */
function chapterPeople(stats) {
    const fav = (stats.bayesian_directors?.directors || [])[0];
    const most = (stats.most_watched?.directors || [])[0];
    $('ch3-sub').textContent = fav && most && fav.director !== most.name
        ? `En çok ${most.name} izliyorsun ama en yüksek puanı ${fav.director} alıyor.`
        : 'Yönetmen ve oyuncu tercihlerinin dökümü.';

    ranking('r-dirs', (stats.bayesian_directors?.directors || []).slice(0, 8)
        .map(d => ({ name: d.director, sub: `${d.movie_count} film`, value: d.my_avg, bar: d.my_avg })));
    ranking('r-dirs-count', (stats.most_watched?.directors || []).slice(0, 8)
        .map(d => ({ name: d.name, sub: d.my_avg ? `ort. ${d.my_avg}` : '', value: `${d.count} film`, bar: d.count })));
    ranking('r-actors', (stats.bayesian_actors?.actors || []).slice(0, 8)
        .map(a => ({ name: a.actor, sub: `${a.movie_count} film`, value: a.my_avg, bar: a.my_avg })));
    ranking('r-actors-count', (stats.most_watched?.actors || []).slice(0, 8)
        .map(a => ({ name: a.name, sub: a.my_avg ? `ort. ${a.my_avg}` : '', value: `${a.count} film`, bar: a.count })));
    ranking('r-pairs', (stats.network?.top_collaborations || []).slice(0, 8)
        .map(p => ({ name: p.pair, value: `${p.count} film`, bar: p.count })));
}

/* 04 — Ne izliyorsun */
function chapterWhat(stats) {
    const genres = (stats.genre_distribution?.genres || []).filter(g => g.count >= 10);
    const byCount = [...genres].sort((a, b) => b.count - a.count);
    const byScore = [...genres].sort((a, b) => b.avg_my_rating - a.avg_my_rating);

    if (byCount[0] && byScore[0] && byCount[0].genre !== byScore[0].genre) {
        $('ch4-sub').textContent =
            `En çok ${byCount[0].genre} izliyorsun (${byCount[0].count} film, ${byCount[0].avg_my_rating}) `
            + `ama en yüksek puanı ${byScore[0].genre} alıyor (${byScore[0].count} film, ${byScore[0].avg_my_rating}).`;
    }

    // Ratings bunch up between roughly 2.8 and 4.0, so a 0–5 bar makes every
    // genre look identical. Stretch the bar across the observed range instead,
    // and colour it by whether the genre beats the user's own average.
    const shown = byCount.slice(0, 10);
    const maxCount = Math.max(...shown.map(g => g.count), 1);
    const scores = shown.map(g => g.avg_my_rating);
    const lo = Math.min(...scores) - 0.1;
    const hi = Math.max(...scores) + 0.1;
    const mean = Number(stats.summary.avg_my_rating) || 0;

    $('genre-table').innerHTML = shown.map(g => {
        const width = ((g.avg_my_rating - lo) / (hi - lo)) * 100;
        const beats = g.avg_my_rating >= mean;
        return `
        <div class="genre-row${beats ? '' : ' below'}">
            <span class="g">${esc(g.genre)}<small>${g.count} film</small></span>
            <span class="bars">
                <i class="bar count" style="width:${(g.count / maxCount) * 100}%"></i>
                <i class="bar score" style="width:${width}%"></i>
            </span>
            <span class="s">${g.avg_my_rating}</span>
        </div>`;
    }).join('')
        + `<div class="genre-legend">
             <span class="k1">Kaç film izledin</span>
             <span class="k2">Ortalama puanın</span>
             <span class="k3">Genel ortalamanın (${mean}) altında</span>
           </div>`;

    const rc = stats.runtime_counts, ra = stats.runtime_avg_rating;
    chart('c-runtime', {
        type: 'bar',
        data: {
            labels: rc.labels,
            datasets: [
                { label: 'Film', data: rc.values, backgroundColor: 'rgba(255,59,92,0.35)', borderRadius: 6, yAxisID: 'y' },
                { label: 'Ortalama', data: ra.values, type: 'line', borderColor: '#FF3B5C',
                  pointBackgroundColor: '#FF3B5C', tension: 0.3, yAxisID: 'y1' },
            ],
        },
        options: {
            plugins: {
                legend: { display: true, labels: { color: '#8E8EA3', boxWidth: 12, font: { family: 'Inter', size: 11 } } },
            },
            scales: {
                x: { ticks: AXIS, grid: GRID },
                y: { ticks: AXIS, grid: GRID, position: 'left' },
                y1: { ticks: AXIS, grid: { display: false }, position: 'right', min: 0, max: 5 },
            },
        },
    });

    const best = ra.labels[ra.values.indexOf(Math.max(...ra.values.filter(v => v != null)))];
    const chi = stats.chi_square;
    $('runtime-note').textContent = chi?.p_value != null
        ? `En yüksek puanı ${best} filmlere veriyorsun. İstatistiksel testte p = ${chi.p_value.toFixed(3)} — `
          + (chi.significant ? 'bu ilişki anlamlı.' : 'eğilim var ama kanıt zayıf.')
        : '';

    const dec = stats.decades;
    if (dec?.labels?.length) {
        chart('c-decades', {
            type: 'bar',
            data: { labels: dec.labels, datasets: [{ data: dec.counts, backgroundColor: '#FF3B5C', borderRadius: 6 }] },
        });
    }
}

/* 05 — Ne zaman izliyorsun */
function chapterWhen(stats) {
    const t = stats.temporal_evolution;
    if (t?.years?.length) {
        chart('c-trend', {
            type: 'line',
            data: {
                labels: t.years,
                datasets: [{ data: t.avg_ratings, borderColor: '#00E5A0', backgroundColor: 'rgba(0,229,160,0.12)',
                             tension: 0.35, fill: true, pointBackgroundColor: '#00E5A0', pointRadius: 5 }],
            },
            options: { scales: { x: { ticks: AXIS, grid: GRID }, y: { ticks: AXIS, grid: GRID, min: 0, max: 5 } } },
        });
    }

    const b = stats.backlog;
    if (b?.categories) {
        const LABELS = { 'Release Year': 'Çıktığı yıl', 'Recent (1-2y)': 'Yeni (1-2 yıl)',
                         'Decade (3-10y)': '3-10 yıllık', 'Classic (11-30y)': 'Klasik (11-30 yıl)',
                         'Old (31y+)': 'Eski (31+ yıl)' };
        chart('c-backlog', {
            type: 'bar',
            data: {
                labels: b.categories.map(c => LABELS[c] || c),
                datasets: [{ data: b.counts, backgroundColor: '#00E5A0', borderRadius: 6 }],
            },
            options: { indexAxis: 'y', scales: { x: { ticks: AXIS, grid: GRID }, y: { ticks: AXIS, grid: GRID } } },
        });
        const top = b.categories[b.counts.indexOf(Math.max(...b.counts))];
        $('backlog-note').textContent =
            `İzlediğin filmin ortalama yaşı ${b.avg_age}. En büyük dilim: ${LABELS[top] || top}.`;
        $('ch5-sub').textContent = b.avg_age >= 8
            ? 'Vizyon takipçisi değilsin — arşiv kazıyorsun.'
            : 'Çoğunlukla yeni çıkanları izliyorsun.';
    }
}

/* 06 — Nereden izliyorsun */
function chapterWhere(stats) {
    const countries = stats.diversity?.top_countries || [];
    const langs = stats.diversity?.top_languages || [];
    // Share is computed over the top-10 slice the API returns, which is close
    // enough for a headline — but the country *count* must come from the
    // summary, or it would read as "10 countries" for everyone.
    const shown = countries.reduce((sum, c) => sum + c.count, 0);
    const top3 = countries.slice(0, 3).reduce((sum, c) => sum + c.count, 0);
    $('ch6-sub').textContent = shown
        ? `${stats.summary.unique_countries} ülke, ${stats.summary.unique_languages} dil. `
          + `Ama filmlerinin %${Math.round(top3 / shown * 100)}'i ilk üç ülkeden.`
        : '';

    ranking('r-countries', countries.slice(0, 8).map(c => ({ name: c.name, value: `${c.count} film`, bar: c.count })));
    ranking('r-langs', langs.slice(0, 8).map(l => ({ name: l.name, value: `${l.count} film`, bar: l.count })));
}

/* 07 — Seni ne tahmin ediyor */
function chapterDrivers(explain) {
    if (!explain?.drivers?.length) {
        $('drivers').innerHTML = '<p style="color:var(--muted)">Model için yeterli veri yok.</p>';
        return;
    }
    $('ch7-sub').textContent =
        'Puanlarını tahmin eden bir model eğittik. Kararında en çok neyin ağırlığı var?';
    $('drivers').innerHTML =
        `<p class="driver-lead">${esc(explain.headline)}</p>`
        + explain.drivers.map(d => `
            <div class="driver">
                <span class="l">${esc(d.label)}</span>
                <span class="p">%${d.share}</span>
                <span class="track"><i style="width:${d.share}%"></i></span>
            </div>`).join('');
}

/* ── boot ────────────────────────────────────────────────────── */

function wireButtons() {
    $('btn-skip').addEventListener('click', () => Quiz.skip());
    $('btn-dash').addEventListener('click', () =>
        $('dash-bar').scrollIntoView({ behavior: 'smooth' }));
    $('btn-back').addEventListener('click', () =>
        window.scrollTo({ top: 0, behavior: 'smooth' }));

    $('btn-again').addEventListener('click', () => {
        clear(); session = null; wrapped = null; quizResult = null;
        loadAnalysis.done = false;
        $('go').disabled = true; $('go').textContent = 'Başla';
        $('file-name').textContent = ''; $('drop').classList.remove('filled');
        show('landing');
    });
    $('btn-reset').addEventListener('click', () => $('btn-again').click());

    $('btn-png').addEventListener('click', async () => {
        try {
            const canvas = await html2canvas($('share-card'), { backgroundColor: '#0A0A0F', scale: 2 });
            const link = document.createElement('a');
            link.download = 'letterboxd-wrapped.png';
            link.href = canvas.toDataURL('image/png');
            link.click();
        } catch { toast('Görsel oluşturulamadı', true); }
    });
}

/**
 * URL hooks. A stored session otherwise sends every visit straight to the
 * analysis, with no way back to the test:
 *   #test  replay the quiz on the stored session, without re-uploading
 *   #yeni  drop the session and start over from the landing page
 * Returns true when the hook took over the screen.
 */
async function runHook(hook) {
    if (hook !== 'test' && hook !== 'yeni') return false;
    history.replaceState(null, '', location.pathname);

    if (hook === 'yeni') { $('btn-again').click(); return true; }

    let stored = null;
    try { stored = JSON.parse(localStorage.getItem(KEY) || 'null'); } catch {}
    if (!stored?.session) return false;

    session = stored.session;
    quizResult = null;
    loadAnalysis.done = false;
    try {
        const state = await api(`/api/status?session=${session}`);
        if (!state.exists) { clear(); return false; }
        startQuiz(state.scrape);
        return true;
    } catch {
        clear();
        return false;
    }
}


async function boot() {
    wireLanding();
    wireButtons();
    paintPosterWall();

    // Hash hooks run on load *and* on hashchange: typing #test into the bar of
    // an already-open page is a fragment navigation, so nothing reloads.
    window.addEventListener('hashchange', () => runHook(location.hash.slice(1)));
    if (await runHook(location.hash.slice(1))) return;

    let stored = null;
    try { stored = JSON.parse(localStorage.getItem(KEY) || 'null'); } catch {}
    if (!stored?.session) return;

    // Resume a previous visit if the server still has the session.
    try {
        wrapped = await api(`/api/wrapped?session=${stored.session}`);
        session = stored.session;
        quizResult = stored.quizResult || { score: 0, total: 0 };
        renderResult();
        show('wrapped');
        loadAnalysis();
    } catch {
        clear();
    }
}

boot();
