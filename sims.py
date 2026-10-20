"""Залипательные симуляции шариков.

Всё считается в условном квадрате 1000x1000 (центр 500,500), а при отрисовке
масштабируется под размер оверлея. Физика идёт с шагом 1/60 c.
Каждая симуляция сама понимает, когда она «закончилась» (self.done),
после чего Player включает следующую случайную.
"""
import colorsys
import math
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont

TAU = math.pi * 2
C = 500.0
DT = 1 / 60
SCALE = [0, 2, 4, 7, 9, 12, 14, 16, 19, 21, 24]


def hsl(h, s, l, a=255):
    r, g, b = colorsys.hls_to_rgb((h % 360) / 360, l, s)
    return (int(r * 255), int(g * 255), int(b * 255), a)


def with_alpha(col, a):
    return (col[0], col[1], col[2], max(0, min(255, int(a))))


_fonts = {}


def font(px):
    px = max(8, int(px))
    if px not in _fonts:
        try:
            _fonts[px] = ImageFont.load_default(size=px)
        except TypeError:  # старый Pillow
            _fonts[px] = ImageFont.load_default()
    return _fonts[px]


class Ball:
    def __init__(self, x, y, vx, vy, r):
        self.x, self.y, self.vx, self.vy, self.r = x, y, vx, vy, r

    def move(self, g=0.0, max_v=None):
        self.vy += g
        if max_v:
            v = math.hypot(self.vx, self.vy)
            if v > max_v:
                self.vx *= max_v / v
                self.vy *= max_v / v
        self.x += self.vx
        self.y += self.vy

    def dist(self):
        return math.hypot(self.x - C, self.y - C)


class Sim:
    max_time = 35.0       # страховка, если шарик «застрял»
    particle_gravity = 0.15

    def __init__(self, rng: random.Random, sound):
        self.rng = rng
        self._sound = sound
        self.t = 0.0
        self.done = False
        self.end_timer = None
        self.parts = []
        self.note = rng.randrange(5)
        h = rng.uniform(0, 360)
        self.h, self.h2 = h, (h + rng.uniform(30, 180)) % 360
        self.ball_col = hsl(h + 180, 1.0, 0.7)
        self.setup()

    # --- helpers ---
    def rnd(self, a, b):
        return self.rng.uniform(a, b)

    def grad(self, t):
        return hsl(self.h + (self.h2 - self.h) * t, 0.9, 0.45 + t * 0.4)

    def next_note(self, base):
        f = base * 2 ** (SCALE[self.note % len(SCALE)] / 12)
        self.note += 1
        return f

    def sound(self, freq, dur=0.08, wave="sine", vol=0.25):
        self._sound(freq, dur, wave, vol)

    def burst(self, x, y, col, n=40, sp=12):
        for _ in range(n):
            a, s = self.rng.uniform(0, TAU), self.rng.uniform(0, sp)
            self.parts.append([x, y, math.cos(a) * s, math.sin(a) * s, 1.0, col, self.rng.uniform(3, 8)])

    def finish(self, delay=1.0):
        if self.end_timer is None:
            self.end_timer = delay

    def bounce_circle(self, b, R, kick=1.0, mult=1.0):
        """Отскок шарика от внутренней стенки круга радиуса R. Возвращает нормаль или None."""
        dx, dy = b.x - C, b.y - C
        d = math.hypot(dx, dy) or 1e-6
        lim = R - b.r
        if d <= lim:
            return None
        nx, ny = dx / d, dy / d
        dot = b.vx * nx + b.vy * ny
        if dot > 0:
            b.vx = (b.vx - 2 * dot * nx) * mult
            b.vy = (b.vy - 2 * dot * ny) * mult
        k = self.rng.uniform(-kick, kick)
        b.vx += -ny * k
        b.vy += nx * k
        b.x, b.y = C + nx * lim, C + ny * lim
        return nx, ny

    # --- lifecycle ---
    def setup(self):
        pass

    def step(self):
        pass

    def draw(self, d: ImageDraw.ImageDraw, k: float):
        pass

    def update(self):
        self.t += DT
        if self.end_timer is None or self.end_timer > 0.3:
            self.step()
        alive = []
        for p in self.parts:
            p[0] += p[2]
            p[1] += p[3]
            p[3] += self.particle_gravity
            p[4] -= 0.012
            if p[4] > 0:
                alive.append(p)
        self.parts = alive
        if self.end_timer is not None:
            self.end_timer -= DT
            if self.end_timer <= 0:
                self.done = True
        elif self.t > self.max_time:
            self.finish(0.5)

    def render(self, d, k):
        self.draw(d, k)
        for x, y, _, _, life, col, r in self.parts:
            s = r * k
            d.rectangle([x * k, y * k, x * k + s, y * k + s], fill=with_alpha(col, life * 255))

    # --- drawing helpers ---
    @staticmethod
    def circle(d, k, x, y, r, fill=None, outline=None, width=1):
        d.ellipse([(x - r) * k, (y - r) * k, (x + r) * k, (y + r) * k], fill=fill, outline=outline,
                  width=max(1, int(width * k)))

    def draw_ball(self, d, k, b, col, trail=None):
        self.circle(d, k, b.x, b.y, b.r * 2.2, fill=with_alpha(col, 35))
        self.circle(d, k, b.x, b.y, b.r * 1.5, fill=with_alpha(col, 70))
        self.circle(d, k, b.x, b.y, b.r, fill=col)
        self.circle(d, k, b.x - b.r * 0.3, b.y - b.r * 0.3, b.r * 0.35, fill=(255, 255, 255, 200))


