/* ============================================================
   Quiz engine
   Renders server-built questions one at a time, scores answers,
   and can accept more questions mid-run (the "full" phase lands
   once the metadata scrape finishes).
   ============================================================ */

const Quiz = (() => {
    const stage = document.getElementById('play');
    const body = document.getElementById('quiz-body');
    const rail = document.getElementById('rail');
    const scoreEl = document.getElementById('score');

    let questions = [];
    let index = 0;
    let score = 0;
    let locked = false;          // true while a reveal is on screen
    let expecting = 0;           // how many questions we still expect to arrive
    let onFinish = () => {};

    const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
        c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

    function reset(handler) {
        questions = []; index = 0; score = 0; locked = false;
        // Start out expecting at least one question, so the empty question
        // list reads as "still loading" rather than "quiz already over".
        expecting = 1;
        onFinish = handler || (() => {});
        scoreEl.textContent = '0';
        render();
    }

    /** Append questions; safe to call while the user is mid-quiz. */
    function add(list) {
        const fresh = (list || []).filter(q => !questions.some(existing => existing.id === q.id));
        questions.push(...fresh);
        expecting = 0;
        drawRail();
        if (!locked && index < questions.length && body.querySelector('.waiting')) render();
    }

    /** Tell the quiz more questions are still on the way. */
    function expect(n) { expecting = n; if (body.querySelector('.waiting')) render(); }

    function drawRail() {
        const total = Math.max(questions.length + expecting, 1);
        rail.innerHTML = Array.from({ length: total },
            (_, i) => `<span class="${i < index ? 'seen' : ''}"></span>`).join('');
    }

    function render() {
        drawRail();

        if (index >= questions.length) {
            if (expecting > 0) {
                body.innerHTML = `<div class="waiting">
                    <div class="spin"></div>
                    <p>Film bilgilerin geliyor… birazdan devam ediyoruz.</p>
                </div>`;
            } else {
                onFinish(score, questions.length);
            }
            return;
        }

        const q = questions[index];
        stage.dataset.accent = q.accent || 'amber';

        const poster = q.poster
            ? `<img class="q-poster" src="${esc(q.poster)}" alt="" loading="lazy" />` : '';
        const hint = q.hint?.length
            ? `<div class="q-hint">${q.hint.map(h => `<span>${esc(h)}</span>`).join('')}</div>` : '';

        body.innerHTML = `
            <p class="q-eyebrow">${esc(q.eyebrow)}</p>
            ${poster}
            <h2 class="q-prompt">${esc(q.prompt)}</h2>
            ${hint}
            <div class="options" id="opts">
                ${q.options.map((o, i) =>
                    `<button class="opt" data-i="${i}">${esc(o)}</button>`).join('')}
            </div>
            <div id="after"></div>`;

        body.querySelectorAll('.opt').forEach(btn =>
            btn.addEventListener('click', () => answer(Number(btn.dataset.i))));
    }

    function answer(choice) {
        if (locked) return;
        locked = true;

        const q = questions[index];
        const correct = choice === q.answer;
        if (correct) { score += 1; scoreEl.textContent = String(score); }

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

    /** Abandon the quiz and go straight to the results. */
    function skip() {
        expecting = 0;
        onFinish(score, index, true);
    }

    return { reset, add, expect, skip,
             get score() { return score; },
             get total() { return questions.length; } };
})();
