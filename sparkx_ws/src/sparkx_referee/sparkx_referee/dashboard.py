"""Organizer web dashboard (stdlib HTTP, no extra deps). Open http://<host>:8080

Shows live score, timer, events, robot track over the ground-truth map, survivors, hints, the
stack-integrity result and rule violations. It displays secret ground truth, so:

  * bound to 127.0.0.1 (default) everything is readable locally, the admin buttons need the token;
  * bound to any other address (dashboard_host:=0.0.0.0) the data endpoints (/state, /gt.png) need
    the admin token as well - without it only the empty page and the sponsor logos are served.

The token is printed in the referee log (parameter dashboard_token, random when not set)."""
import io
import json
import math
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
LOOPBACK = ('127.0.0.1', 'localhost', '::1')
NAVY = (36, 40, 96)
OFFWHITE = (240, 239, 242)
CRIMSON = (229, 2, 39)


def brand_dir():
    try:
        from ament_index_python.packages import get_package_share_directory
        d = os.path.join(get_package_share_directory('sparkx_worlds'), 'models', 'sparkx_media', 'brand')
        if os.path.isdir(d):
            return d
    except Exception:
        pass
    d = os.path.abspath(os.path.join(HERE, '..', '..', 'sparkx_worlds', 'models', 'sparkx_media', 'brand'))
    return d if os.path.isdir(d) else None


def brand_images(height=96):
    """{'airoot': png bytes, 'stemlearn': png bytes} for a dark header, or {} if the artwork is missing."""
    out = {}
    d = brand_dir()
    if not d:
        return out
    from PIL import Image

    def png(im):
        b = io.BytesIO()
        im.save(b, 'PNG')
        return b.getvalue()
    try:
        m = Image.open(os.path.join(d, 'airoot_logo_mask.png')).convert('L')
        m = m.resize((max(1, m.width * height // m.height), height), Image.LANCZOS)
        im = Image.new('RGBA', m.size, CRIMSON + (0,))
        im.putalpha(m)
        out['airoot'] = png(im)
    except Exception:
        pass
    try:
        a = np.asarray(Image.open(os.path.join(d, 'stemlearn_logo.png')).convert('RGBA'), np.float32).copy()
        ys, xs = np.nonzero(a[..., 3] > 8)
        a = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        k = np.clip(1.0 - np.linalg.norm(a[..., :3] - np.asarray(NAVY, np.float32), axis=2) / 70.0, 0, 1)[..., None]
        a[..., :3] = a[..., :3] * (1 - k) + np.asarray(OFFWHITE, np.float32) * k     # navy letters -> off-white
        im = Image.fromarray(a.astype(np.uint8), 'RGBA')
        h = int(height * 0.72)
        out['stemlearn'] = png(im.resize((max(1, im.width * h // im.height), h), Image.LANCZOS))
    except Exception:
        pass
    return out


class Dashboard:
    def __init__(self, node, port):
        self.node = node
        self.token = str(node.declare_parameter('dashboard_token', '').value or '') or secrets.token_hex(4)
        host = str(node.declare_parameter('dashboard_host', '127.0.0.1').value or '127.0.0.1')
        self.open = host in LOOPBACK          # data readable without the token only on loopback
        self.png = self._gt_png()
        self.brand = brand_images()
        dash = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def send(self, code, body, ctype='application/json', cache='no-store'):
                self.send_response(code)
                self.send_header('Content-Type', ctype)
                self.send_header('Cache-Control', cache)
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers()
                self.wfile.write(body if isinstance(body, bytes) else body.encode())

            def token_ok(self, url):
                given = (parse_qs(url.query).get('token') or [self.headers.get('X-SparkX-Token', '')])[0]
                return secrets.compare_digest(str(given), dash.token)

            def do_GET(self):
                url = urlparse(self.path)
                if url.path in ('/', '/index.html'):
                    with open(os.path.join(HERE, 'web', 'index.html'), encoding='utf-8') as f:
                        return self.send(200, f.read(), 'text/html; charset=utf-8')
                if url.path.startswith('/brand/'):
                    img = dash.brand.get(url.path[len('/brand/'):].split('.')[0])
                    return self.send(200, img, 'image/png', 'max-age=3600') if img else self.send(404, '{}')
                if url.path in ('/state', '/gt.png'):
                    if not (dash.open or self.token_ok(url)):
                        return self.send(403, '{"error":"admin token required"}')
                    if url.path == '/gt.png':
                        return self.send(200, dash.png, 'image/png')
                    return self.send(200, json.dumps(dash.state()))
                self.send(404, '{}')

            def do_POST(self):
                parts = urlparse(self.path).path.strip('/').split('/')
                if len(parts) != 3 or parts[0] != 'admin' or not secrets.compare_digest(parts[2], dash.token):
                    return self.send(403, '{"error":"bad token"}')
                n = dash.node
                ref = n.ref
                fn = {'start': n.request_start, 'pause': ref.pause, 'abort': lambda: ref.finish('ABORTED'),
                      'intervention': lambda: ref.intervention('organizer intervention (dashboard)')}.get(parts[1])
                if fn is None:
                    return self.send(404, '{}')
                with n.lock:
                    ev = fn()
                n.emit(ev)
                out = dict(ref.status(), start_block=n.start_block)
                self.send(200, json.dumps(out))

        self.httpd = ThreadingHTTPServer((host, port), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        node.get_logger().info(f'dashboard: http://{host}:{port}  (admin token: {self.token}'
                               f'{"" if self.open else "; also required to VIEW because the host is not loopback"})')

    def _gt_png(self):
        from PIL import Image
        r = self.node.ref
        img = np.full(r.gt_occ.shape, 58, np.uint8)
        img[r.gt_reach > 0] = 214
        img[r.gt_occ > 0] = 24
        b = io.BytesIO()
        Image.fromarray(img[::-1]).save(b, 'PNG')
        return b.getvalue()

    def state(self):
        n = self.node
        r = n.ref
        p = r.pose['p'] if r.pose else None
        integ = n.integrity
        return dict(
            status=r.status(), origin=r.gt_origin, res=r.gt_res, shape=list(r.gt_occ.shape),
            robot=None if p is None else dict(x=p.x, y=p.y, yaw=p.yaw),
            track=[[t[1], t[2]] for t in r.trajectory[-2000:]],
            survivors=[dict(id=s['id'], x=s['x'], y=s['y'], rescued=s['rescued']) for s in r.survivors.values()],
            fires=[dict(x=f['x'], y=f['y'], r=f['hazard_radius']) for f in r.sc['fires']],
            hints=[dict(id=h, text=r.hints[h]['text'], source=r.hints[h]['source'], target=r.hints[h]['target'])
                   for h in r.released],
            aftershocks=[dict(x=a['x'], y=a['y'], yaw=a['yaw'], w=a['width']) for a in r.aftershocks_done],
            events=r.events[-40:][::-1],
            behavior=None if n.last_behavior is None else dict(state=n.last_behavior.state,
                                                               detail=n.last_behavior.detail),
            base=dict(x=r.start.x, y=r.start.y, r=r.rs['base_zone_radius_m'], yaw=r.start.yaw),
            integrity=dict(status=integ['status'], note=integ['note'], fingerprint=integ['digest'][:16],
                           files=integ['files'], changed=(integ['modified'] + integ['added'] + integ['removed'])[:12]),
            lockdown=dict(competition=n.competition, require_integrity=n.require_integrity,
                          policy=n.violation_policy, start_block=n.start_block, disqualified=n.disqualified),
            violations=n.violations[-20:],
            log_dir=n.log_dir, pi=math.pi)