# ---------------------------------------------------------------- режимы

class Rings(Sim):
    """Шарик выбивается из вращающихся колец со щелью (как в исходном видео)."""
    title = "Побег из колец"
    max_time = 50.0
    gravity = (0.25, 0.5)

    def setup(self):
        n = self.rng.randint(13, 22)
        inner, outer = self.rnd(70, 110), self.rnd(400, 465)
        gap = self.rnd(0.45, 0.8)
        self.rings = []
        for i in range(n):
            t = i / (n - 1)
            self.rings.append(dict(
                r=inner + (outer - inner) * t, w=(outer - inner) / n * self.rnd(0.45, 0.8),
                start=self.rnd(0, TAU), gap=gap,
                spd=self.rng.choice((-1, 1)) * self.rnd(0.006, 0.03) * (1 + t if self.rng.random() < 0.3 else 1),
                col=self.grad(t), alive=True))
        self.b = Ball(C, C, self.rnd(-8, 8), self.rnd(-11, -5), self.rnd(13, 18))
        self.g = self.rnd(*self.gravity)
        self.mult = self.rnd(1.0, 1.03)
        self.max_v = self.rnd(16, 24)
        self.wear = self.rnd(0.025, 0.05)
        self.trail = []

    def step(self):
        b = self.b
        b.move(self.g, self.max_v)
        ring = next((r for r in self.rings if r["alive"]), None)
        if ring and self.t > 35 and int(self.t * 60) % 8 == 0:
            self.break_ring(ring)  # финальный каскад: кольца лопаются одно за другим
            ring = next((r for r in self.rings if r["alive"]), None)
        if ring:
            dx, dy = b.x - C, b.y - C
            d = math.hypot(dx, dy)
            if d > ring["r"] - ring["w"] / 2 - b.r:
                a = (math.atan2(dy, dx) - ring["start"]) % TAU
                if a < ring["gap"]:
                    if d > ring["r"] + ring["w"] / 2:
                        self.break_ring(ring)
                else:
                    if self.bounce_circle(b, ring["r"] - ring["w"] / 2, 1.5, self.mult):
                        self.sound(self.next_note(220))
                        ring["gap"] = min(ring["gap"] + self.wear * (1 + self.t / 7), 2.6)  # кольцо «изнашивается»
                        v = math.hypot(b.vx, b.vy)
                        if v < self.max_v * 0.8:  # не даём шарику «укатиться» и заглохнуть
                            b.vx *= self.max_v * 0.8 / v
                            b.vy *= self.max_v * 0.8 / v
        elif b.dist() > 560:
            self.burst(min(max(b.x, 20), 980), min(max(b.y, 20), 980), self.ball_col, 80, 14)
            self.sound(880, 0.5, "triangle", 0.3)
            self.finish(1.2)
        for r in self.rings:
            r["start"] += r["spd"]

    def break_ring(self, ring):
        ring["alive"] = False
        for i in range(24):
            a = ring["start"] + ring["gap"] + (TAU - ring["gap"]) * i / 24
            self.burst(C + math.cos(a) * ring["r"], C + math.sin(a) * ring["r"], ring["col"], 6, 10)
        self.sound(self.next_note(330) * 2, 0.25, "triangle", 0.3)

    def draw(self, d, k):
        for r in self.rings:
            if not r["alive"]:
                continue
            ro = r["r"] + r["w"] / 2
            d.arc([(C - ro) * k, (C - ro) * k, (C + ro) * k, (C + ro) * k],
                  math.degrees(r["start"] + r["gap"]), math.degrees(r["start"] + TAU),
                  fill=r["col"], width=max(1, int(r["w"] * k)))
        self.draw_ball(d, k, self.b, self.ball_col, self.trail)


