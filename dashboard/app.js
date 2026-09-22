/* ============================================================
   Orchestration: upload → quiz → result → dashboard
   ============================================================ */

const KEY = 'closeup';
const $ = (id) => document.getElementById(id);

let session = null;
let wrapped = null;
let quizResult = null;
let charts = {};

/**
 * The example library's session id, learned from /api/demo.
 *
 * Demo mode is derived from the session rather than kept as its own boolean,
 * because every way out of the demo — the reset buttons, the hash hooks, a
 * real upload — already replaces `session`. A separate flag would have to be
 * cleared in all of them, and the one that got missed would leave the example
 * library's copy and chapters sitting under a stranger's own result.
 */
let demoId = null;
const isDemo = () => !!session && session === demoId;

/* ── helpers ─────────────────────────────────────────────────── */

/**
 * The three screens, as real addresses.
 *
 * Two reasons they are not just CSS classes. The back button: on a phone it is
 * the main way out of a screen, and without history the only thing it could do
 * from the result was leave the site, taking the run with it. And the funnel:
 * Cloudflare Web Analytics has no custom-event API, but it does count SPA route
 * changes, so pushing a path per screen is what makes landing -> quiz -> result
 * readable as drop-off instead of one undifferentiated pile of pageviews.
 *
 * server.py serves index.html on all three, so a reload or a shared link lands
 * somewhere real rather than on a 404.
 */
const PATHS = { landing: '/', play: '/quiz', wrapped: '/result' };

/**
 * The example result reuses the `wrapped` screen but gets its own address.
 *
 * It is not in PATHS for that reason: it is a second way into an existing
 * screen, not a fourth screen. Keeping it off /result is what keeps the
 * funnel readable — a visitor browsing the example never took the test, and
 * counting them as someone who finished it would make the completion number
 * meaningless. It also gives the example a link worth sharing.
 */
const DEMO_PATH = '/example';

function show(page, push = true) {
    document.querySelectorAll('.page').forEach(p => p.classList.toggle('active', p.id === page));
    window.scrollTo({ top: 0 });
    if (push && PATHS[page] && location.pathname !== PATHS[page]) {
        try { history.pushState({ page }, '', PATHS[page]); } catch { /* not fatal */ }
    }
    // Watched here as well as after the fetch, so a failed analysis load can
    // never leave the chapters hidden.
    if (page === 'wrapped') watchReveals();
}

// Going back re-shows the screen without pushing it again, which would other-
// wise trap the reader in a history loop they cannot get out of.
window.addEventListener('popstate', (event) => {
    // The path is read before the history state because the example lives at
    // an address that is not one of the three screens, and because runHook()
    // nulls the state out from under it. Coming back to /example has to
    // re-open the example rather than restore whatever was painted there.
    if (location.pathname === DEMO_PATH) { openDemo(); return; }
    const page = event.state?.page;
    show(page && PATHS[page] ? page : 'landing', false);
});

function toast(message, isError = false) {
    const el = $('toast');
    el.textContent = message;
    el.classList.toggle('err', isError);
    el.classList.add('show');
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.remove('show'), 4200);
}

async function api(path) {
    let res;
    try {
        res = await fetch(path);
    } catch {
        // fetch only rejects on a transport failure — a 404 or a 500 resolves
        // normally. Separating the two is the difference between "your network
        // dropped" and "the server said no", which are not the same advice.
        throw new Error('Lost the connection. Check your network and try again.');
    }
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.error || `Something went wrong (HTTP ${res.status}).`);
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
            toast('That is not a ZIP file. Pick the export Letterboxd gave you.', true);
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
    $('btn-demo').addEventListener('click', () => openDemo());
}

/* ── upload & quiz run ───────────────────────────────────────── */

async function upload(file) {
    $('go').disabled = true;
    $('go').textContent = 'Uploading…';

    try {
        const form = new FormData();
        form.append('file', file);

        let res;
        try {
            res = await fetch('/api/upload-zip', { method: 'POST', body: form });
        } catch {
            throw new Error('Lost the connection while uploading. Try again.');
        }
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || 'That upload did not work. Try again.');

        session = data.session_id;
        quizResult = null;
        save();
        startQuiz(data.scrape);
    } catch (err) {
        toast(err.message, true);
        $('go').disabled = false;
        $('go').textContent = 'Start';
    }
}

