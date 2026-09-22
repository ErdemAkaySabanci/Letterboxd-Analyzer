/* ============================================================
   Quiz engine
   Renders server-built questions one at a time, scores answers,
   and can accept more questions mid-run (the "full" phase lands
   once the metadata scrape finishes).

   Progress is a film strip: every answered question exposes one
   frame, so the strip fills left to right while the run goes on,
   and the finished strip is the image on the shareable card.
   ============================================================ */

const Quiz = (() => {
    const stage = document.getElementById('play');
    const body = document.getElementById('quiz-body');
    const railL = document.getElementById('rail-l');
    const railR = document.getElementById('rail-r');
    const railAria = document.getElementById('rail-aria');
    const scoreEl = document.getElementById('score');
    const countEl = document.getElementById('frame-count');
    const shareStrip = document.getElementById('share-strip');

    // Gates of padding under the first question and over the last, so the
    // reel reaches both edges of the screen instead of running out mid-air.
    const LEAD = 4, TAIL = 6;

    let questions = [];
    let index = 0;
    let score = 0;
    let locked = false;          // true while a reveal is on screen
    let expecting = 0;           // how many questions we still expect to arrive
    let onFinish = () => {};

    let waitTimer = 0;           // bounds how long a reader sits on the skeleton
    const WAIT_CAP_MS = 9000;

    let frames = [];             // one per answered question: { url, correct }
    let pool = [];               // poster URLs the strip exposes frames from
    let poolAt = 0;

    const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
        c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

    function reset(handler) {
        questions = []; index = 0; score = 0; locked = false;
        frames = []; poolAt = 0;
        clearTimeout(waitTimer); waitTimer = 0;
        stage.style.setProperty('--exposure', '0');
        railL.innerHTML = ''; railR.innerHTML = '';
        drawShareStrip();
        // Start out expecting at least one question, so the empty question
        // list reads as "still loading" rather than "quiz already over".
        expecting = 1;
        onFinish = handler || (() => {});
        scoreEl.textContent = '0';
        loadPool();
        render();
    }

    /** Append questions; safe to call while the user is mid-quiz. */
    function add(list, final = true) {
        // Duplicate ids would double-count the score.
        const fresh = (list || []).filter(q => !questions.some(existing => existing.id === q.id));
        questions.push(...fresh);
        // `final` says whether more are still on the way. Phase-2 questions are
        // pulled several times as the scrape proceeds, so an early batch must
        // leave the run open rather than declaring the quiz over.
        expecting = final ? 0 : Math.max(expecting - fresh.length, 1);
        // Questions arrived, so the reader is no longer stranded.
        if (fresh.length) { clearTimeout(waitTimer); waitTimer = 0; }
        drawStrip();
        if (!locked && index < questions.length && body.querySelector('.waiting')) render();
    }

    /** Tell the quiz more questions are still on the way. */
    function expect(n) { expecting = n; if (body.querySelector('.waiting')) render(); }

    /* ── film strip ───────────────────────────────────────────── */

    /**
     * Posters for the strip. Only one question kind carries a poster of its
     * own, so the rest of the frames come from the library sample — the same
     * endpoint the landing wall uses.
     */
    async function loadPool() {
        let sid = '';
        try { sid = JSON.parse(localStorage.getItem('lbxw') || 'null')?.session || ''; } catch { /* private mode */ }
        try {
            const query = sid ? '&session=' + encodeURIComponent(sid) : '';
            const res = await fetch('/api/posters?n=64' + query);
            const data = await res.json();
            pool = (data.posters || []).filter(Boolean);
        } catch { return; }              // the reels fall back to empty gates
        // Frames exposed before the posters landed are still blank; refill them.
        frames.filter(shot => shot && !shot.url).forEach(shot => { shot.url = nextPoster(); });
        drawStrip();
    }

    /**
     * How lit the stage is, 0 to 1.
     *
     * Progress alone raises it a little; being right raises it a lot. A
     * reader who knows their own taste finishes the run on a fully exposed
     * screen, one who does not finishes it in the dark. One number written
     * once per answer: every layer derives from it and CSS does the rest.
     */
    function setExposure() {
        const total = Math.max(questions.length + expecting, frames.length, 1);
        const hits = frames.reduce((n, shot) => n + (shot && shot.correct ? 1 : 0), 0);
        stage.style.setProperty('--exposure',
            ((frames.length * 0.35 + hits * 0.65) / total).toFixed(3));
    }

    function nextPoster() {
        return pool.length ? pool[poolAt++ % pool.length] : null;
    }

    /**
     * Draw both reels.
     *
     * Every gate carries a poster from the moment it exists, unexposed; the
     * screen is therefore full before a single question is answered, and
     * answering develops one frame rather than adding one. Position is a
     * custom property the CSS reads — one write per answer, no per-frame JS.
     */
    function drawStrip() {
        const total = Math.max(questions.length + expecting, frames.length, 1);
        const slots = LEAD + total + TAIL;

        [railL, railR].forEach((track, side) => {
            while (track.children.length < slots) {
                const gate = document.createElement('div');
                gate.className = 'gate';
                gate.innerHTML = '<img alt="" loading="lazy" />';
                track.appendChild(gate);
            }
            while (track.children.length > slots) track.lastChild.remove();

            Array.from(track.children).forEach((gate, s) => {
                const i = s - LEAD;                  // question index, or padding
                const shot = i >= 0 && i < total ? frames[i] : null;
                const img = gate.firstElementChild;

                // An answered gate shows that answer's poster on both reels —
                // the same frame passing two sprockets. Padding and unanswered
                // gates take from the pool, offset per side so the two edges
                // are not a mirror.
                const url = (shot && shot.url)
                    || (pool.length ? pool[(s * 2 + side * 7) % pool.length] : '');
                if (url && img.getAttribute('src') !== url) img.src = url;

                // Toggling beats reassigning className: a gate still running
                // its exposure animation keeps the `pop` class.
                gate.classList.toggle('hit', !!shot && shot.correct);
                gate.classList.toggle('miss', !!shot && !shot.correct);
                gate.classList.toggle('now', !shot && i === index);
                if (shot && !gate.dataset.filled) {
                    gate.dataset.filled = '1';
                    gate.classList.add('pop');
                }
            });

            // The gate under the beam. Advancing it pulls the reel down.
            track.style.setProperty('--i', String(index + LEAD));
        });

        setExposure();
        if (railAria) {
            railAria.setAttribute('aria-valuenow', String(frames.length));
            railAria.setAttribute('aria-valuemax', String(total));
        }
        if (countEl) countEl.textContent = frames.length + '/' + total;
    }

    /**
     * A frame's contents: the poster, over its own number.
     *
     * The number is not decoration. html2canvas will only draw an image it is
     * allowed to read, and the Letterboxd CDN sends no Access-Control-Allow-Origin
     * header, so the posters are silently dropped when the share card is turned
     * into a PNG. Layering the poster over a numbered card means the download
     * still shows a strip of frames instead of a row of holes. Serving posters
     * from our own origin would make them survive the export.
     */
    function frameInner(shot, i) {
        return '<span class="frame-num">' + (i + 1) + '</span>'
            + (shot.url ? '<img src="' + esc(shot.url) + '" alt="" loading="lazy" />' : '');
    }

    /** The accumulated strip, rebuilt inside the shareable card. */
    function drawShareStrip() {
        if (!shareStrip) return;
        if (!frames.length) { shareStrip.innerHTML = ''; shareStrip.hidden = true; return; }

        // One long row reads as a strip; past eight frames each one gets too
        // thin, so it breaks into evenly sized rows.
        const rows = Math.ceil(frames.length / 8);
        const per = Math.ceil(frames.length / rows);
        let html = '';
        for (let start = 0; start < frames.length; start += per) {
            const row = frames.slice(start, start + per).map((shot, j) =>
                '<div class="frame ' + (shot.correct ? 'hit' : 'miss') + '">'
                + frameInner(shot, start + j) + '</div>').join('');
            html += '<div class="filmstrip is-static" style="--per:' + per + '">' + row + '</div>';
        }

        const hits = frames.filter(shot => shot.correct).length;
        html += '<p class="strip-legend">'
              + '<span><i class="ok"></i>' + hits + ' doğru</span>'
              + '<span><i class="no"></i>' + (frames.length - hits) + ' yanlış</span>'
              + '</p>';

        shareStrip.innerHTML = html;
        shareStrip.hidden = false;
    }

    /* ── questions ────────────────────────────────────────────── */

    /** Half-star glyphs for a rating option; '' when the value isn't a
        clean half-step (averages like 3.62 read better as a bare number). */
    function stars(value) {
        const v = parseFloat(String(value).replace(',', '.'));
        if (!Number.isFinite(v) || v <= 0 || v > 5) return '';
        if (Math.abs(v * 2 - Math.round(v * 2)) > 1e-9) return '';
        return '★'.repeat(Math.floor(v)) + (v % 1 ? '½' : '');
    }

    function option(kind, label, i) {
        const inner = (kind === 'rating' || kind === 'poster') && stars(label)
            ? `<span class="opt-stars">${stars(label)}</span>
               <span class="opt-num">${esc(label)}</span>`
            : `<span class="opt-label">${esc(label)}</span>`;
        return `<button class="opt" data-i="${i}">${inner}</button>`;
    }

    function render() {
        drawStrip();

        if (index >= questions.length) {
            if (expecting > 0) {
                // Skeleton, not a spinner: the wait is always the same
                // shape, so show the question that is about to arrive.
                body.innerHTML = `<div class="waiting">
                    <div class="skel skel-line"></div>
                    <div class="skel skel-line short"></div>
                    <div class="skel-opts">
                        <div class="skel"></div><div class="skel"></div>
                        <div class="skel"></div><div class="skel"></div>
                    </div>
                    <p>Film bilgilerin geliyor… birazdan devam ediyoruz.</p>
                </div>`;
                armWaitCap();
            } else {
                finish(score, questions.length);
            }
            return;
        }

        const q = questions[index];
        const kind = q.kind || 'plain';
        stage.dataset.accent = q.accent || 'amber';
        // The layout is picked by `kind`, so consecutive questions don't all
        // look like the same card. See _question() in quiz.py.
        body.dataset.kind = kind;

        const poster = q.poster
            ? `<img class="q-poster" src="${esc(q.poster)}" alt="" loading="lazy" />` : '';
        const hint = !q.hint?.length ? ''
            : kind === 'cast'
                ? `<ul class="q-cast">${q.hint.map(h => `<li>${esc(h)}</li>`).join('')}</ul>`
                : `<div class="q-hint">${q.hint.map(h => `<span>${esc(h)}</span>`).join('')}</div>`;

        const text = `
            <p class="q-eyebrow">${esc(q.eyebrow)}</p>
            <h2 class="q-prompt">${esc(q.prompt)}</h2>
            ${hint}
            <div class="options" id="opts">
                ${q.options.map((o, i) => option(kind, o, i)).join('')}
            </div>
            <div id="after"></div>`;

        // On a poster question the film is the subject, so it sits beside the
        // question rather than above it.
        body.innerHTML = kind === 'poster' && poster
            ? `<div class="q-split">${poster}<div class="q-col">${text}</div></div>`
            : poster + text;

        body.querySelectorAll('.opt').forEach(btn =>
            btn.addEventListener('click', () => answer(Number(btn.dataset.i))));
    }

    /**
     * A bounded wait. Phase-2 questions may still be on their way, but a
     * reader must never be parked on a skeleton indefinitely: on a cold
     * library the metadata that would build them can be minutes out. If they
     * do not arrive in time the run ends with the questions it has, and the
     * analysis fills itself in later either way.
     */
    function armWaitCap() {
        if (waitTimer) return;
        waitTimer = setTimeout(() => {
            waitTimer = 0;
            if (index >= questions.length) { expecting = 0; render(); }
        }, WAIT_CAP_MS);
    }

    function answer(choice) {
        if (locked) return;
        locked = true;

        const q = questions[index];
        const correct = choice === q.answer;
        if (correct) { score += 1; scoreEl.textContent = String(score); }

        // Expose this question's frame. A poster question hands over its own
        // film; every other kind takes the next one out of the library.
        frames[index] = { url: q.poster || nextPoster(), correct };
        drawStrip();

        body.querySelectorAll('.opt').forEach((btn, i) => {
            btn.disabled = true;
            if (i === q.answer) btn.classList.add('correct');
            else if (i === choice) btn.classList.add('wrong');
            else btn.classList.add('faded');
        });

        const last = index === questions.length - 1 && expecting === 0;
        document.getElementById('after').innerHTML = `
            <div class="reveal"><p>${esc(q.reveal)}</p></div>
            <div class="quiz-actions">
                <button class="btn-next" id="next">${last ? 'Sonucu gör' : 'Devam'} →</button>
            </div>`;
        document.getElementById('next').addEventListener('click', next);
    }

    function next() {
        locked = false;
        index += 1;
        render();
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    /** Hand the strip over to the share card, then release the run. */
    function finish(finalScore, total, skipped = false) {
        drawShareStrip();
        onFinish(finalScore, total, skipped);
    }

    /** Abandon the quiz and go straight to the results. */
    function skip() {
        expecting = 0;
        finish(score, index, true);
    }

    return { reset, add, expect, skip,
             get score() { return score; },
             get total() { return questions.length; } };
})();
