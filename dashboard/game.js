/* ============================================================
   "Daha Çok İzlenen" — the higher-lower duel

   Two films, one number showing: how many Letterboxd members have rated
   it. That is not the watch count — Letterboxd keeps that behind a fragment
   Cloudflare will not serve us — so the interface says "puanlamış", never
   "izlemiş".

   The deck arrives whole from /api/game/popularity and every round is
   decided on the client, so a run costs exactly one request however long
   it lasts.
   ============================================================ */

const Game = (() => {

const $ = (id) => document.getElementById(id);
const BEST_KEY = 'lbxw-best';
const nf = new Intl.NumberFormat('tr-TR');

let deck = [];
let at = 0;          // index of the film whose number is already showing
let streak = 0;
let locked = false;  // set between an answer and the next round
let mode = 'shared';

const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const best = {
    get: () => { try { return +localStorage.getItem(BEST_KEY) || 0; } catch { return 0; } },
    set: (v) => { try { localStorage.setItem(BEST_KEY, v); } catch {} },
};

/* ── a run ───────────────────────────────────────────────────── */

/**
 * Fetch a deck and deal the first pair.
 *
 * `session` is optional: with one the films are the visitor's own, which
 * turns the question from "what do people watch" into "what did I watch".
 */
async function start(session = null) {
    const track = $('duel-track');
    track.innerHTML = '<p class="game-loading">Deste hazırlanıyor…</p>';
    $('game-over').hidden = true;
    $('duel').hidden = false;

    let data;
    try {
        data = await api(`/api/game/popularity?n=60${session ? `&session=${session}` : ''}`);
    } catch {
        track.innerHTML = '<p class="game-loading">Deste alınamadı. Sonra tekrar dene.</p>';
        return;
    }

    deck = data.films || [];
    mode = data.mode || 'shared';
    if (deck.length < 8) {
        track.innerHTML = '<p class="game-loading">Bu oyun için yeterli film yok.</p>';
        return;
    }

    at = 0;
    streak = 0;
    locked = false;
    $('game-mode').textContent = mode === 'mine' ? 'kendi filmlerin' : 'herkesin filmleri';
    paintScore();

    track.innerHTML = deck.map(panel).join('');
    track.style.setProperty('--i', 0);
    loadWindow();
    reveal(0, true);   // the opening film shows its number from the start
}

/** One side of the duel. Posters stay unset until the panel is nearly up. */
function panel(film, k) {
    return `
    <div class="duel-side" style="--k:${k}" id="side-${k}">
        <img class="duel-poster" data-src="${esc(film.poster)}" alt="" />
        <div class="duel-scrim"></div>
        <div class="duel-body">
            <h2 class="duel-title">${esc(film.title)}</h2>
            <p class="duel-year">${film.year ? esc(film.year) : ''}</p>
            <div class="duel-figure" id="fig-${k}">
                <b class="duel-count" id="count-${k}">0</b>
                <span class="duel-unit">kişi Letterboxd'da puanlamış</span>
            </div>
        </div>
    </div>`;
}

/**
 * Give src only to the panels about to be seen.
 *
 * The whole deck is in the DOM so the slide can be a single uninterrupted
 * transform, but sixty posters is a megabyte nobody asked for. `loading`
 * cannot help here — the offscreen panels are inside the transformed track,
 * which the browser still counts as being in the viewport.
 */
function loadWindow() {
    for (let k = at - 1; k <= at + 3; k++) {
        const el = $(`side-${k}`);
        if (!el) continue;
        const img = el.querySelector('.duel-poster');
        if (img?.dataset.src) { img.src = img.dataset.src; delete img.dataset.src; }
    }
}

/* ── rounds ──────────────────────────────────────────────────── */

/** Show a panel's number: counted up on a reveal, printed on the opener. */
function reveal(k, instant = false) {
    const fig = $(`fig-${k}`);
    const out = $(`count-${k}`);
    if (!fig || !out) return;
    fig.classList.add('shown');

    const target = deck[k].rated_by;
    if (instant) { out.textContent = nf.format(target); return; }

    const started = performance.now();
    const step = (now) => {
        const t = Math.min((now - started) / 850, 1);
        const eased = 1 - Math.pow(1 - t, 4);
        out.textContent = nf.format(Math.round(target * eased));
        if (t < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
}

/**
 * Answer the round.
 *
 * A tie counts as correct. Telling somebody they were wrong about two films
 * that are genuinely level would be the game's fault, not theirs.
 */
function answer(higher) {
    if (locked || at + 1 >= deck.length) return;
    locked = true;

    const mine = deck[at].rated_by;
    const theirs = deck[at + 1].rated_by;
    const right = theirs === mine || (theirs > mine) === higher;

    $('duel-ask').classList.add('spent');
    reveal(at + 1);

    const verdict = $('duel-verdict');
    verdict.textContent = right ? '✓' : '✗';
    verdict.className = 'duel-verdict ' + (right ? 'good' : 'bad');

    // Counted here rather than after the pause: the tick belongs to the
    // moment of the reveal, not to the slide that follows it.
    if (right) { streak++; paintScore(); }

    setTimeout(() => {
        if (!right) return end();
        if (at + 2 >= deck.length) return end(true);
        advance();
    }, 1250);
}

/** Slide the challenger into the left slot and bring in a fresh one. */
function advance() {
    at++;
    $('duel-track').style.setProperty('--i', at);
    $('duel-verdict').className = 'duel-verdict';
    $('duel-verdict').textContent = '';
    $('duel-ask').classList.remove('spent');
    loadWindow();
    locked = false;
}

function paintScore() {
    $('game-streak').textContent = streak;
    const b = best.get();
    $('game-best').textContent = b ? `en iyi ${b}` : '';
}

/* ── the end ─────────────────────────────────────────────────── */

function end(ranOut = false) {
    const b = best.get();
    const record = streak > b;
    if (record) best.set(streak);

    $('duel').hidden = true;
    const box = $('game-over');
    box.hidden = false;
    box.innerHTML = `
        <p class="over-num">${streak}</p>
        <p class="over-label">${ranOut ? 'deste bitti — hepsini bildin' : 'doğru üst üste'}</p>
        <p class="over-note">${record && streak > 0 ? 'Yeni rekor.'
            : b ? `En iyin ${b}.` : ''}</p>
        <div class="over-acts">
            <button class="btn-go" id="over-again">Tekrar oyna</button>
            ${mode === 'shared'
                ? '<button class="btn-ghost" id="over-mine">Kendi filmlerinle oyna →</button>'
                : '<button class="btn-ghost" id="over-exit">Analizine dön</button>'}
        </div>`;

    $('over-again').addEventListener('click', () => start(mode === 'mine' ? session : null));
    // Already uploaded? Then "your films" is another run, not a trip to the
    // upload screen they have no reason to see twice.
    $('over-mine')?.addEventListener('click', () => session ? start(session) : show('landing'));
    $('over-exit')?.addEventListener('click', () => show('wrapped'));
    $('over-again').focus();
}

return { start, answer };

})();