/**
 * Open the example library: one real result, read-only, no upload.
 *
 * It walks the ordinary session endpoints and skips the quiz, which is the
 * only honest thing to do with it — the test asks how well you know your own
 * taste, and a stranger answering it about someone else's library is just
 * guessing. What they came for is the result and the chapters.
 *
 * Nothing here is saved: the example must never end up in localStorage, or
 * the next visit would resume into somebody else's library instead of the
 * landing page.
 */
async function openDemo() {
    if (isDemo() && wrapped) { show('wrapped', false); return; }

    const btn = $('btn-demo');
    btn.disabled = true;
    try {
        const { session_id } = await api('/api/demo');
        demoId = session_id;
        session = session_id;
        // Never sat the test, so the result reads as an unscored one.
        quizResult = { score: 0, total: 0, skipped: true };
        wrapped = await api(`/api/wrapped?session=${session}`);
        resetAnalysis();
        renderResult();
        // Pushed by hand rather than through show(), so the run is never
        // recorded against /result. See DEMO_PATH.
        show('wrapped', false);
        if (location.pathname !== DEMO_PATH) {
            try { history.pushState({ page: 'wrapped' }, '', DEMO_PATH); } catch { /* not fatal */ }
        }
        loadAnalysis();
        loadProfile();
    } catch (err) {
        session = null;
        demoId = null;
        toast(err.message, true);
    } finally {
        btn.disabled = false;
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
        setScrapePill(`fetching ${pending} films`, false);
        followScrape();
    } else {
        setScrapePill('ready', true);
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
        setScrapePill('this took too long', true);
        loadFullPhase(true);
    }, 90000);

    stream.onmessage = (event) => {
        const state = JSON.parse(event.data);
        if (state.status === 'running') {
            const left = Math.max(state.total - state.done, 0);
            setScrapePill(`${left} films to go`, false);
            const progress = state.total ? state.done / state.total : 0;
            if (progress >= nextPull) {
                nextPull += 0.2;
                loadFullPhase(false);
            }
            return;
        }
        stop();
        setScrapePill(state.status === 'done' ? 'ready' : 'some films missing', true);
        loadFullPhase(true);
        refreshAfterScrape();
    };
    stream.onerror = () => { stop(); setScrapePill('connection lost', true); loadFullPhase(true); };
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
        // The question card is spent by now — its options are disabled and
        // nothing re-renders it, so a toast alone strands the reader on a
        // dead screen with no way forward. Put the way out in the body.
        toast(err.message, true);
        showQuizFailure(err.message, () => finishQuiz(score, total, skipped));
        return;
    }
    renderResult();
    show('wrapped');
    loadAnalysis();          // ready by the time they scroll down to it
    loadProfile();
}

/** Replace the spent question card with the reason and a way to retry. */
function showQuizFailure(message, retry) {
    const body = $('quiz-body');
    if (!body) return;
    body.innerHTML = `
        <div class="q-fail">
            <p class="q-eyebrow">Could not build your result</p>
            <p class="q-fail-msg">${esc(message)}</p>
            <button class="btn-solid" id="q-retry">Try again</button>
        </div>`;
    $('q-retry').addEventListener('click', () => { body.innerHTML = ''; retry(); }, { once: true });
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
        resetAnalysis();
        loadProfile();
        await loadAnalysis();
        toast('Film details finished loading — the analysis is up to date.');
    } catch { /* keep what's on screen */ }
}

/**
 * The written taste profile. Asked for after every render of the result and
 * again when the scrape lands; the server answers `pending` until it has the
 * whole library, `disabled` when no model is configured, and keeps the text
 * once written, so asking twice costs nothing. Anything but `ready` leaves
 * the block hidden — a spinner here would promise a paragraph that may
 * never come.
 */
async function loadProfile() {
    if (!session) return;
    const asked = session;
    let res;
    try { res = await api(`/api/profile?session=${asked}`); } catch { return; }
    // The reader may have started over while the model was writing.
    if (asked !== session || res.status !== 'ready' || !res.text) return;

    const box = $('taste-text');
    box.innerHTML = '';
    res.text.split(/\n\s*\n/).forEach(para => {
        const p = document.createElement('p');
        p.textContent = para.trim();
        box.appendChild(p);
    });
    $('taste-profile').hidden = false;
}

