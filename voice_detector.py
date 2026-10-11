import time
import threading
import warnings
from collections import deque
import numpy as np
from config import log_debug

SR = 44100
HOP = 882            # 20 ms
WIN = 2048
HOP_SEC = HOP / SR

VOICED_MIN_SEC = 0.8     # voz só "volta" depois de sustentada (ignora ruídos de guitarra/synth)
QUIET_MIN_SEC = 0.4      # silêncio vocal só "começa" depois de sustentado
THRESHOLD = 0.2
WEIGHTS = (0.40, 0.35, 0.25)
HISTORY_FRAMES = 1500    # 30 s para normalizar os sinais
READY_FRAMES = 250       # 5 s de áudio antes de confiar na detecção


class VoiceAnalyzer:
    """Núcleo sem I/O: recebe blocos estéreo e mantém o estado voz / silêncio vocal."""

    def __init__(self):
        self.buf = np.zeros((WIN, 2), np.float32)
        self.win = np.hanning(WIN).astype(np.float32)
        freqs = np.fft.rfftfreq(WIN, 1.0 / SR)
        self.m_voc = (freqs >= 200) & (freqs < 4000)
        self.m_form = (freqs >= 1000) & (freqs < 3000)
        self.smooth = [deque(maxlen=9) for _ in range(3)]
        self.combo = deque(maxlen=11)
        self.hist = deque(maxlen=HISTORY_FRAMES)
        self.p5 = np.zeros(3)
        self.p95 = np.ones(3)
        self.frames_since_norm = 0
        self.last_signal_time = 0.0
        self.state = None            # None | "voiced" | "quiet"
        self.quiet_since_time = None
        self.v_run = 0.0
        self.q_run = 0.0
        self.v_start = None
        self.q_start = None

    def process(self, block, now):
        """block: (n, 2) float32 com n == HOP. now: time.perf_counter() do bloco."""
        self.buf = np.concatenate([self.buf[len(block):], block])
        if float(np.sqrt((block.mean(axis=1) ** 2).mean())) < 1e-4:
            return  # áudio mudo/pausado: não atualiza nada
        self.last_signal_time = now

        mid = (self.buf[:, 0] + self.buf[:, 1]) * 0.5 * self.win
        side = (self.buf[:, 0] - self.buf[:, 1]) * 0.5 * self.win
        pm = np.abs(np.fft.rfft(mid)) ** 2
        ps = np.abs(np.fft.rfft(side)) ** 2
        voc_m = pm[self.m_voc].sum() + 1e-12
        voc_s = ps[self.m_voc].sum() + 1e-12
        feats = (
            10.0 * np.log10(voc_m),
            10.0 * np.log10(voc_m / voc_s),
            pm[self.m_form].sum() / voc_m,
        )

        sm = np.empty(3)
        for i in range(3):
            self.smooth[i].append(feats[i])
            sm[i] = sum(self.smooth[i]) / len(self.smooth[i])
        self.hist.append(sm)

        self.frames_since_norm += 1
        if self.frames_since_norm >= 25 and len(self.hist) >= 50:
            h = np.asarray(self.hist)
            self.p5 = np.percentile(h, 5, axis=0)
            self.p95 = np.percentile(h, 95, axis=0)
            self.frames_since_norm = 0

        norm = (sm - self.p5) / (self.p95 - self.p5 + 1e-9)
        self.combo.append(WEIGHTS[0] * norm[0] + WEIGHTS[1] * norm[1] + WEIGHTS[2] * norm[2])
        score = sum(self.combo) / len(self.combo)

        if len(self.hist) < READY_FRAMES:
            return

        if score > THRESHOLD:
            if self.v_run == 0.0:
                self.v_start = now
            self.v_run += HOP_SEC
            self.q_run = 0.0
            if self.v_run >= VOICED_MIN_SEC and self.state != "voiced":
                self.state = "voiced"
                self.quiet_since_time = None
        else:
            if self.q_run == 0.0:
                self.q_start = now
            self.q_run += HOP_SEC
            self.v_run = 0.0
            if self.q_run >= QUIET_MIN_SEC and self.state != "quiet":
                self.state = "quiet"
                self.quiet_since_time = self.q_start

    def ready(self, now):
        return len(self.hist) >= READY_FRAMES and (now - self.last_signal_time) < 1.0

    def quiet_since(self):
        return self.quiet_since_time if self.state == "quiet" else None

    def voiced(self):
        return self.state == "voiced"


class VoiceDetector(VoiceAnalyzer):
    """Captura o áudio que sai no Windows (loopback) e alimenta o analisador. Só analisa, nunca grava."""

    def __init__(self):
        super().__init__()
        self.available = False
        self._thread = None

    def start(self):
        try:
            import soundcard  # noqa: F401
        except Exception as e:
            log_debug(f"[Voz] Detecção de voz desativada (instale 'soundcard'): {e}")
            return
        self.available = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def is_ready(self):
        return self.available and self.ready(time.perf_counter())

    def _run(self):
        import soundcard as sc
        warnings.filterwarnings("ignore", category=sc.SoundcardRuntimeWarning)
        while True:
            try:
                speaker = sc.default_speaker()
                mic = sc.get_microphone(speaker.name, include_loopback=True)
                log_debug(f"[Voz] Capturando áudio de: {speaker.name}")
                with mic.recorder(samplerate=SR, channels=2, blocksize=HOP) as rec:
                    blocks = 0
                    while True:
                        data = rec.record(numframes=HOP)
                        self.process(np.asarray(data, np.float32), time.perf_counter())
                        blocks += 1
                        if blocks % 250 == 0 and sc.default_speaker().name != speaker.name:
                            log_debug("[Voz] Dispositivo de saída mudou; reconectando.")
                            break
            except Exception as e:
                log_debug(f"[Voz] Captura interrompida ({e}); tentando de novo em 3s.")
                time.sleep(3)
