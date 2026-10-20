"""Накладывает симуляции на видео через ffmpeg (бинарник берём из imageio-ffmpeg)."""
import math
import os
import re
import subprocess
import wave
from dataclasses import dataclass, field

import imageio_ffmpeg
import numpy as np

from sims import MODES, Player

FFMPEG = os.getenv("FFMPEG_PATH") or imageio_ffmpeg.get_ffmpeg_exe()
OVERLAY_FPS = 30
SR = 44100
TG_LIMIT = 49 * 1024 * 1024  # Bot API не отправит файл больше 50 МБ


@dataclass
class Options:
    position: str = "bottom"      # bottom | center | top
    size: float = 0.55            # доля от ширины кадра
    modes: list = field(default_factory=lambda: list(MODES))
    sound: bool = True
    sound_volume: float = 0.6
    backdrop: bool = True         # тёмный круг под симуляцией
    seed: int | None = None


@dataclass
class VideoInfo:
    width: int
    height: int
    duration: float
    has_audio: bool


def probe(path) -> VideoInfo:
    err = subprocess.run([FFMPEG, "-hide_banner", "-i", path], capture_output=True,
                         text=True, errors="ignore").stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    if not m:
        raise ValueError("Не удалось прочитать видео")
    duration = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
    m = re.search(r"Video:.*?\b(\d{2,5})x(\d{2,5})\b", err)
    if not m:
        raise ValueError("В файле нет видеодорожки")
    w, h = int(m[1]), int(m[2])
    rot = re.search(r"rotation of (-?[\d.]+)", err) or re.search(r"rotate\s*:\s*(-?\d+)", err)
    if rot and round(abs(float(rot[1]))) % 180 == 90:
        w, h = h, w  # телефонное видео с поворотом в метаданных
    return VideoInfo(w, h, duration, "Audio:" in err)


def synth(events, duration, volume, path):
    """Собирает звуки ударов в wav."""
    out = np.zeros(int((duration + 1) * SR), np.float32)
    for t, f, dur, wav, vol in events:
        n = int((dur + 0.03) * SR)
        tt = np.arange(n) / SR
        ph = 2 * math.pi * f * tt
        if wav == "square":
            s = np.sign(np.sin(ph))
        elif wav == "triangle":
            s = 2 / math.pi * np.arcsin(np.sin(ph))
        elif wav == "sawtooth":
            s = 2 * ((tt * f) % 1) - 1
        else:
            s = np.sin(ph)
        s *= vol * np.exp(-tt * 6.9 / dur)
        i = int(t * SR)
        seg = out[i:i + n]
        seg += s[:len(seg)]
    peak = np.abs(out).max()
    if peak > 0.95:
        out *= 0.95 / peak
    out *= volume
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((out * 32767).astype("<i2").tobytes())


def render_video(src, dst, opts: Options, progress=None, workdir=None, demo_seconds=None, max_duration=None):
    """src=None + demo_seconds → демо на чёрном фоне 720x1280."""
    workdir = workdir or os.path.dirname(dst) or "."
    if src is None:
        info = VideoInfo(720, 1280, float(demo_seconds or 15), False)
        base_input = ["-f", "lavfi", "-i", f"color=c=black:s=720x1280:r=30:d={info.duration}"]
    else:
        info = probe(src)
        if max_duration and info.duration > max_duration:
            raise ValueError(f"видео длиннее {max_duration} с")
        base_input = ["-i", src]

    size = int(min(info.width, info.height) * opts.size) // 2 * 2
    frames = math.ceil(info.duration * OVERLAY_FPS) + 1
    seed = opts.seed if opts.seed is not None else int.from_bytes(os.urandom(4), "big")

    # 1) быстрый прогон физики без отрисовки — собираем звуки (физика детерминирована по seed)
    pre = Player(seed, opts.modes, OVERLAY_FPS, opts.backdrop)
    for _ in range(frames):
        pre.advance()
    wav_path = os.path.join(workdir, "blips.wav")
    synth(pre.events if opts.sound else [], info.duration, opts.sound_volume, wav_path)

    # 2) рендер кадров прямо в ffmpeg
    y = {"top": "H*0.06", "center": "(H-h)/2"}.get(opts.position, "H-h-H*0.06")
    filt = f"[1:v]format=rgba[ov];[0:v][ov]overlay=x=(W-w)/2:y={y}:eof_action=pass,format=yuv420p[v];"
    filt += "[0:a][2:a]amix=inputs=2:duration=first:normalize=0[a]" if info.has_audio else "[2:a]anull[a]"
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", *base_input,
           "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{size}x{size}", "-r", str(OVERLAY_FPS), "-i", "pipe:0",
           "-i", wav_path, "-filter_complex", filt, "-map", "[v]", "-map", "[a]",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
           "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", "-t", f"{info.duration:.3f}", dst]
    log_path = os.path.join(workdir, "ffmpeg.log")
    player = Player(seed, opts.modes, OVERLAY_FPS, opts.backdrop)
    with open(log_path, "wb") as log:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=log)
        try:
            last = -1
            for i in range(frames):
                proc.stdin.write(player.frame(size).tobytes())
                player.advance()
                pct = i * 100 // frames
                if progress and pct // 10 != last:
                    last = pct // 10
                    progress(pct)
            proc.stdin.close()
        except BrokenPipeError:
            pass
        code = proc.wait()
    if code != 0:
        with open(log_path, encoding="utf-8", errors="ignore") as f:
            raise RuntimeError("ffmpeg: " + f.read()[-800:])

    # 3) если не влезает в лимит Telegram — пережимаем в 720p с подходящим битрейтом
    if os.path.getsize(dst) > TG_LIMIT:
        smaller = dst + ".small.mp4"
        kbps = max(300, int(TG_LIMIT * 8 / 1000 / info.duration * 0.9) - 128)
        subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", dst,
                        "-vf", "scale='if(gt(iw,ih),-2,720)':'if(gt(iw,ih),720,-2)'",
                        "-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k",
                        "-bufsize", f"{kbps * 2}k", "-c:a", "aac", "-b:a", "128k",
                        "-movflags", "+faststart", smaller], check=True)
        os.replace(smaller, dst)

    return dict(played=list(dict.fromkeys(player.played)), duration=info.duration)


if __name__ == "__main__":
    import sys, time
    t0 = time.time()
    src = sys.argv[1] if len(sys.argv) > 1 else None
    res = render_video(src, "out.mp4", Options(), progress=lambda p: print(f"{p}%"), demo_seconds=20)
    print(res, f"{time.time() - t0:.1f}s")