function clearProfile() {
    $('taste-profile').hidden = true;
    $('taste-text').innerHTML = '';
}

function renderResult() {
    const { score, total, skipped } = quizResult || { score: 0, total: 0 };
    const pct = total ? Math.round((score / total) * 100) : 0;

    // Someone who skipped never claimed to know anything — scoring them 0/0
    // and calling it a failure would be both wrong and rude.
    const noScore = skipped && total === 0;
    $('r-score').hidden = noScore;
    $('quiz-row').hidden = noScore;

    // Set on every render rather than toggled on the way in and out of the
    // example, so there is no restore step that can be missed.
    $('btn-again').textContent = isDemo() ? 'Upload your own export' : 'Upload another export';

    if (noScore && isDemo()) {
        $('r-verdict').textContent = "This is Erdem's library.";
        // Read off the data rather than written down, so the number cannot
        // drift away from the library it describes.
        $('r-note').textContent =
            `${wrapped.total_movies ?? 'Every'} films, every rating and every watch date. `
            + 'Yours will look like this.';
    } else if (noScore) {
        $('r-verdict').textContent = 'Here is your library.';
        $('r-note').textContent = 'You skipped the test — the full analysis is below.';
    } else {
        $('r-score').innerHTML = `${score}<small>/${total}</small>`;
        $('r-verdict').textContent =
            pct >= 80 ? 'You know your own taste.' :
            pct >= 55 ? 'Close — but your taste had a few surprises.' :
            pct >= 30 ? 'Your taste is not quite what you think it is.' :
                        'You do not know your own library.';
        $('r-note').textContent = skipped
            ? `You stopped early — ${score} of ${total} right.`
            : `${score} of ${total} right.`;
    }

    const fav = wrapped.top_director;
    const most = wrapped.most_watched_director;
    const actor = wrapped.most_watched_actor;

    $('s-films').textContent = wrapped.total_movies ?? '—';
    $('s-hours').textContent = Math.round(wrapped.total_hours ?? 0);
    $('s-dirs').textContent = wrapped.unique_directors ?? '—';
    $('s-avg').textContent = wrapped.avg_rating ?? '—';
    $('s-fav').textContent = fav ? `${fav.name} (${fav.my_avg})` : '—';
    $('s-most').textContent = most ? `${most.name} · ${most.movie_count} films` : '—';
    $('s-actor').textContent = actor ? `${actor.name} · ${actor.movie_count} films` : '—';
    $('s-quiz').textContent = `${score}/${total}`;

    // Only once the scrape has placed the library; a "—" here would look
    // like a type that could not be found.
    const type = wrapped.persona;
    $('type-row').hidden = !type;
    $('s-type').textContent = type ? `${type.name.replace(/^The /, '')} · ${type.match_pct}%` : '';
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
        box.innerHTML = '<p class="empty">Could not load those films.</p>';
        return;
    }

    const rated = data.rated_count
        ? `${data.rated_count} rated, you average ${data.my_avg}`
        : 'none of them rated';
    box.innerHTML =
        `<div class="drill-head">
            <span><b>${esc(label)}</b> · ${data.count} films, ${esc(rated)}</span>
            <button class="drill-close">Close</button>
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
    if (!films.length) return '<p class="empty">No films here.</p>';
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
 * Row names are the raw API values, so a row filters on what it displays.
 */
function ranking(el, rows, max = null, zoom = false, drillKey = null) {
    const node = $(el);
    if (!node) return;
    if (!rows.length) {
        node.innerHTML = '<p class="empty">Not enough data yet.</p>';
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
        openable(row, { [drillKey]: rows[i].name }, rows[i].name));
}

/**
 * Fill the analysis chapters. They live on the same page as the summary, so
 * this only loads data — the reader reaches them by scrolling.
 */
/**
 * Drop everything the analysis renders only once.
 *
 * Three separate one-shot guards exist — loadAnalysis.done, loadPeople.done,
 * and the poster wall's own childElementCount check — and clearing only the
 * first leaves the previous library's directors, faces and posters on screen
 * under the next one's numbers. Anything that means to re-render the analysis
 * for a different library has to go through here.
 */
function resetAnalysis() {
    loadAnalysis.done = false;
    loadPeople.done = false;
    $('dash-bg').innerHTML = '';
    clearProfile();
}

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
    chapterType(stats);
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

/* 01 — The Record */
function chapterOverview(stats) {
    const s = stats.summary;
    const hours = wrapped ? Math.round(wrapped.total_hours || 0) : null;

    // Two groups instead of one flat row of eight: "how much" and "how
    // varied" are different questions, and reading eight same-weight
    // numbers as one glance is not actually a glance.
    const volume = [
        ['Films', s.total_movies],
        ['Rated', s.rated_movies],
        ...(hours ? [['Hours', hours.toLocaleString('en')], ['Full days', Math.round(hours / 24)]] : []),
    ].filter(([, value]) => value != null);
    const breadth = [
        ['Directors', s.unique_directors],
        ['Genres', s.unique_genres],
        ['Countries', s.unique_countries],
        ['Languages', s.unique_languages],
    ].filter(([, value]) => value != null);

    const group = (label, cells) => `
        <div class="big-stats-group">
            <span class="big-stats-label">${esc(label)}</span>
            <div class="big-stats-row">
                ${cells.map(([l, v]) => `<div class="big-stat"><span class="v">${esc(v)}</span><span class="l">${esc(l)}</span></div>`).join('')}
            </div>
        </div>`;

    $('big-stats').innerHTML = group('How much you watched', volume) + group('How varied it is', breadth);
    titleCard(1, s.total_movies, 'films',
        `${s.rated_movies} of them rated. You average ${s.avg_my_rating ?? '—'}.`);
}

/* 02 — How You Rate */
function chapterRatings(stats) {
    const dist = stats.rating_distribution;
    const peak = dist.counts.indexOf(Math.max(...dist.counts));
    titleCard(2, dist.ratings[peak], 'your most common score',
        `${dist.counts[peak]} films sit there. You average ${stats.crowd_comparison?.yours ?? '—'}, `
        + `the crowd ${stats.crowd_comparison?.crowd ?? '—'}.`);

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
                `Films you rated ${dist.ratings[hits[0].index]}`),
            onHover: (e, hits) => { e.native.target.style.cursor = hits.length ? 'pointer' : 'default'; },
            plugins: { tooltip: { callbacks: { label: (ctx) => `${ctx.parsed.y} films` } } },
        },
    });
    describeChart('c-ratings', `Rating distribution: your most common score is ${dist.ratings[peak]}, given to ${dist.counts[peak]} films.`);
    $('c-ratings').insertAdjacentHTML('afterend', srTable('c-ratings-table',
        'Rating distribution', ['Rating', 'Films'],
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
                        label: (ctx) => `${ctx.raw.t} — you ${ctx.raw.y}, crowd ${ctx.raw.x}`,
                    },
                },
            },
            scales: {
                x: { title: { display: true, text: 'Crowd rating', color: '#8E8EA3' },
                     ticks: AXIS, grid: GRID, min: 0, max: 5 },
                y: { title: { display: true, text: 'Your rating', color: '#8E8EA3' },
                     ticks: AXIS, grid: GRID, min: 0, max: 5 },
            },
        },
    });

    const c = stats.correlation_my_vs_avg;
    const cmp = stats.crowd_comparison || {};
    $('corr-note').textContent = c?.r != null
        ? `You track the crowd at r = ${c.r} across ${c.n} films. You average ${cmp.yours}, they average ${cmp.crowd}.`
        : '';
    describeChart('c-scatter', c?.r != null
        ? `Your ratings against the crowd average, ${c.n} films. Correlation r = ${c.r}.`
        : 'Scatter plot of your ratings against the crowd average.');

    $('r-contro').innerHTML = (stats.controversial?.controversial || []).slice(0, 8).map(m => `
        <div class="film-card">
            ${m.poster
                ? `<img src="${esc(m.poster)}" alt="" loading="lazy" />`
                : '<div class="poster-blank"></div>'}
            <div class="t">${esc(m.title)}</div>
            <div class="r"><b>${m.my_rating}</b> <s>/ ${m.average_rating}</s></div>
        </div>`).join('');
}

/* 03 — Who You Watch */
function chapterPeople(stats) {
    const fav = (stats.bayesian_directors?.directors || [])[0];
    const most = (stats.most_watched?.directors || [])[0];
    titleCard(3, most?.name ?? '—', most ? `${most.count} films` : '',
        fav && most && fav.director !== most.name
            ? `The director you watch most. But the one you rate highest is ${fav.director}.`
            : 'Who you keep coming back to, on both sides of the camera.',
        true);

    // The two "most watched" rankings are now portrait shelves, filled by
    // loadPeople() — same numbers, with the faces attached.
    ranking('r-dirs', (stats.bayesian_directors?.directors || []).slice(0, 8)
        .map(d => ({ name: d.director, sub: `${d.movie_count} films · avg ${d.my_avg}`,
                     value: d.bayesian_avg, bar: d.bayesian_avg })), null, true, 'director');
    ranking('r-actors', (stats.bayesian_actors?.actors || []).slice(0, 8)
        .map(a => ({ name: a.actor, sub: `${a.movie_count} films · avg ${a.my_avg}`,
                     value: a.bayesian_avg, bar: a.bayesian_avg })), null, true, 'actor');
    ranking('r-pairs', (stats.network?.top_collaborations || []).slice(0, 8)
        .map(p => ({ name: p.pair, value: `${p.count} films`, bar: p.count })));
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
    // The scraped biography is still not shown. It is a paragraph of prose
    // dropped into a card built for one number and one name, and it would
    // bury both. It stays in the people cache, a line away if that changes.
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
    if (!rows.length) { node.innerHTML = '<p class="empty">Not enough data yet.</p>'; return; }
    node.innerHTML = rows.map(p => `
        <div class="person">
            ${p.portrait
                ? `<img src="${esc(p.portrait)}" alt="${esc(p.name)}" loading="lazy" />`
                : '<div class="no-face"></div>'}
            <div class="n">${esc(p.name)}</div>
            <div class="m">${esc(p.count)} films</div>
        </div>`).join('');

    const kind = id === 'shelf-actors' ? 'actor' : 'director';
    node.querySelectorAll('.person').forEach((card, i) =>
        openable(card, { [kind]: rows[i].name }, rows[i].name));
}

/* 04 — What You Watch */
function chapterWhat(stats) {
    const genres = (stats.genre_distribution?.genres || []).filter(g => g.count >= 10);
    const byCount = [...genres].sort((a, b) => b.count - a.count);
    const byScore = [...genres].sort((a, b) => b.avg_my_rating - a.avg_my_rating);

    titleCard(4, byCount[0]?.genre ?? '—', byCount[0] ? `${byCount[0].count} films` : '',
        byCount[0] && byScore[0] && byCount[0].genre !== byScore[0].genre
            ? `The genre you watch most, and you average ${byCount[0].avg_my_rating} on it. The one you `
              + `rate highest is ${byScore[0].genre} (${byScore[0].count} films, ${byScore[0].avg_my_rating}).`
            : 'Your library, broken down by genre.',
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
            <span class="g">${esc(g.genre)}<small>${g.count} films</small></span>
            <span class="bars">
                <i class="bar count" style="width:${(g.count / maxCount) * 100}%"></i>
                <i class="bar score" style="width:${width}%"></i>
            </span>
            <span class="s">${g.avg_my_rating}</span>
        </div>`;
    }).join('')
        + `<div class="genre-legend">
             <span class="k1">How many you watched</span>
             <span class="k2">Your average rating</span>
             <span class="k3">Below your overall average (${mean})</span>
           </div>`;

    $('genre-table').querySelectorAll('.genre-row').forEach((row, i) =>
        openable(row, { genre: shown[i].genre }, shown[i].genre));

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
            plugins: { tooltip: { callbacks: { label: (ctx) => `${ctx.parsed.y} films` } } },
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
                label: (ctx) => ctx.parsed.y != null ? `you average ${ctx.parsed.y}` : 'no data',
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
        ? `You rate ${best} films highest. A chi-square test puts that at p = ${chi.p_value.toFixed(3)} — `
          + (chi.significant ? 'strong enough to call real.' : 'a leaning, but the evidence is thin.')
        : '';
    describeChart('c-runtime-count', 'How many films you have watched at each runtime.');
    describeChart('c-runtime-avg', `Your average rating by runtime. The highest average falls on ${best} films.`);
}