class Spiral(Rings):
    """Много тонких колец со смещёнными щелями — получается спиральный лабиринт."""
    title = "Спиральный лабиринт"

    def setup(self):
        n = self.rng.randint(24, 36)
        step = self.rnd(0.15, 0.4)
        gap = self.rnd(0.55, 0.85)
        sign = self.rng.choice((-1, 1))
        self.rings = []
        for i in range(n):
            t = i / n
            self.rings.append(dict(r=60 + t * 400, w=6, start=i * step, gap=gap,
                                   spd=sign * (0.012 + t * 0.004), col=self.grad(t), alive=True))
        self.b = Ball(C, C, self.rnd(-6, 6), self.rnd(-6, 6), 11)
        self.g = 0.0
        self.mult = 1.02
        self.max_v = self.rnd(16, 22)
        self.wear = self.rnd(0.025, 0.05)
        self.trail = []


class Hex(Sim):
    """Шарик в крутящемся многоугольнике с дыркой в одной стороне."""
    title = "Крутящийся многоугольник"

    def setup(self):
        self.n = self.rng.choice((4, 5, 6, 6, 8))
        self.R = self.rnd(330, 430)
        self.rot = self.rnd(0, TAU)
        self.spd = self.rng.choice((-1, 1)) * self.rnd(0.006, 0.02)
        self.gapf = self.rnd(0.18, 0.35)
        self.b = Ball(C, C, self.rnd(-7, 7), self.rnd(-8, -3), self.rnd(14, 22))
        self.g = self.rnd(0.3, 0.5)
        self.col = hsl(self.h2, 0.9, 0.6)
        self.trail = []
        self.escaped = False

    def segments(self):
        pts = [(C + math.cos(self.rot + i * TAU / self.n) * self.R,
                C + math.sin(self.rot + i * TAU / self.n) * self.R) for i in range(self.n + 1)]
        segs = []
        for i in range(self.n):
            (x0, y0), (x1, y1) = pts[i], pts[i + 1]
            if i == 0:
                a, bb = 0.5 - self.gapf / 2, 0.5 + self.gapf / 2
                segs.append(((x0, y0), (x0 + (x1 - x0) * a, y0 + (y1 - y0) * a)))
                segs.append(((x0 + (x1 - x0) * bb, y0 + (y1 - y0) * bb), (x1, y1)))
            else:
                segs.append(((x0, y0), (x1, y1)))
        return segs

    def step(self):
        b = self.b
        b.move(self.g, 22)
        self.rot += self.spd
        if self.escaped:
            return
        for (x0, y0), (x1, y1) in self.segments():
            ex, ey = x1 - x0, y1 - y0
            L2 = ex * ex + ey * ey
            t = max(0.0, min(1.0, ((b.x - x0) * ex + (b.y - y0) * ey) / L2))
            qx, qy = x0 + ex * t, y0 + ey * t
            dx, dy = b.x - qx, b.y - qy
            dist = math.hypot(dx, dy)
            if 1e-6 < dist < b.r:
                nx, ny = dx / dist, dy / dist
                dot = b.vx * nx + b.vy * ny
                if dot < 0:
                    b.vx -= 2 * dot * nx
                    b.vy -= 2 * dot * ny
                    # стенка вращается — подкручиваем шарик по касательной
                    b.vx += -ny * self.spd * 60 * self.rnd(0.5, 1.5)
                    b.vy += nx * self.spd * 60 * self.rnd(0.5, 1.5)
                    self.sound(self.next_note(247), 0.1, "sine", 0.25)
                b.x, b.y = qx + nx * b.r, qy + ny * b.r
        if b.dist() > self.R + b.r + 10:
            self.escaped = True
            for (x0, y0), (x1, y1) in self.segments():
                for f in (0.2, 0.5, 0.8):
                    self.burst(x0 + (x1 - x0) * f, y0 + (y1 - y0) * f, self.col, 10, 10)
            self.sound(660, 0.6, "triangle", 0.3)
            self.finish(1.5)

    def draw(self, d, k):
        if not self.escaped:
            w = max(2, int(14 * k))
            for (x0, y0), (x1, y1) in self.segments():
                d.line([x0 * k, y0 * k, x1 * k, y1 * k], fill=self.col, width=w)
                self.circle(d, k, x0, y0, 7, fill=self.col)
                self.circle(d, k, x1, y1, 7, fill=self.col)
        self.draw_ball(d, k, self.b, self.ball_col, self.trail)


