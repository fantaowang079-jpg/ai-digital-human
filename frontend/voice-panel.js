/**
 * VoicePanel —— 麦克风语音输入面板（音量条 + 判定时间条 + 语音检测）
 *
 * 职责边界：
 *   - 只管「麦克风 / 音量 / 停顿倒计时 / 录音」，不关心聊天和 TTS。
 *   - 判定条走完时抛出 onRecordReady(blob)，由 app.js 决定识别与发送。
 *
 * 对外事件（通过构造参数传入回调）：
 *   onStateChange(state, detail)  状态：requesting / waiting / speaking / counting /
 *                                 monitoring / barged / recognizing / idle
 *   onRecordReady(blob)           一次完整录音结束，且不是主动取消
 *   onCountdownComplete()         判定时间条刚走完（用于立刻切「识别中」）
 *   onIdleTimeout()               长时间没说话
 *   onBargeIn()                   数字人说话时检测到人声（用于打断播放）
 *   onError(err)                  麦克风 / 录音失败
 */
(function (global) {
  "use strict";

  const DEFAULTS = {
    vadThreshold: 0.02,
    displayMaxRms: 0.15,
    silenceMs: 2000,
    silenceMsMin: 1000,
    silenceMsMax: 4000,
    maxRecordMs: 15000,
    idleTimeoutMs: 12000,
    // 打断检测：绝对阈值略高于普通 VAD，再按现场底噪自适应，避免被 TTS 回声误触发
    bargeInThreshold: 0.03,
    bargeInHoldMs: 300,
    bargeInGuardMs: 400,
  };

  const STATUS_TEXT = {
    idle: "语音输入未开启",
    requesting: "正在打开麦克风…",
    waiting: "聆听中 · 等待说话",
    speaking: "检测到声音",
    counting: "停顿倒计时",
    monitoring: "监听中 · 你开口就会打断",
    barged: "检测到你的声音",
    recognizing: "正在识别…",
  };

  const HINT_TEXT = {
    idle: "点 🎤 开始说话",
    requesting: "",
    waiting: "说完停顿一下，判定条走完会自动发送",
    speaking: "继续说话，或停下让判定条走完",
    counting: "判定条走完将自动识别并发送",
    monitoring: "数字人正在说话，检测到你的声音会立即停止",
    barged: "",
    recognizing: "",
  };

  const SILENCE_STORAGE_KEY = "digitalHuman.silenceMs";

  function clamp(v, min, max) {
    return Math.min(max, Math.max(min, v));
  }

  class VoicePanel {
    constructor(options = {}) {
      this.cfg = Object.assign({}, DEFAULTS, options.config || {});
      this.cfg.silenceMs = clamp(
        this.cfg.silenceMs,
        this.cfg.silenceMsMin,
        this.cfg.silenceMsMax
      );

      this.onStateChange = options.onStateChange || function () {};
      this.onRecordReady = options.onRecordReady || function () {};
      this.onCountdownComplete = options.onCountdownComplete || function () {};
      this.onIdleTimeout = options.onIdleTimeout || function () {};
      this.onBargeIn = options.onBargeIn || function () {};
      this.onError = options.onError || function () {};

      this.el = {
        panel: document.getElementById("voicePanel"),
        status: document.getElementById("vpStatusText"),
        timer: document.getElementById("vpTimer"),
        levelFill: document.getElementById("vpLevelFill"),
        levelPeak: document.getElementById("vpLevelPeak"),
        threshold: document.getElementById("vpThreshold"),
        countdownFill: document.getElementById("vpCountdownFill"),
        hint: document.getElementById("vpHint"),
        slider: document.getElementById("vpSilenceSlider"),
        sliderValue: document.getElementById("vpSilenceValue"),
      };

      this.state = "idle";
      this.stream = null;
      this.ctx = null;
      this.analyser = null;
      this.data = null;
      this.recorder = null;
      this.chunks = [];
      this.discard = false;
      this.rafId = 0;

      this.hasSpoken = false;
      this.silenceAccum = 0;
      this.remaining = this.cfg.silenceMs;
      this.recordStart = 0;
      this.lastTick = 0;

      this.smoothLevel = 0;
      this.peakLevel = 0;
      this.peakHoldUntil = 0;

      this.watchStart = 0;
      this.bargeScore = 0;
      this.noiseFloor = 0;
      this.speechLevel = 0; // 用户说话时的平均音量，用来给打断阈值做个性化
      this.effectiveBargeThreshold = this.cfg.bargeInThreshold;
      this.lastRms = 0;
      this.debug = /[?&]voiceDebug=1\b/.test(window.location.search);

      this._loadSilencePreference();
      this._bindSlider();
      this._renderThreshold();
      this._setState("idle");
      this._render(0);
    }

    /** 麦克风是否处于开启/录音状态 */
    isActive() {
      return !!(this.recorder && this.recorder.state === "recording");
    }

    /** 麦克风是否正在监听人声（数字人说话时） */
    isWatching() {
      return this.state === "monitoring" || this.state === "barged";
    }

    /** 打开麦克风与音量分析器；已有可用连接时直接复用 */
    async _ensureAudio() {
      if (this.stream && this.ctx && this.ctx.state !== "closed" && this.analyser) {
        return;
      }
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          // 连续对话时麦克风与扬声器同开，回声抑制能减少把 TTS 当成人声
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      this.stream = stream;

      this.ctx = new (window.AudioContext || window.webkitAudioContext)();
      // 自动重听时不是用户手势，AudioContext 可能处于 suspended，必须 resume
      await this.ctx.resume();

      const source = this.ctx.createMediaStreamSource(stream);
      this.analyser = this.ctx.createAnalyser();
      this.analyser.fftSize = 512;
      this.analyser.smoothingTimeConstant = 0.5;
      source.connect(this.analyser);
      this.data = new Uint8Array(this.analyser.fftSize);
    }

    /** 开始聆听（申请麦克风 + 录音 + 实时音量/倒计时） */
    async start() {
      if (this.isActive() || this.state === "requesting") return false;
      cancelAnimationFrame(this.rafId);
      this.rafId = 0;
      this._setState("requesting");

      try {
        await this._ensureAudio();

        const mimeType =
          window.MediaRecorder &&
          MediaRecorder.isTypeSupported &&
          MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
            ? "audio/webm;codecs=opus"
            : "";
        this.recorder = new MediaRecorder(
          this.stream,
          mimeType ? { mimeType } : undefined
        );
        this.chunks = [];
        this.recorder.ondataavailable = (e) => {
          if (e.data && e.data.size > 0) this.chunks.push(e.data);
        };
        this.recorder.onerror = (e) => this._fail(e.error || new Error("录音失败"));
        this.recorder.onstop = () => this._handleStopped();
        this.recorder.start(250);

        this.hasSpoken = false;
        this.silenceAccum = 0;
        this.remaining = this.cfg.silenceMs;
        this.recordStart = performance.now();
        this.lastTick = this.recordStart;
        this.bargeScore = 0;
        this.smoothLevel = 0;
        this.peakLevel = 0;
        this.discard = false;

        this._setState("waiting");
        this._loop();
        return true;
      } catch (err) {
        this._cleanup();
        this._setState("idle");
        this.onError(err);
        return false;
      }
    }

    /**
     * 监听人声（不录音）：数字人说话期间调用。
     * 打开麦克风做电平分析，持续超过打断阈值就触发 onBargeIn。
     * 与 start() 共用同一个麦克风流，打断后可直接开录，无需重新申请权限。
     */
    async watch() {
      if (this.isActive() || this.state === "monitoring") return true;
      try {
        await this._ensureAudio();
        this.watchStart = performance.now();
        this.bargeScore = 0;
        this.noiseFloor = 0;
        this.lastRms = 0;
        this.effectiveBargeThreshold = this.cfg.bargeInThreshold;
        this.smoothLevel = 0;
        this.peakLevel = 0;
        this._setState("monitoring");
        this._watchLoop();
        return true;
      } catch (err) {
        this._cleanup();
        this._setState("idle");
        this.onError(err);
        return false;
      }
    }

    /** 停止人声监听，但保留麦克风流，便于立刻转入录音 */
    stopWatch() {
      if (!this.isWatching()) return;
      cancelAnimationFrame(this.rafId);
      this.rafId = 0;
      this.bargeScore = 0;
      this._setState("idle");
    }

    /** 主动取消：停止录音并丢弃音频（打字/退出语音时用） */
    cancel() {
      if (!this.isActive()) {
        this._cleanup();
        this._setState("idle");
        return;
      }
      this.discard = true;
      this._stopRecorder();
    }

    /** 回到空闲态（识别完成后、或退出对话时调用） */
    reset() {
      if (this.isActive()) {
        this.discard = true;
        this._stopRecorder();
        return;
      }
      this._cleanup();
      this._setState("idle");
    }

    // ---- 内部：主循环 ----

    _loop() {
      const tick = (now) => {
        if (!this.isActive()) return;
        const dt = Math.max(0, now - this.lastTick);
        this.lastTick = now;

        const rms = this._rms();
        const display = this._trackLevel(rms, now);

        if (rms > this.cfg.vadThreshold) {
          // 记录用户正常说话的音量，供打断阈值自适应（长对话中持续校准）
          this.speechLevel = this.speechLevel
            ? this.speechLevel * 0.97 + rms * 0.03
            : rms;
          this.hasSpoken = true;
          this.silenceAccum = 0;
          this.remaining = this.cfg.silenceMs;
          if (this.state !== "speaking") this._setState("speaking");
        } else if (this.hasSpoken) {
          this.silenceAccum += dt;
          this.remaining = Math.max(0, this.cfg.silenceMs - this.silenceAccum);
          if (this.state !== "counting") this._setState("counting");
          if (this.remaining <= 0) {
            this._finishCountdown();
            return;
          }
        } else {
          // 还没开口：一直等，但不能无限等
          this.remaining = this.cfg.silenceMs;
          if (now - this.recordStart > this.cfg.idleTimeoutMs) {
            this.onIdleTimeout();
            this.discard = true;
            this._stopRecorder();
            return;
          }
        }

        // 最长录音兜底：说过话就走识别，没说过话直接放弃
        if (now - this.recordStart > this.cfg.maxRecordMs) {
          if (this.hasSpoken) {
            this._finishCountdown();
          } else {
            this.discard = true;
            this._stopRecorder();
          }
          return;
        }

        this._render(display);
        this.rafId = requestAnimationFrame(tick);
      };
      this.rafId = requestAnimationFrame(tick);
    }

    /**
     * 当前生效的打断阈值：
     *   1) 按用户实际说话音量下调（不低于普通 VAD 阈值），说话小声也能打断；
     *   2) 再按现场底噪（含 TTS 残留回声）× 1.5 上调，最多放宽到配置值的 2.5 倍。
     */
    _bargeThreshold() {
      const cfgFloor = this.cfg.bargeInThreshold;
      const vadFloor = this.cfg.vadThreshold;
      const byVoice =
        this.speechLevel > 0
          ? Math.min(cfgFloor, Math.max(vadFloor, this.speechLevel * 0.6))
          : cfgFloor;
      const byNoise = this.noiseFloor > 0 ? this.noiseFloor * 1.5 : 0;
      return Math.min(cfgFloor * 2.5, Math.max(byVoice, byNoise));
    }

    /** 数字人说话期间的人声监听：只分析电平，不录音 */
    _watchLoop() {
      const tick = (now) => {
        if (this.state !== "monitoring") return;

        const rms = this._rms();
        this.lastRms = rms;
        const display = this._trackLevel(rms, now);

        // 跟踪底噪（含 TTS 残留回声）：下降快、上升很慢，避免把人声当底噪
        if (this.noiseFloor === 0) {
          this.noiseFloor = rms;
        } else if (rms < this.noiseFloor) {
          this.noiseFloor += (rms - this.noiseFloor) * 0.08;
        } else {
          this.noiseFloor += (rms - this.noiseFloor) * 0.0015;
        }
        this.effectiveBargeThreshold = this._bargeThreshold();

        // 起播静默期：避开扬声器起播杂音
        const guardOver = now - this.watchStart >= this.cfg.bargeInGuardMs;

        // 漏积分：超过阈值时累计，短暂回落时缓慢衰减而不是清零，
        // 这样一句话中间的音节间隙不会把计时打断
        const dt = Math.min(50, Math.max(0, now - this.lastWatchTick));
        this.lastWatchTick = now;
        if (guardOver && rms >= this.effectiveBargeThreshold) {
          this.bargeScore += dt;
        } else {
          this.bargeScore = Math.max(0, this.bargeScore - dt * 0.6);
        }

        if (this.bargeScore >= this.cfg.bargeInHoldMs) {
          console.info(
            "[barge] rms=" + rms.toFixed(4) +
            " threshold=" + this.effectiveBargeThreshold.toFixed(4) +
            " floor=" + this.noiseFloor.toFixed(4)
          );
          this._setState("barged");
          this.onBargeIn();
          return;
        }

        this._render(display);
        this.rafId = requestAnimationFrame(tick);
      };
      this.lastWatchTick = performance.now();
      this.rafId = requestAnimationFrame(tick);
    }

    /** 读取当前 RMS 音量 */
    _rms() {
      this.analyser.getByteTimeDomainData(this.data);
      let sum = 0;
      for (let i = 0; i < this.data.length; i += 1) {
        const v = (this.data[i] - 128) / 128;
        sum += v * v;
      }
      return Math.sqrt(sum / this.data.length);
    }

    /** 平滑音量并维护峰值保持，返回 0–1 的显示比例 */
    _trackLevel(rms, now) {
      this.smoothLevel = this.smoothLevel * 0.72 + rms * 0.28;
      const display = this._normalizeLevel(this.smoothLevel);
      if (display >= this.peakLevel) {
        this.peakLevel = display;
        this.peakHoldUntil = now + 450;
      } else if (now > this.peakHoldUntil) {
        this.peakLevel = Math.max(display, this.peakLevel * 0.94);
      }
      return display;
    }

    _finishCountdown() {
      this.onCountdownComplete();
      this._setState("recognizing");
      this._stopRecorder();
    }

    _stopRecorder() {
      cancelAnimationFrame(this.rafId);
      this.rafId = 0;
      if (this.recorder && this.recorder.state !== "inactive") {
        try {
          this.recorder.stop();
        } catch (e) {
          this._cleanup();
          this._setState("idle");
        }
      } else {
        this._cleanup();
        this._setState("idle");
      }
    }

    _handleStopped() {
      const chunks = this.chunks;
      const mime = (this.recorder && this.recorder.mimeType) || "audio/webm";
      const discarded = this.discard;
      this.discard = false;
      // 若麦克风流已被「监听人声」接管（说话期间 wait→watch），
      // 不能在这里关掉 stream/ctx，否则监听会瞬间失效
      const keepAudio = this.isWatching();
      this.recorder = null;
      if (!keepAudio) this._cleanup();

      if (discarded) {
        if (!keepAudio) this._setState("idle");
        return;
      }
      if (keepAudio) return;

      const blob = new Blob(chunks, { type: mime });
      if (!blob.size) {
        this._setState("idle");
        this.onError(new Error("没有录到声音"));
        return;
      }
      this._setState("recognizing");
      this.onRecordReady(blob);
    }

    _fail(err) {
      this.discard = true;
      this._cleanup();
      this._setState("idle");
      this.onError(err);
    }

    _cleanup() {
      cancelAnimationFrame(this.rafId);
      this.rafId = 0;
      if (this.stream) {
        this.stream.getTracks().forEach((t) => t.stop());
        this.stream = null;
      }
      if (this.ctx) {
        this.ctx.close().catch(() => {});
        this.ctx = null;
      }
      this.analyser = null;
      this.data = null;
      this.recorder = null;
    }

    // ---- 内部：渲染 ----

    _normalizeLevel(rms) {
      const ratio = clamp(rms / this.cfg.displayMaxRms, 0, 1);
      // 平方根压缩：正常说话音量也能把条推到 60% 以上，小声音可见
      return Math.sqrt(ratio);
    }

    _renderThreshold() {
      // 正常聆听时标出 VAD 阈值；数字人说话时标出实时自适应的打断阈值
      const watching = this.isWatching();
      const threshold = watching
        ? this.effectiveBargeThreshold
        : this.cfg.vadThreshold;
      const pos = clamp(
        Math.sqrt(threshold / this.cfg.displayMaxRms) * 100,
        2,
        98
      );
      if (this.el.threshold) {
        this.el.threshold.style.left = pos + "%";
        const label = this.el.threshold.querySelector("span");
        if (label) label.textContent = watching ? "打断" : "阈值";
      }
    }

    _render(display) {
      const el = this.el;
      if (!el.panel) return;

      el.panel.dataset.state = this.state;
      if (this.isWatching()) this._renderThreshold();
      el.status.textContent = STATUS_TEXT[this.state] || "";
      el.hint.textContent = HINT_TEXT[this.state] || "";

      // 排障用：地址栏加 ?voiceDebug=1 可看到实时电平与自适应阈值
      if (this.debug && this.state === "monitoring") {
        el.hint.textContent =
          "电平 " + this.lastRms.toFixed(3) +
          " / 阈值 " + this.effectiveBargeThreshold.toFixed(3) +
          "（底噪 " + this.noiseFloor.toFixed(3) +
          " · 说话 " + this.speechLevel.toFixed(3) + "）";
      }

      const total = this.cfg.silenceMs;
      const counting = this.state === "counting";
      const fraction = counting ? clamp(this.remaining / total, 0, 1) : 1;

      if (counting) {
        el.timer.textContent = (this.remaining / 1000).toFixed(1) + "s";
      } else {
        el.timer.textContent = (total / 1000).toFixed(1) + "s";
      }

      if (el.levelFill) {
        el.levelFill.style.width = (display * 100).toFixed(1) + "%";
      }
      if (el.levelPeak) {
        el.levelPeak.style.left = (this.peakLevel * 100).toFixed(1) + "%";
      }
      if (el.countdownFill) {
        el.countdownFill.style.width = (fraction * 100).toFixed(1) + "%";
      }

      const danger = counting && fraction <= 0.25;
      el.panel.classList.toggle("danger", danger);
      el.panel.classList.toggle("counting", counting);
      el.panel.classList.toggle(
        "live",
        this.state === "waiting" ||
          this.state === "speaking" ||
          this.state === "counting" ||
          this.state === "monitoring" ||
          this.state === "barged"
      );
    }

    _setState(state) {
      if (this.state === state) return;
      this.state = state;
      // 非聆听状态把音量条归零，避免退出后残留上一次的音量
      const atRest =
        state === "idle" || state === "requesting" || state === "recognizing";
      if (atRest) {
        this.smoothLevel = 0;
        this.peakLevel = 0;
      }
      this._renderThreshold();
      this._render(atRest ? 0 : this._normalizeLevel(this.smoothLevel));
      this.onStateChange(state);
    }

    // ---- 内部：判定时间条可调 ----

    _loadSilencePreference() {
      try {
        const saved = parseInt(localStorage.getItem(SILENCE_STORAGE_KEY), 10);
        if (Number.isFinite(saved)) {
          this.cfg.silenceMs = clamp(
            saved,
            this.cfg.silenceMsMin,
            this.cfg.silenceMsMax
          );
        }
      } catch (e) {
        /* localStorage 不可用时用默认值 */
      }
    }

    _bindSlider() {
      const slider = this.el.slider;
      if (!slider) return;
      slider.min = String(this.cfg.silenceMsMin);
      slider.max = String(this.cfg.silenceMsMax);
      slider.step = "100";
      slider.value = String(this.cfg.silenceMs);
      if (this.el.sliderValue) {
        this.el.sliderValue.textContent = (this.cfg.silenceMs / 1000).toFixed(1) + "s";
      }
      slider.addEventListener("input", () => {
        const v = parseInt(slider.value, 10);
        if (!Number.isFinite(v)) return;
        this.cfg.silenceMs = clamp(v, this.cfg.silenceMsMin, this.cfg.silenceMsMax);
        if (this.state === "counting") {
          // 调短时立即把剩余时间夹到新总长内
          this.remaining = Math.min(this.remaining, this.cfg.silenceMs);
        } else {
          this.remaining = this.cfg.silenceMs;
        }
        if (this.el.sliderValue) {
          this.el.sliderValue.textContent = (this.cfg.silenceMs / 1000).toFixed(1) + "s";
        }
        try {
          localStorage.setItem(SILENCE_STORAGE_KEY, String(this.cfg.silenceMs));
        } catch (e) {
          /* 忽略 */
        }
        this._render(this._normalizeLevel(this.smoothLevel));
      });
    }
  }

  global.VoicePanel = VoicePanel;
})(window);