/* 05 — When You Watch */
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
                plugins: { tooltip: { callbacks: { label: (ctx) => `${ctx.parsed.y} films` } } },
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
                    label: (ctx) => ctx.parsed.y != null ? `you average ${ctx.parsed.y}` : 'not enough films',
                } } },
                scales: {
                    x: { ticks: AXIS, grid: { display: false } },
                    y: { ticks: AXIS, grid: GRID, min: 0, max: 5 },
                },
            },
        });
        describeChart('c-decades-count', 'How many films you have watched from each decade.');
        describeChart('c-decades-avg', 'Your average rating by decade.');
        $('c-decades-avg').insertAdjacentHTML('afterend', srTable('c-decades-table',
            'Films watched and average rating by decade', ['Decade', 'Films', 'Your average'],
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
            titleCard(5, best.label, `you average ${best.avg}`,
                `This is the decade you rate highest. The lowest is ${worst.label} `
                + `(${worst.avg}) — and the busiest stretch of your library is ${biggest.label}, `
                + `with ${biggest.n} films.`, true);
            $('decades-note').textContent =
                'Rating older films higher is common, and it is mostly survivorship: only what '
                + 'lasted is still in circulation, so the decade has already been filtered for you.';
        }
    }

    const b = stats.backlog;
    if (b?.categories) {
        const LABELS = { 'Release Year': 'Its release year', 'Recent (1-2y)': 'Recent (1–2 yrs)',
                         'Decade (3-10y)': '3–10 yrs old', 'Classic (11-30y)': 'Classic (11–30 yrs)',
                         'Old (31y+)': 'Older (31+ yrs)' };
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
                plugins: { tooltip: { callbacks: { label: (ctx) => `${ctx.parsed.x} films` } } },
                scales: { x: { ticks: AXIS, grid: GRID }, y: { ticks: AXIS, grid: { display: false } } },
            },
        });
        const top = b.categories[b.counts.indexOf(Math.max(...b.counts))];
        // Say plainly what was left out. Letterboxd's watch dates are log
        // dates, and a bulk import on signup day would otherwise dominate.
        const skipped = b.excluded
            ? ` ${b.excluded} films were left out: they were logged in bulk over ${b.excluded_days} days, `
              + 'so those dates say when you catalogued them, not when you watched them.'
            : '';
        $('backlog-note').textContent =
            `A film was ${b.avg_age} years old on average by the time you logged it. Biggest slice: `
            + `${LABELS[top] || top}. Measured across ${b.used} films.${skipped}`;
        describeChart('c-backlog', `How old films were when you logged them. Biggest slice: ${LABELS[top] || top}.`);
    }
}

