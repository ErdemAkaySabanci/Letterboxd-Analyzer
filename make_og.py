"""Render dashboard/og.png — the LinkedIn/social preview card.

Built as HTML and screenshotted rather than drawn, so it inherits the site's
own type, colour tokens and poster treatment instead of approximating them.
Run from the repo root when the card needs to change; the PNG is committed.
"""
import io, json, os, random

POSTERS_WANTED = 24

cache = json.load(io.open('film_cache.json', encoding='utf-8'))
posters = [f['poster'] for f in cache.values()
           if isinstance(f, dict) and f.get('poster')]
random.Random(7).shuffle(posters)
posters = posters[:POSTERS_WANTED]

tiles = ''.join(f'<img src="{u}" />' for u in posters)

HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8" />
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,400;12..96,700;12..96,800&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet" />
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body {
      width:1200px; height:630px; overflow:hidden; position:relative;
      background:#0A0A0F; color:#F2EFE6;
      font-family:'Inter',system-ui,sans-serif;
  }
  .wall {
      position:absolute; inset:-6%;
      display:grid; grid-template-columns:repeat(8,1fr); gap:10px;
      transform:rotate(-8deg) scale(1.22); opacity:.20;
  }
  .wall img { width:100%; aspect-ratio:2/3; object-fit:cover; border-radius:6px; }
  .veil {
      position:absolute; inset:0;
      background:
        radial-gradient(ellipse 66% 80% at 32% 50%,
                 #0A0A0F 44%, rgba(10,10,15,.93) 68%, rgba(10,10,15,.72) 100%),
        linear-gradient(90deg, #0A0A0F 22%, rgba(10,10,15,.55) 62%, rgba(10,10,15,.78) 100%);
  }
  .inner { position:relative; height:100%; padding:74px 80px; display:flex; flex-direction:column; }
  .brand {
      display:flex; align-items:center; gap:14px;
      font-family:'Bricolage Grotesque',sans-serif;
      font-size:17px; font-weight:700; letter-spacing:.20em; text-transform:uppercase;
      color:#8E8EA3;
  }
  .dots { display:flex; gap:7px; }
  .dots i { width:11px; height:11px; border-radius:50%; display:block; }
  .rule { width:1px; height:15px; background:rgba(255,255,255,.18); }
  .sub { font-weight:400; color:rgba(142,142,163,.82); }
  h1 {
      margin-top:auto;
      font-family:'Bricolage Grotesque',sans-serif;
      font-size:92px; font-weight:800; line-height:.94; letter-spacing:-.045em;
      max-width:15ch;
  }
  h1 em { font-style:normal; color:#FFB020; }
  p.lede {
      margin-top:26px; font-size:26px; line-height:1.4; color:#8E8EA3; max-width:30ch;
  }
  .foot {
      margin-top:auto; display:flex; align-items:center; gap:18px;
      font-size:20px; color:#8E8EA3;
  }
  .pill {
      padding:9px 20px; border-radius:999px;
      border:1px solid rgba(255,255,255,.13); background:rgba(255,255,255,.05);
      font-weight:600; color:#F2EFE6;
  }
</style></head>
<body>
  <div class="wall">__TILES__</div>
  <div class="veil"></div>
  <div class="inner">
    <div class="brand">
      <span class="dots"><i style="background:#00E5A0"></i><i style="background:#FFB020"></i><i style="background:#FF3B5C"></i></span>
      Close-Up <span class="rule"></span> <span class="sub">for Letterboxd</span>
    </div>
    <h1>How well do you know <em>your own taste?</em></h1>
    <p class="lede">A test built entirely from films you actually watched.</p>
    <div class="foot">
      <span class="pill">Upload your export</span>
      <span>6 chapters on what your library says about you</span>
    </div>
  </div>
</body></html>"""

html = HTML.replace('__TILES__', tiles)
tmp = os.path.join(os.environ.get('TEMP', '.'), 'og_card.html')
io.open(tmp, 'w', encoding='utf-8').write(html)

from playwright.sync_api import sync_playwright

out = os.path.join('dashboard', 'og.png')
with sync_playwright() as pw:
    for launch in (lambda: pw.chromium.launch(),
                   lambda: pw.chromium.launch(channel='chrome')):
        try:
            browser = launch()
            break
        except Exception as exc:
            last = exc
    else:
        raise SystemExit(f'no browser available: {last}')

    page = browser.new_page(viewport={'width': 1200, 'height': 630},
                            device_scale_factor=1)
    page.goto('file:///' + tmp.replace('\\', '/'))
    page.wait_for_timeout(3500)          # fonts + poster images
    page.screenshot(path=out)
    browser.close()

from PIL import Image
img = Image.open(out).convert('RGB')
img.save(out, 'PNG', optimize=True)
print('wrote %s (%.0f KB)' % (out, os.path.getsize(out) / 1024))
