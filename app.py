import os
import time
from collections import defaultdict
from threading import Lock
from urllib.parse import urlsplit
from flask import Flask, jsonify, render_template, request
from yt_dlp import YoutubeDL

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 4096
LAST = defaultdict(list)
LOCK = Lock()
VALID_HOSTS = {'youtube.com','www.youtube.com','m.youtube.com','music.youtube.com','youtu.be','www.youtube-nocookie.com'}

@app.after_request
def headers(resp):
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['Referrer-Policy'] = 'no-referrer'
    resp.headers['Content-Security-Policy'] = "frame-ancestors *"
    return resp

@app.get('/')
def index():
    return render_template('index.html')

@app.get('/health')
def health():
    return jsonify(status='ok')

@app.post('/api/formats')
def formats():
    data = request.get_json(silent=True) or {}
    url = str(data.get('url', '')).strip()
    try:
        p = urlsplit(url)
        if p.scheme != 'https' or p.hostname not in VALID_HOSTS or p.username or p.password or p.port:
            raise ValueError()
    except (ValueError, TypeError):
        return jsonify(error='لینک معتبر HTTPS یوتیوب وارد کنید.'), 400
    ip = request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()[:64]
    now = time.monotonic()
    with LOCK:
        LAST[ip] = [t for t in LAST[ip] if now-t < 60]
        if len(LAST[ip]) >= 5:
            return jsonify(error='درخواست زیاد است؛ یک دقیقه بعد امتحان کنید.'), 429
        LAST[ip].append(now)
    opts = {
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'skip_download': True,
        'socket_timeout': 12,
        'retries': 1,
        'extractor_retries': 1,
        'ignoreerrors': False,
        'cachedir': False,
    }
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if not info or info.get('_type') == 'playlist':
            return jsonify(error='فقط لینک یک ویدیوی تکی پشتیبانی می‌شود.'), 400
        items = []
        used = set()
        for f in info.get('formats') or []:
            link = f.get('url')
            if not isinstance(link, str) or not link.startswith('https://') or f.get('has_drm'):
                continue
            vc = f.get('vcodec') not in (None, 'none')
            ac = f.get('acodec') not in (None, 'none')
            if not vc and not ac:
                continue
            h = f.get('height') or 0
            quality = f'{h}p' if vc and h else ('ویدیو' if vc else 'صدا')
            kind = 'ویدیو + صدا' if vc and ac else ('فقط ویدیو (بدون صدا)' if vc else 'فقط صدا')
            key = (quality, kind, f.get('ext'), f.get('format_id'))
            if key in used:
                continue
            used.add(key)
            items.append({'quality': quality, 'type': kind, 'ext': f.get('ext') or '', 'height': h, 'url': link})
        items.sort(key=lambda x: (x['height'], x['type'] == 'ویدیو + صدا'), reverse=True)
        return jsonify(title=info.get('title') or 'ویدیو', formats=items[:100])
    except Exception as exc:
        # Avoid leaking server paths and sensitive details in public error messages.
        message = str(exc).lower()
        if 'sign in' in message or 'bot' in message:
            error = 'یوتیوب درخواست سرور را محدود کرده است (Bot verification).'
        elif 'timed out' in message or 'timeout' in message:
            error = 'اتصال سرور به یوتیوب زمان‌بر شد یا قطع است.'
        elif 'unsupported url' in message:
            error = 'این نشانی یا نوع ویدیو پشتیبانی نمی‌شود.'
        else:
            error = 'استخراج کیفیت‌ها ناموفق بود. ممکن است ویدیو محدود باشد یا IP سرور توسط یوتیوب محدود شده باشد.'
        app.logger.warning('yt-dlp failed: %s', str(exc)[:500])
        return jsonify(error=error), 502

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', '10000')))