/* 06 — Where You Watch */
function chapterWhere(stats) {
    const countries = stats.diversity?.top_countries || [];
    const langs = stats.diversity?.top_languages || [];
    // Share is computed over the top-10 slice the API returns, which is close
    // enough for a headline — but the country *count* must come from the
    // summary, or it would read as "10 countries" for everyone.
    const shown = countries.reduce((sum, c) => sum + c.count, 0);
    const top3 = countries.slice(0, 3).reduce((sum, c) => sum + c.count, 0);
    titleCard(6, stats.summary.unique_countries ?? '—', 'countries',
        shown
            ? `Across ${stats.summary.unique_languages} languages. But `
              + `${Math.round(top3 / shown * 100)}% of your films come from just three countries.`
            : '');

    ranking('r-countries', countries.slice(0, 8)
        .map(c => ({ name: c.name, value: `${c.count} films`, bar: c.count })),
        null, false, 'country');
    ranking('r-langs', langs.slice(0, 8)
        .map(l => ({ name: l.name, value: `${l.count} films`, bar: l.count })),
        null, false, 'language');
}

/* 07 — The Type */

/**
 * Which viewer persona the library sits nearest to. The server does the
 * placing (personas.py); this only draws it: the radar is the library's
 * eleven feature shares laid over the winning persona's centroid, the
 * ranking is closeness to all five. Hidden when the scrape has not given
 * enough films metadata to place the library at all.
 */
