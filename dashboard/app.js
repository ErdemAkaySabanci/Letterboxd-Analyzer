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
    // Watched here as well as after the fetch, so a failed analysis load can
    // never leave the chapters hidden.
    if (page === 'wrapped') watchReveals();
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

/**
 * Follow the background scrape.
 *
 * Phase-2 questions used to wait for the *whole* scrape to report done, which
 * on a cold library is long enough that the quiz looks broken. They are pulled
 * at a couple of points on the way instead. No threshold has to be agreed on:
 * `Quiz.add()` drops ids it already holds, so an early pull simply takes
 * whatever `build_full_quiz()` can build from the metadata that has landed,
 * and a later one fills in the rest.
 */
function followScrape() {
    const stream = new EventSource(`/api/progress?session=${session}`);
    let nextPull = 0.4;                       // fraction of the scrape
    let giveUp = 0;
    const stop = () => { stream.close(); clearTimeout(giveUp); };

    // A scrape that hangs still has to let the quiz end.
    giveUp = setTimeout(() => {
        stream.close();
        setScrapePill('tarama uzun sürdü', true);
        loadFullPhase(true);
    }, 90000);

    stream.onmessage = (event) => {
        const state = JSON.parse(event.data);
        if (state.status === 'running') {
            const left = Math.max(state.total - state.done, 0);
            setScrapePill(`${left} film kaldı`, false);
            const progress = state.total ? state.done / state.total : 0;
            if (progress >= nextPull) {
                nextPull += 0.2;
                loadFullPhase(false);
            }
            return;
        }
        stop();
        setScrapePill(state.status === 'done' ? 'hazır' : 'bazı filmler eksik', true);
        loadFullPhase(true);
        refreshAfterScrape();
    };
    stream.onerror = () => { stop(); setScrapePill('bağlantı koptu', true); loadFullPhase(true); };
}