class Grow(Sim):
    """Шарик растёт от каждого удара, пока не заполнит круг."""
    title = "Шарик растёт"

    def setup(self):
        self.R0 = 440
        self.b = Ball(C, C - 200, self.rnd(-8, 8), 0, 14)
        self.g = self.rnd(0.4, 0.65)
        self.grow = self.rnd(7, 12)
        self.hits = 0

    def col(self):
        return hsl(self.h + self.hits * 7, 1.0, 0.6)

    def step(self):
        b = self.b
        b.move(self.g)
        n = self.bounce_circle(b, self.R0, 1.0)
        if n and self.end_timer is None:
            self.hits += 1
            b.r += self.grow
            self.sound(self.next_note(196), 0.12, "square", 0.12)
            self.burst(C + n[0] * self.R0, C + n[1] * self.R0, self.col(), 12, 6)
            if b.r >= self.R0 - 6:
                b.r = self.R0 - 6
                self.burst(C, C, self.col(), 300, 22)
                self.sound(880, 0.6, "sawtooth", 0.25)
                self.b.vx = self.b.vy = 0
                self.g = 0
                self.finish(1.2)

    def draw(self, d, k):
        self.circle(d, k, C, C, self.R0 + 6, outline=hsl(self.h2, 0.9, 0.6), width=10)
        if self.end_timer is None:
            self.draw_ball(d, k, self.b, self.col())
        d.text((C * k, 30 * k), str(self.hits), fill=(255, 255, 255, 255), font=font(56 * k), anchor="mm")


class Duel(Sim):
    """Два шарика бьются, у одного шипы — у кого кончится HP."""
    title = "Битва шариков"
    particle_gravity = 0.0

    def setup(self):
        self.R0 = 440
        self.balls = []
        for x, h in ((C - 150, self.h), (C + 150, self.h2)):
            b = Ball(x, C, self.rnd(-12, 12), self.rnd(-12, 12), 60)
            b.hp, b.col, b.dead = 100, hsl(h, 1.0, 0.6), False
            self.balls.append(b)
        self.spike = self.rng.randrange(2)

    def step(self):
        for b in self.balls:
            b.move()
            if self.bounce_circle(b, self.R0, 0.3):
                self.sound(self.next_note(260), 0.06)
        a, b = self.balls
        if a.dead or b.dead:
            return
        dx, dy = b.x - a.x, b.y - a.y
        d = math.hypot(dx, dy) or 1e-6
        if d < a.r + b.r:
            nx, ny = dx / d, dy / d
            p = (a.vx - b.vx) * nx + (a.vy - b.vy) * ny
            if p > 0:
                a.vx -= p * nx; a.vy -= p * ny
                b.vx += p * nx; b.vy += p * ny
            ov = (a.r + b.r - d) / 2
            a.x -= nx * ov; a.y -= ny * ov
            b.x += nx * ov; b.y += ny * ov
            victim = self.balls[1 - self.spike]
            victim.hp = max(0, victim.hp - self.rng.randint(12, 25))
            self.burst((a.x + b.x) / 2, (a.y + b.y) / 2, (255, 255, 255, 255), 30, 10)
            self.sound(120, 0.2, "sawtooth", 0.3)
            if self.rng.random() < 0.5:
                self.spike = 1 - self.spike
            if victim.hp <= 0:
                victim.dead = True
                self.burst(victim.x, victim.y, victim.col, 300, 22)
                self.sound(660, 0.8, "triangle", 0.3)
                self.finish(1.8)

    def draw(self, d, k):
        self.circle(d, k, C, C, self.R0 + 4, outline=(255, 255, 255, 255), width=8)
        for i, b in enumerate(self.balls):
            label = f"HP {b.hp}"
            d.text(((70 if i == 0 else 930) * k, 35 * k), label, fill=b.col, font=font(48 * k),
                   anchor="lm" if i == 0 else "rm")
            if b.dead:
                continue
            self.draw_ball(d, k, b, b.col)
            if i == self.spike:
                for j in range(10):
                    a = j / 10 * TAU + self.t * 3
                    pts = [(b.x + math.cos(a - 0.15) * b.r, b.y + math.sin(a - 0.15) * b.r),
                           (b.x + math.cos(a) * (b.r + 45), b.y + math.sin(a) * (b.r + 45)),
                           (b.x + math.cos(a + 0.15) * b.r, b.y + math.sin(a + 0.15) * b.r)]
                    d.polygon([(x * k, y * k) for x, y in pts], fill=(255, 255, 255, 255))