function chapterType(stats) {
    const p = stats.persona;
    const section = $('ch7');
    if (!section) return;
    section.hidden = !p;
    if (!p) return;

    titleCard(7, p.name, '',
        `${p.tagline} A ${p.match_pct}% match, with ${p.runner_up.name} second at ${p.runner_up.pct}%.`,
        true);

    const narrow = window.innerWidth < 480;
    chart('c-persona', {
        type: 'radar',
        data: {
            labels: p.axes,
            datasets: [
                { label: 'You', data: p.features,
                  borderColor: accent('c-persona'), backgroundColor: accent('c-persona', 0.22),
                  pointBackgroundColor: accent('c-persona'), pointRadius: 3, pointHoverRadius: 5,
                  pointHitRadius: 12, borderWidth: 2 },
                { label: p.name, data: p.centroid,
                  borderColor: 'rgba(242,239,230,0.55)', backgroundColor: 'transparent',
                  borderDash: [5, 4], pointRadius: 0, pointHitRadius: 12, borderWidth: 1.5 },
            ],
        },
        options: {
            interaction: { mode: 'nearest', intersect: false },
            // Eleven labels round a phone-width circle clip at the edges;
            // a smaller face and a little padding keep them whole.
            layout: { padding: narrow ? 4 : 10 },
            plugins: { tooltip: { callbacks: {
                label: (ctx) => `${ctx.dataset.label}: ${Math.round(ctx.parsed.r * 100)}%`,
            } } },
            scales: { r: {
                min: 0, max: 1,
                ticks: { display: false, stepSize: 0.25 },
                grid: GRID, angleLines: GRID,
                pointLabels: { color: AXIS.color, font: { ...AXIS.font, size: narrow ? 9 : 11 } },
            } },
        },
    });
    describeChart('c-persona',
        `Your library on eleven axes, against the ${p.name} profile. `
        + p.axes.map((a, i) => `${a}: you ${Math.round(p.features[i] * 100)}%, type ${Math.round(p.centroid[i] * 100)}%`).join('; ') + '.');

    ranking('r-personas', p.ranking.map(r => ({ name: r.name, value: `${r.pct}%`, bar: r.pct })), 100);

    $('persona-why').innerHTML = p.evidence.map(line => `<li>${esc(line)}</li>`).join('');
    $('persona-note').textContent =
        `Placed from the ${p.films_used.toLocaleString('en')} films with full details, by nearest of five `
        + 'hand-drawn profiles — closeness, not a verdict.';
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
        ? `That is ${n.toLocaleString('en')} films, read back to you. Now go argue about it.`
        : 'That is your taste, read back to you. Now go argue about it.';
}