async function loadFullPhase(final = true) {
    try {
        const { questions } = await api(`/api/quiz?session=${session}&phase=full`);
        Quiz.add(questions, final);
    } catch {
        if (final) Quiz.expect(0);            // let the quiz end gracefully
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

/**
 * The accent of the chapter a canvas sits in. Chart colours used to be hex
 * literals that happened to match the section accent; reading the token means
 * re-accenting a chapter re-colours its charts too.
 */
function accent(id, alpha = 1) {
    const el = $(id);
    const hex = (el ? getComputedStyle(el).getPropertyValue('--accent').trim() : '') || '';
    const safe = /^#[0-9a-f]{6}$/i.test(hex) ? hex : '#FFB020';
    if (alpha === 1) return safe;
    const [r, g, b] = [1, 3, 5].map(i => parseInt(safe.slice(i, i + 2), 16));
    return `rgba(${r},${g},${b},${alpha})`;
}

/**
 * Chart.js's own tooltip is a flat, undecorated box — the fastest visual
 * tell that a chart came straight from a library default. Rendering it as
 * a real DOM element instead means it can look like the rest of the page
 * (the card surface, the hairline border, the type) rather than like
 * Chart.js. `enabled:false` in `chart()`'s defaults hands positioning and
 * content data to Chart.js while this owns the pixels.
 */
function externalTooltip(context) {
    const { chart: c, tooltip: t } = context;
    const host = c.canvas.closest('.ev') || c.canvas.parentNode;
    if (getComputedStyle(host).position === 'static') host.style.position = 'relative';
    let el = host.querySelector(':scope > .chart-tip');
    if (!el) { el = document.createElement('div'); el.className = 'chart-tip'; host.appendChild(el); }

    if (!t.opacity) { el.classList.remove('show'); return; }

    const title = t.title?.[0];
    el.innerHTML = (title ? `<i>${esc(title)}</i>` : '')
        + (t.body || []).map(b => `<b>${esc(b.lines.join(' '))}</b>`).join('');
    el.classList.add('show');

    // Keep the tip inside the chart's own box rather than drifting past its
    // right edge, which a plain caretX offset would do near the last bar.
    const w = el.offsetWidth, chartW = c.width;
    const x = Math.min(Math.max(t.caretX, w / 2 + 4), chartW - w / 2 - 4);
    el.style.left = `${c.canvas.offsetLeft + x}px`;
    el.style.top = `${c.canvas.offsetTop + t.caretY}px`;
}

function chart(id, config) {
    charts[id]?.destroy();
    const el = $(id);
    if (!el) return;
    const callerPlugins = config.options?.plugins || {};
    // `tooltip` is merged one level deeper than the rest: a caller that sets
    // its own `plugins.tooltip.callbacks` (for a custom label string) must
    // not blow away the external renderer, and a caller that sets nothing
    // must still get it.
    charts[id] = new Chart(el, {
        ...config,
        options: {
            ...config.options,
            responsive: true,
            maintainAspectRatio: false,
            animation: { duration: 700, easing: 'easeOutQuint', ...(config.options?.animation || {}) },
            plugins: {
                legend: { display: false },
                ...callerPlugins,
                tooltip: { enabled: false, external: externalTooltip, ...(callerPlugins.tooltip || {}) },
            },
            scales: config.options?.scales ?? {
                x: { ticks: AXIS, grid: GRID }, y: { ticks: AXIS, grid: GRID },
            },
        },
    });
}

const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/**
 * Letterboxd's genre, country, and language fields come back in English —
 * they're scraped, not translated. Everywhere else on this page is Turkish,
 * so an untranslated "Drama" or "USA" reads as a seam. These maps cover
 * Letterboxd's genre taxonomy in full and the countries/languages real
 * libraries hit most; anything missing falls back to the English original
 * rather than showing nothing.
 */
const GENRE_TR = {
    'Action': 'Aksiyon', 'Adventure': 'Macera', 'Animation': 'Animasyon',
    'Comedy': 'Komedi', 'Crime': 'Suç', 'Documentary': 'Belgesel',
    'Drama': 'Dram', 'Family': 'Aile', 'Fantasy': 'Fantastik',
    'History': 'Tarih', 'Horror': 'Korku', 'Music': 'Müzik',
    'Mystery': 'Gizem', 'Romance': 'Romantik', 'Science Fiction': 'Bilim Kurgu',
    'TV Movie': 'TV Filmi', 'Thriller': 'Gerilim', 'War': 'Savaş', 'Western': 'Vahşi Batı',
};
const COUNTRY_TR = {
    'USA': 'ABD', 'United States': 'ABD', 'United States of America': 'ABD',
    'UK': 'İngiltere', 'United Kingdom': 'İngiltere', 'Turkey': 'Türkiye',
    'France': 'Fransa', 'Germany': 'Almanya', 'Italy': 'İtalya', 'Spain': 'İspanya',
    'Canada': 'Kanada', 'Australia': 'Avustralya', 'Japan': 'Japonya',
    'South Korea': 'Güney Kore', 'North Korea': 'Kuzey Kore', 'China': 'Çin',
    'Hong Kong': 'Hong Kong', 'Taiwan': 'Tayvan', 'India': 'Hindistan',
    'Russia': 'Rusya', 'Netherlands': 'Hollanda', 'Belgium': 'Belçika',
    'Sweden': 'İsveç', 'Norway': 'Norveç', 'Denmark': 'Danimarka',
    'Finland': 'Finlandiya', 'Iceland': 'İzlanda', 'Poland': 'Polonya',
    'Austria': 'Avusturya', 'Switzerland': 'İsviçre', 'Ireland': 'İrlanda',
    'Portugal': 'Portekiz', 'Greece': 'Yunanistan', 'Hungary': 'Macaristan',
    'Czech Republic': 'Çekya', 'Romania': 'Romanya', 'Bulgaria': 'Bulgaristan',
    'Ukraine': 'Ukrayna', 'Croatia': 'Hırvatistan', 'Serbia': 'Sırbistan',
    'Mexico': 'Meksika', 'Brazil': 'Brezilya', 'Argentina': 'Arjantin',
    'Chile': 'Şili', 'Colombia': 'Kolombiya', 'Peru': 'Peru',
    'New Zealand': 'Yeni Zelanda', 'South Africa': 'Güney Afrika',
    'Israel': 'İsrail', 'Iran': 'İran', 'Egypt': 'Mısır', 'Morocco': 'Fas',
    'Thailand': 'Tayland', 'Indonesia': 'Endonezya', 'Malaysia': 'Malezya',
    'Philippines': 'Filipinler', 'Vietnam': 'Vietnam', 'Singapore': 'Singapur',
    'Lebanon': 'Lübnan', 'Saudi Arabia': 'Suudi Arabistan',
    'Malawi': 'Malavi', 'Nigeria': 'Nijerya', 'Kenya': 'Kenya',
};
const LANG_TR = {
    'English': 'İngilizce', 'Turkish': 'Türkçe', 'French': 'Fransızca',
    'German': 'Almanca', 'Italian': 'İtalyanca', 'Spanish': 'İspanyolca',
    'Portuguese': 'Portekizce', 'Russian': 'Rusça', 'Chinese': 'Çince',
    'Cantonese': 'Kantonca', 'Mandarin': 'Mandarin Çincesi', 'Japanese': 'Japonca',
    'Korean': 'Korece', 'Hindi': 'Hintçe', 'Arabic': 'Arapça', 'Hebrew (modern)': 'İbranice',
    'Persian (Farsi)': 'Farsça', 'Dutch': 'Felemenkçe', 'Swedish': 'İsveççe',
    'Norwegian': 'Norveççe', 'Danish': 'Danca', 'Finnish': 'Fince',
    'Icelandic': 'İzlandaca', 'Polish': 'Lehçe', 'Czech': 'Çekçe',
    'Slovak': 'Slovakça', 'Hungarian': 'Macarca', 'Romanian': 'Rumence',
    'Bulgarian': 'Bulgarca', 'Greek (modern)': 'Yunanca', 'Ukrainian': 'Ukraynaca',
    'Croatian': 'Hırvatça', 'Serbo-Croatian': 'Sırp-Hırvatça', 'Latin': 'Latince',
    'Thai': 'Tayca', 'Vietnamese': 'Vietnamca', 'Indonesian': 'Endonezce',
    'Malay': 'Malayca', 'Tagalog': 'Tagalogca', 'Swahili': 'Svahilice',
    'Urdu': 'Urduca', 'Estonian': 'Estonca', 'Yiddish': 'Yidiş',
};
const trGenre = (g) => g == null ? g : (GENRE_TR[g] || g);
const trCountry = (c) => c == null ? c : (COUNTRY_TR[c] || c);
const trLang = (l) => l == null ? l : (LANG_TR[l] || l);

/**
 * A screen-reader-only data table, kept in sync beside a canvas the chart
 * library draws into. `Chart.js` renders to a bitmap with no text content,
 * so a canvas alone gives a screen reader nothing; this restates the same
 * numbers as a real table instead of just labelling the shape.
 */
function srTable(id, caption, headers, rows) {
    document.getElementById(id)?.remove();
    return `<table id="${id}" class="sr-only"><caption>${esc(caption)}</caption>
        <thead><tr>${headers.map(h => `<th scope="col">${esc(h)}</th>`).join('')}</tr></thead>
        <tbody>${rows.map(r => `<tr>${r.map(c => `<td>${esc(c)}</td>`).join('')}</tr>`).join('')}</tbody>
    </table>`;
}

/** Give a chart canvas an accessible name; canvases carry none on their own. */
function describeChart(id, label) {
    const el = $(id);
    if (!el) return;
    el.setAttribute('role', 'img');
    el.setAttribute('aria-label', label);
}

/**
 * The one statement a chapter opens on: a number or a name, an optional
 * unit, and the sentence underneath that explains it.
 *
 * `word` marks the value as a person or a genre rather than a measurement.
 * Those get their own smaller treatment, both so a long name still fits and
 * because a name reads as a subject, not a quantity.
 */
function titleCard(n, stat, unit, line, word = false) {
    const el = $(`ch${n}-stat`);
    if (el) {
        el.innerHTML = esc(stat ?? '—') + (unit ? `<small>${esc(unit)}</small>` : '');
        el.classList.toggle('is-word', word);
    }
    const sub = $(`ch${n}-sub`);
    if (sub) sub.textContent = line || '';
}

/**
 * Entry reveals. One observer for the whole page, each element dropped the
 * moment it lands, and the animation itself left to CSS — nothing here runs
 * per frame.
 */
const revealer = 'IntersectionObserver' in window
    ? new IntersectionObserver((entries, obs) => {
        entries.forEach(entry => {
            if (!entry.isIntersecting) return;
            entry.target.classList.add('in');
            obs.unobserve(entry.target);
        });
    }, { threshold: 0.12, rootMargin: '0px 0px -6% 0px' })
    : null;

function watchReveals() {
    const targets = document.querySelectorAll('#wrapped .rise:not(.in)');
    // No observer means no reveal: show everything rather than nothing.
    if (!revealer) { targets.forEach(el => el.classList.add('in')); return; }
    targets.forEach(el => revealer.observe(el));
}

/**
 * Open the films behind one claim.
 *
 * The panel is inserted after the element that was clicked, never in a
 * modal: the row stays where the reader found it. Clicking the same thing
 * again closes it, and only one panel is open at a time.
 */
async function drill(host, params, label) {
    const key = JSON.stringify(params);
    const current = document.querySelector('.drill');
    const repeat = current && current.previousElementSibling === host
                            && current.dataset.key === key;
    current?.remove();
    if (repeat) return;

    const box = document.createElement('div');
    box.className = 'drill';
    box.dataset.key = key;
    box.innerHTML = `<div class="drill-head"><span><b>${esc(label)}</b></span></div>`
                  + '<div class="film-strip"><div class="poster-blank"></div>'
                  + '<div class="poster-blank"></div><div class="poster-blank"></div></div>';
    host.after(box);

    let data;
    try {
        const query = new URLSearchParams({ session, limit: 24, ...params });
        data = await api('/api/films?' + query);
    } catch {
        box.innerHTML = '<p class="empty">Filmler alınamadı</p>';
        return;
    }

    const rated = data.rated_count
        ? `${data.rated_count} tanesi puanlı, ortalaman ${data.my_avg}`
        : 'hiçbiri puanlanmamış';
    box.innerHTML =
        `<div class="drill-head">
            <span><b>${esc(label)}</b> · ${data.count} film, ${esc(rated)}</span>
            <button class="drill-close">Kapat</button>
        </div>
        <div class="film-strip">${filmCards(data.films || [])}</div>`;
    box.querySelector('.drill-close').addEventListener('click', () => box.remove());
}

/** Make one element open a drill-down, by pointer or by keyboard. */
function openable(el, params, label) {
    el.classList.add('can-drill');
    el.tabIndex = 0;
    el.setAttribute('role', 'button');
    el.addEventListener('click', () => drill(el, params, label));
    el.addEventListener('keydown', (e) => {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        e.preventDefault();
        drill(el, params, label);
    });
}

/** Poster, title, and your rating against the crowd's. */
function filmCards(films) {
    if (!films.length) return '<p class="empty">Film bulunamadı</p>';
    return films.map(f => `
        <div class="film-card">
            ${f.poster
                ? `<img src="${esc(f.poster)}" alt="" loading="lazy" />`
                : '<div class="poster-blank"></div>'}
            <div class="t">${esc(f.title)}</div>
            <div class="r"><b>${f.my_rating != null ? esc(f.my_rating) : '—'}</b>
                ${f.average_rating != null ? `<s>/ ${esc(f.average_rating)}</s>` : ''}</div>
        </div>`).join('');
}

/**
 * Ranked list; `max` draws a proportional bar under each row.
 *
 * `zoom` stretches the bars across the observed range instead of starting
 * them at zero. Ratings sit in a band roughly 4.0 to 4.5 wide, so a bar
 * measured from zero makes every row in a rating ranking the same length —
 * the same reason the genre table scales to its own range.
 *
 * `drillKey` names the /api/films filter a row stands for, which is what
 * turns a ranking into a way into the library rather than a list to read.
 */
function ranking(el, rows, max = null, zoom = false, drillKey = null) {
    const node = $(el);
    if (!node) return;
    if (!rows.length) {
        node.innerHTML = '<p class="empty">Yeterli veri yok</p>';
        return;
    }
    const values = rows.map(r => Number(r.bar) || 0);
    const peak = max ?? (Math.max(...values) || 1);
    const low = Math.min(...values);
    // Leave the last row a visible stub rather than an empty track.
    const floor = zoom && peak > low ? low - (peak - low) * 0.35 : 0;
    node.innerHTML = rows.map((r, i) => `
        <div class="rank-row">
            <span class="n">${i + 1}</span>
            <span class="name">${esc(r.name)}${r.sub ? `<small>${esc(r.sub)}</small>` : ''}</span>
            <span class="val">${esc(r.value)}</span>
            ${r.bar != null ? `<span class="track"><i style="width:${((r.bar - floor) / (peak - floor)) * 100}%"></i></span>` : ''}
        </div>`).join('');

    if (!drillKey) return;
    node.querySelectorAll('.rank-row').forEach((row, i) =>
        // `filterName` is the raw value the API filters on (e.g. English
        // country names); `name` may be a translated display label and the
        // two have to stay independent.
        openable(row, { [drillKey]: rows[i].filterName ?? rows[i].name }, rows[i].name));
}

/**
 * Fill the analysis chapters. They live on the same page as the summary, so
 * this only loads data — the reader reaches them by scrolling.
 */
async function loadAnalysis() {
    if (loadAnalysis.done) return;

    // The chapters are one screen further down, so the reader can arrive
    // mid-fetch. Shimmer the evidence rather than showing empty space.
    const page = $('wrapped');
    page.classList.add('loading');

    let stats;
    try {
        stats = await api(`/api/stats?session=${session}`);
    } catch (err) { page.classList.remove('loading'); toast(err.message, true); return; }
    page.classList.remove('loading');

    paintDashBg();
    chapterOverview(stats);
    chapterRatings(stats);
    chapterPeople(stats);
    chapterWhat(stats);
    chapterWhen(stats);
    chapterWhere(stats);
    chapterFinale(stats);
    loadPeople();                    // faces follow, the chapter does not wait
    watchReveals();
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

    // Two groups instead of one flat row of eight: "how much" and "how
    // varied" are different questions, and reading eight same-weight
    // numbers as one glance is not actually a glance.
    const volume = [
        ['Film', s.total_movies],
        ['Puanladığın', s.rated_movies],
        ...(hours ? [['Saat', hours.toLocaleString('tr')], ['Tam gün', Math.round(hours / 24)]] : []),
    ].filter(([, value]) => value != null);
    const breadth = [
        ['Yönetmen', s.unique_directors],
        ['Tür', s.unique_genres],
        ['Ülke', s.unique_countries],
        ['Dil', s.unique_languages],
    ].filter(([, value]) => value != null);

    const group = (label, cells) => `
        <div class="big-stats-group">
            <span class="big-stats-label">${esc(label)}</span>
            <div class="big-stats-row">
                ${cells.map(([l, v]) => `<div class="big-stat"><span class="v">${esc(v)}</span><span class="l">${esc(l)}</span></div>`).join('')}
            </div>
        </div>`;

    $('big-stats').innerHTML = group('Ne kadar izledin', volume) + group('Ne kadar çeşitli', breadth);
    titleCard(1, s.total_movies, 'film',
        `${s.rated_movies} tanesi puanlanmış. Ortalaman ${s.avg_my_rating ?? '—'}.`);
}

/* 02 — Nasıl puanlıyorsun */
function chapterRatings(stats) {
    const dist = stats.rating_distribution;
    const peak = dist.counts.indexOf(Math.max(...dist.counts));
    titleCard(2, dist.ratings[peak], 'en sık verdiğin',
        `${dist.counts[peak]} film bu puanda. Ortalaman ${stats.crowd_comparison?.yours ?? '—'}, `
        + `kitlenin ${stats.crowd_comparison?.crowd ?? '—'}.`);

    chart('c-ratings', {
        type: 'bar',
        data: { labels: dist.ratings, datasets: [{ data: dist.counts, backgroundColor: accent('c-ratings'),
            hoverBackgroundColor: accent('c-ratings'), hoverBorderColor: 'rgba(255,255,255,0.5)', hoverBorderWidth: 2,
            borderRadius: 6, maxBarThickness: 46, categoryPercentage: 0.7 }] },
        options: {
            // `intersect:false` makes the whole category column the hit
            // target, not just the painted bar pixels — a short bar next to
            // a tall one otherwise leaves most of its column dead to hover.
            interaction: { mode: 'index', intersect: false },
            onClick: (_e, hits) => hits.length && drill(
                $('c-ratings'), { rating: dist.ratings[hits[0].index] },
                `${dist.ratings[hits[0].index]} verdiğin filmler`),
            onHover: (e, hits) => { e.native.target.style.cursor = hits.length ? 'pointer' : 'default'; },
            plugins: { tooltip: { callbacks: { label: (ctx) => `${ctx.parsed.y} film` } } },
        },
    });
    describeChart('c-ratings', `Puan dağılımı: en sık verdiğin puan ${dist.ratings[peak]}, ${dist.counts[peak]} film.`);
    $('c-ratings').insertAdjacentHTML('afterend', srTable('c-ratings-table',
        'Puan dağılımı', ['Puan', 'Film sayısı'],
        dist.ratings.map((r, i) => [r, dist.counts[i]])));

    const points = stats.scatter?.points || [];
    chart('c-scatter', {
        type: 'scatter',
        data: {
            datasets: [{
                data: points,
                backgroundColor: accent('c-scatter', 0.45),
                pointRadius: 3.5, pointHoverRadius: 6,
                // The painted dot is 7px across — nobody can land a pointer
                // dead-center on that. The hit area is invisible and wider.
                pointHitRadius: 14,
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
    describeChart('c-scatter', c?.r != null
        ? `Senin puanların kitle ortalamasına karşı, ${c.n} film. Uyum r = ${c.r}.`
        : 'Senin puanların kitle ortalamasına karşı dağılım grafiği.');

    $('r-contro').innerHTML = (stats.controversial?.controversial || []).slice(0, 8).map(m => `
        <div class="film-card">
            ${m.poster
                ? `<img src="${esc(m.poster)}" alt="" loading="lazy" />`
                : '<div class="poster-blank"></div>'}
            <div class="t">${esc(m.title)}</div>
            <div class="r"><b>${m.my_rating}</b> <s>/ ${m.average_rating}</s></div>
        </div>`).join('');
}

/* 03 — Kimleri izliyorsun */
function chapterPeople(stats) {
    const fav = (stats.bayesian_directors?.directors || [])[0];
    const most = (stats.most_watched?.directors || [])[0];
    titleCard(3, most?.name ?? '—', most ? `${most.count} film` : '',
        fav && most && fav.director !== most.name
            ? `En çok izlediğin yönetmen. Ama en yüksek ortalamayı ${fav.director} alıyor.`
            : 'Yönetmen ve oyuncu tercihlerinin dökümü.',
        true);

    // The two "most watched" rankings are now portrait shelves, filled by
    // loadPeople() — same numbers, with the faces attached.
    ranking('r-dirs', (stats.bayesian_directors?.directors || []).slice(0, 8)
        .map(d => ({ name: d.director, sub: `${d.movie_count} film · ort. ${d.my_avg}`,
                     value: d.bayesian_avg, bar: d.bayesian_avg })), null, true, 'director');
    ranking('r-actors', (stats.bayesian_actors?.actors || []).slice(0, 8)
        .map(a => ({ name: a.actor, sub: `${a.movie_count} film · ort. ${a.my_avg}`,
                     value: a.bayesian_avg, bar: a.bayesian_avg })), null, true, 'actor');
    ranking('r-pairs', (stats.network?.top_collaborations || []).slice(0, 8)
        .map(p => ({ name: p.pair, value: `${p.count} film`, bar: p.count })));
}

/**
 * Portraits for the people this library watches most.
 *
 * Deliberately not awaited by the chapter render: the scrape leaves the
 * machine, so the names and numbers are on screen immediately and the faces
 * arrive when they arrive. A failure here costs a portrait, never a chapter.
 */
async function loadPeople() {
    if (loadPeople.done) return;
    let people;
    try { people = await api(`/api/people?session=${session}&n=8`); } catch { return; }
    loadPeople.done = true;

    shelf('shelf-dirs', people.directors || []);
    shelf('shelf-actors', people.actors || []);

    const lead = (people.directors || [])[0];
    const face = $('ch3-face');
    if (face && lead?.portrait) {
        face.innerHTML = `<img src="${esc(lead.portrait)}" alt="${esc(lead.name)}" loading="lazy" />`;
        face.hidden = false;
    }
    // The scraped biography is deliberately not shown. Letterboxd carries it
    // in English and this interface is Turkish throughout; one untranslated
    // sentence in a chapter's opening card reads as a defect, not as source
    // material. It stays in the people cache, a line away if that changes.
}

/**
 * A row of people, each in the 2:3 frame a film gets.
 *
 * Counts only, deliberately. An average belongs in the ranking beside this
 * one, which is built from ratings: printing one here would attach a score
 * drawn from three films to a row that says thirteen.
 */
function shelf(id, rows) {
    const node = $(id);
    if (!node) return;
    if (!rows.length) { node.innerHTML = '<p class="empty">Yeterli veri yok</p>'; return; }
    node.innerHTML = rows.map(p => `
        <div class="person">
            ${p.portrait
                ? `<img src="${esc(p.portrait)}" alt="${esc(p.name)}" loading="lazy" />`
                : '<div class="no-face"></div>'}
            <div class="n">${esc(p.name)}</div>
            <div class="m">${esc(p.count)} film</div>
        </div>`).join('');

    const kind = id === 'shelf-actors' ? 'actor' : 'director';
    node.querySelectorAll('.person').forEach((card, i) =>
        openable(card, { [kind]: rows[i].name }, rows[i].name));
}

/* 04 — Ne izliyorsun */
function chapterWhat(stats) {
    const genres = (stats.genre_distribution?.genres || []).filter(g => g.count >= 10);
    const byCount = [...genres].sort((a, b) => b.count - a.count);
    const byScore = [...genres].sort((a, b) => b.avg_my_rating - a.avg_my_rating);

    titleCard(4, trGenre(byCount[0]?.genre) ?? '—', byCount[0] ? `${byCount[0].count} film` : '',
        byCount[0] && byScore[0] && byCount[0].genre !== byScore[0].genre
            ? `En çok izlediğin tür, ortalaman ${byCount[0].avg_my_rating}. Ama en yüksek `
              + `ortalamayı ${trGenre(byScore[0].genre)} alıyor (${byScore[0].count} film, ${byScore[0].avg_my_rating}).`
            : 'Türlere göre dökümün.',
        true);

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
            <span class="g">${esc(trGenre(g.genre))}<small>${g.count} film</small></span>
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

    // The filter stays keyed on the raw English genre the API knows about;
    // only the visible label and drill-panel heading are translated.
    $('genre-table').querySelectorAll('.genre-row').forEach((row, i) =>
        openable(row, { genre: shown[i].genre }, trGenre(shown[i].genre)));

    const rc = stats.runtime_counts, ra = stats.runtime_avg_rating;
    // Two charts sharing one x-axis, not one dual-axis plot: film count and
    // average rating are different units on different scales, and lining
    // them up on two y-axes invents an alignment the data doesn't have.
    chart('c-runtime-count', {
        type: 'bar',
        data: { labels: rc.labels, datasets: [{ data: rc.values, backgroundColor: accent('c-runtime-count', 0.4),
            hoverBackgroundColor: accent('c-runtime-count', 0.7),
            borderRadius: 6, maxBarThickness: 34, categoryPercentage: 0.62 }] },
        options: {
            interaction: { mode: 'index', intersect: false },
            plugins: { tooltip: { callbacks: { label: (ctx) => `${ctx.parsed.y} film` } } },
            scales: {
                x: { ticks: { display: false }, grid: { display: false } },
                y: { ticks: AXIS, grid: GRID },
            },
        },
    });
    chart('c-runtime-avg', {
        type: 'line',
        data: { labels: ra.labels, datasets: [{ data: ra.values,
            borderColor: accent('c-runtime-avg'), backgroundColor: accent('c-runtime-avg', 0.12),
            pointBackgroundColor: accent('c-runtime-avg'), pointRadius: 3, pointHoverRadius: 5,
            pointHitRadius: 14, tension: 0.3, fill: true, spanGaps: true }] },
        options: {
            interaction: { mode: 'index', intersect: false },
            plugins: { tooltip: { callbacks: {
                label: (ctx) => ctx.parsed.y != null ? `ortalama ${ctx.parsed.y}` : 'veri yok',
            } } },
            scales: {
                x: { ticks: AXIS, grid: { display: false } },
                y: { ticks: AXIS, grid: GRID, min: 0, max: 5 },
            },
        },
    });

    const best = ra.labels[ra.values.indexOf(Math.max(...ra.values.filter(v => v != null)))];
    const chi = stats.chi_square;
    $('runtime-note').textContent = chi?.p_value != null
        ? `En yüksek puanı ${best} filmlere veriyorsun. İstatistiksel testte p = ${chi.p_value.toFixed(3)} — `
          + (chi.significant ? 'bu ilişki anlamlı.' : 'eğilim var ama kanıt zayıf.')
        : '';
    describeChart('c-runtime-count', 'Film uzunluğuna göre film sayısı.');
    describeChart('c-runtime-avg', `Film uzunluğuna göre ortalama puan. En yüksek ortalama ${best} filmlerde.`);
}

/* 05 — Hangi dönemi izliyorsun */
function chapterWhen(stats) {
    const dec = stats.decade_ratings;
    if (dec?.labels?.length) {
        // Same fix as chapter 04's runtime chart: two charts on one shared
        // x-axis instead of two y-scales invented alignment.
        chart('c-decades-count', {
            type: 'bar',
            data: { labels: dec.labels, datasets: [{ data: dec.counts, backgroundColor: accent('c-decades-count', 0.4),
                hoverBackgroundColor: accent('c-decades-count', 0.7),
                borderRadius: 6, maxBarThickness: 34, categoryPercentage: 0.62 }] },
            options: {
                interaction: { mode: 'index', intersect: false },
                onClick: (_e, hits) => hits.length && drill(
                    $('c-decades-count'), { decade: dec.decades[hits[0].index] }, dec.labels[hits[0].index]),
                onHover: (e, hits) => { e.native.target.style.cursor = hits.length ? 'pointer' : 'default'; },
                plugins: { tooltip: { callbacks: { label: (ctx) => `${ctx.parsed.y} film` } } },
                scales: {
                    x: { ticks: { display: false }, grid: { display: false } },
                    y: { ticks: AXIS, grid: GRID },
                },
            },
        });
        chart('c-decades-avg', {
            type: 'line',
            data: {
                labels: dec.labels,
                // An average over two films is not a trend. The bars still
                // show those decades honestly; the line simply stops, rather
                // than being drawn across them and implying one.
                datasets: [{ data: dec.avg_ratings.map((v, i) => (dec.counts[i] >= 5 ? v : null)),
                    borderColor: accent('c-decades-avg'), backgroundColor: accent('c-decades-avg', 0.12),
                    pointBackgroundColor: accent('c-decades-avg'), pointRadius: 3, pointHoverRadius: 5,
                    pointHitRadius: 14, tension: 0.3, fill: true, spanGaps: false }],
            },
            options: {
                interaction: { mode: 'index', intersect: false },
                plugins: { tooltip: { callbacks: {
                    label: (ctx) => ctx.parsed.y != null ? `ortalama ${ctx.parsed.y}` : 'yeterli film yok',
                } } },
                scales: {
                    x: { ticks: AXIS, grid: { display: false } },
                    y: { ticks: AXIS, grid: GRID, min: 0, max: 5 },
                },
            },
        });
        describeChart('c-decades-count', 'On yıllara göre film sayısı.');
        describeChart('c-decades-avg', 'On yıllara göre ortalama puan.');
        $('c-decades-avg').insertAdjacentHTML('afterend', srTable('c-decades-table',
            'On yıllara göre film sayısı ve ortalama puan', ['On yıl', 'Film sayısı', 'Ortalama puan'],
            dec.labels.map((label, i) => [label, dec.counts[i], dec.avg_ratings[i] ?? '—'])));

        // The story is the slope, so say it rather than leaving it to be read
        // off the line. Only decades with enough films to mean anything.
        const solid = dec.decades
            .map((d, i) => ({ d, label: dec.labels[i], n: dec.counts[i], avg: dec.avg_ratings[i] }))
            .filter(x => x.n >= 10 && x.avg != null);
        if (solid.length >= 2) {
            const best = solid.reduce((a, b) => (b.avg > a.avg ? b : a));
            const worst = solid.reduce((a, b) => (b.avg < a.avg ? b : a));
            const biggest = solid.reduce((a, b) => (b.n > a.n ? b : a));
            titleCard(5, best.label, `ortalama ${best.avg}`,
                `En yüksek ortalamayı bu on yıla veriyorsun. En düşüğü ${worst.label} `
                + `(${worst.avg}) — ve kütüphanenin en kalabalık dönemi ${biggest.label}, `
                + `${biggest.n} film.`, true);
            $('decades-note').textContent =
                'Eski filmleri daha yüksek puanlamak yaygındır: bir on yıldan bugüne ancak '
                + 'ayakta kalanlar geliyor, yani seçim zaten senin adına yapılmış.';
        }
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
                datasets: [{ data: b.counts, backgroundColor: accent('c-backlog'),
                    hoverBorderColor: 'rgba(255,255,255,0.5)', hoverBorderWidth: 2,
                    borderRadius: 6, maxBarThickness: 30, categoryPercentage: 0.62 }],
            },
            options: {
                indexAxis: 'y',
                interaction: { mode: 'index', intersect: false },
                plugins: { tooltip: { callbacks: { label: (ctx) => `${ctx.parsed.x} film` } } },
                scales: { x: { ticks: AXIS, grid: GRID }, y: { ticks: AXIS, grid: { display: false } } },
            },
        });
        const top = b.categories[b.counts.indexOf(Math.max(...b.counts))];
        // Say plainly what was left out. Letterboxd's watch dates are log
        // dates, and a bulk import on signup day would otherwise dominate.
        const skipped = b.excluded
            ? ` ${b.excluded} film hesaba katılmadı: ${b.excluded_days} günde toplu eklenmişler, `
              + 'o tarihler ne zaman izlediğini değil ne zaman kaydettiğini gösteriyor.'
            : '';
        $('backlog-note').textContent =
            `Kaydettiğinde filmin ortalama yaşı ${b.avg_age} yıldı. En büyük dilim: `
            + `${LABELS[top] || top}. ${b.used} film üzerinden.${skipped}`;
        describeChart('c-backlog', `Filmi ne zaman izlediğine göre dağılım. En büyük dilim: ${LABELS[top] || top}.`);
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
    titleCard(6, stats.summary.unique_countries ?? '—', 'ülke',
        shown
            ? `${stats.summary.unique_languages} dil. Ama filmlerinin `
              + `%${Math.round(top3 / shown * 100)}'i ilk üç ülkeden.`
            : '');

    ranking('r-countries', countries.slice(0, 8)
        .map(c => ({ name: trCountry(c.name), filterName: c.name, value: `${c.count} film`, bar: c.count })),
        null, false, 'country');
    ranking('r-langs', langs.slice(0, 8)
        .map(l => ({ name: trLang(l.name), filterName: l.name, value: `${l.count} film`, bar: l.count })),
        null, false, 'language');
}

/**
 * The reveal doesn't get to just stop after chapter 06 — this is the
 * closing beat: acknowledge it's over, and put the share action back in
 * front of the reader at the moment their engagement is highest, instead
 * of leaving it a full scroll above where they've since scrolled from.
 */
function chapterFinale(stats) {
    const n = stats.summary.total_movies;
    const line = $('finale-line');
    if (line) line.textContent = n
        ? `${n.toLocaleString('tr')} filmlik zevkinin özeti bu. Şimdi paylaşma vakti.`
        : 'Zevkinin özeti bu. Şimdi paylaşma vakti.';
}

/* ── boot ────────────────────────────────────────────────────── */

async function downloadSharePng() {
    try {
        const canvas = await html2canvas($('share-card'), { backgroundColor: '#0A0A0F', scale: 2 });
        const link = document.createElement('a');
        link.download = 'letterboxd-wrapped.png';
        link.href = canvas.toDataURL('image/png');
        link.click();
    } catch { toast('Görsel oluşturulamadı', true); }
}

function wireButtons() {
    $('btn-skip').addEventListener('click', () => Quiz.skip());
    $('btn-dash').addEventListener('click', () =>
        $('dash-bar').scrollIntoView({ behavior: 'smooth' }));

    // Two ways back to the top: the sticky bar once scrolled past the
    // summary, and the closing beat at the very end of the six chapters.
    [$('btn-back'), $('btn-back-2')].forEach(btn => btn?.addEventListener('click', () =>
        window.scrollTo({ top: 0, behavior: 'smooth' })));

    $('btn-again').addEventListener('click', () => {
        clear(); session = null; wrapped = null; quizResult = null;
        loadAnalysis.done = false;
        $('go').disabled = true; $('go').textContent = 'Başla';
        $('file-name').textContent = ''; $('drop').classList.remove('filled');
        show('landing');
    });
    $('btn-reset').addEventListener('click', () => $('btn-again').click());

    // Two ways to the share PNG: the summary card at the top, and the
    // closing beat, so sharing is available right where engagement peaks.
    [$('btn-png'), $('btn-png-2')].forEach(btn => btn?.addEventListener('click', downloadSharePng));
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
    // Gates the reveal system's hidden state: without this class the CSS
    // leaves everything visible, which is what a broken script should do.
    document.documentElement.classList.add('js-rise');
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