class Trail(Sim):
    """Шарики оставляют радужный след внутри круга."""
    title = "Радужный след"
    max_time = 16.0

    def setup(self):
        self.R0 = 450
        self.g = self.rnd(0.3, 0.55)
        self.hue = self.h
        self.balls = [Ball(C + self.rnd(-100, 100), C + self.rnd(-200, 0), self.rnd(-8, 8), 0, 20)
                      for _ in range(self.rng.randint(1, 3))]
        self.paths = [[] for _ in self.balls]

    def step(self):
        self.hue += 1.2
        for b, path in zip(self.balls, self.paths):
            b.move(self.g)
            if self.bounce_circle(b, self.R0, 0.5):
                self.sound(self.next_note(294), 0.15, "sine", 0.2)
            path.append((b.x, b.y, self.hue))
            if len(path) > 500:
                path.pop(0)

    def draw(self, d, k):
        self.circle(d, k, C, C, self.R0 + 3, outline=(255, 255, 255, 255), width=6)
        for b, path in zip(self.balls, self.paths):
            w = max(1, int(b.r * 1.8 * k))
            for i in range(2, len(path), 2):
                (x0, y0, _), (x1, y1, h) = path[i - 2], path[i]
                d.line([x0 * k, y0 * k, x1 * k, y1 * k], fill=hsl(h, 1.0, 0.55), width=w)
            self.draw_ball(d, k, b, (255, 255, 255, 255))


MODES = {cls.__name__.lower(): cls for cls in (Rings, Spiral, Hex, Grow, Duel, Trail)}
MODE_TITLES = {key: cls.title for key, cls in MODES.items()}


class Player:
    """Крутит симуляции одну за другой: закончилась — включаем следующую случайную."""

    def __init__(self, seed, modes=None, fps=30, backdrop=True):
        self.rng = random.Random(seed)
        self.modes = [m for m in (modes or MODES) if m in MODES] or list(MODES)
        self.substeps = max(1, round(60 / fps))
        self.backdrop = backdrop
        self.time = 0.0
        self.events = []      # (время, частота, длительность, форма волны, громкость)
        self.played = []
        self.prev = None
        self.cur = self._new()

    def _sound(self, freq, dur, wave, vol):
        self.events.append((self.time, freq, dur, wave, vol))

    def _new(self):
        options = [m for m in self.modes if m != self.prev] or self.modes
        key = self.rng.choice(options)
        self.prev = key
        self.played.append(key)
        return MODES[key](self.rng, self._sound)

    def advance(self):
        for _ in range(self.substeps):
            self.cur.update()
            self.time += DT
            if self.cur.done:
                self.cur = self._new()

    def frame(self, size) -> Image.Image:
        # рисуем в 2x и уменьшаем — сглаженные края; размытая копия даёт неоновое свечение
        ss = 2
        big = Image.new("RGBA", (size * ss, size * ss), (0, 0, 0, 0))
        self.cur.render(ImageDraw.Draw(big, "RGBA"), size * ss / 1000)
        sharp = big.resize((size, size), Image.LANCZOS)
        glow = sharp.filter(ImageFilter.GaussianBlur(size * 0.012))
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        if self.backdrop:
            k = size / 1000
            ImageDraw.Draw(img).ellipse([8 * k, 8 * k, 992 * k, 992 * k], fill=(0, 0, 0, 225))
        img.alpha_composite(glow)
        img.alpha_composite(glow)
        img.alpha_composite(sharp)
        return img