/* ── boot ────────────────────────────────────────────────────── */

async function downloadSharePng() {
    try {
        const canvas = await html2canvas($('share-card'), { backgroundColor: '#0A0A0F', scale: 2 });
        const link = document.createElement('a');
        link.download = 'close-up.png';
        link.href = canvas.toDataURL('image/png');
        link.click();
    } catch { toast('Could not build the image. Try again.', true); }
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
        resetAnalysis();
        $('go').disabled = true; $('go').textContent = 'Start';
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
 *   #new   drop the session and start over from the landing page
 * Returns true when the hook took over the screen.
 */
async function runHook(hook) {
    if (hook !== 'test' && hook !== 'new') return false;
    history.replaceState(null, '', location.pathname);

    if (hook === 'new') { $('btn-again').click(); return true; }

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

    // Before the stored session is read, not after: a returning visitor who
    // opens a shared /example link came for the example, and restoring their
    // own result here would both show them the wrong thing and push a
    // /result they never earned.
    if (location.pathname === DEMO_PATH) { openDemo(); return; }

    // /quiz or /result with nothing to restore is the landing page under the
    // wrong address. Put the bar back in step so the next push is honest.
    const landHere = () => {
        if (location.pathname !== '/') {
            try { history.replaceState({ page: 'landing' }, '', '/'); } catch {}
        }
    };

    let stored = null;
    try { stored = JSON.parse(localStorage.getItem(KEY) || 'null'); } catch {}
    if (!stored?.session) { landHere(); return; }

    // Resume a previous visit if the server still has the session.
    try {
        wrapped = await api(`/api/wrapped?session=${stored.session}`);
        session = stored.session;
        quizResult = stored.quizResult || { score: 0, total: 0 };
        renderResult();
        show('wrapped');
        loadAnalysis();
        loadProfile();
    } catch {
        clear();
        landHere();
    }
}

boot();
