/**
 * app.js —— 对话主逻辑：输入、流式回复、音频播放、数字人口型。
 * 语音采集/音量/判定时间条全部交给 voice-panel.js，这里只接事件。
 */
(function () {
  "use strict";

  const chatBox = document.getElementById("chatBox");
  const input = document.getElementById("input");
  const sendBtn = document.getElementById("send");
  const micBtn = document.getElementById("mic");
  const statusEl = document.getElementById("status");
  const providerEl = document.getElementById("provider");
  const mouth = document.getElementById("mouth");
  const eyeL = document.getElementById("eyeL");
  const eyeR = document.getElementById("eyeR");

  let messages = [];
  let audioCtx = null;
  let analyser = null;
  let rafId = null;

  // 音频播放队列：流式逐句到达，按顺序串行播放
  const audioQueue = [];
  let isPlaying = false;
  let isStreaming = false;
  let conversationMode = false;
  let panel = null;

  // 打断（barge-in）相关状态
  let currentSource = null;      // 正在播放的音频节点
  let playbackSession = 0;       // 播放代次：停止播放时递增，让旧循环失效
  let chatAbort = null;          // 流式请求的 AbortController
  let abortedByBarge = false;    // 本轮回复是否被用户打断
  let bargeInPending = false;    // 打断后正在转入录音，避免旧逻辑重复启动聆听

  function addBubble(text, role) {
    const div = document.createElement("div");
    div.className = "bubble " + role;
    div.textContent = text;
    chatBox.appendChild(div);
    chatBox.scrollTop = chatBox.scrollHeight;
    return div;
  }

  function setStatus(mode, text) {
    statusEl.className = "status " + mode;
    statusEl.innerHTML = '<span class="dot"></span>' + text;
  }

  // ---- 音频播放与口型 ----

  function ensureAudioCtx() {
    if (!audioCtx) {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      analyser = audioCtx.createAnalyser();
      analyser.fftSize = 256;
      // 只连一次：重复 connect 到 destination 会让音量叠加
      analyser.connect(audioCtx.destination);
    }
    return audioCtx;
  }

  function playAudio(base64) {
    const bin = atob(base64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);

    const ctx = ensureAudioCtx();
    return ctx
      .resume()
      .then(() => ctx.decodeAudioData(bytes.buffer))
      .then(
        (buffer) =>
          new Promise((resolve) => {
            const source = ctx.createBufferSource();
            source.buffer = buffer;
            source.connect(analyser);
            currentSource = source;
            source.start();
            animateMouth();
            source.onended = () => {
              if (currentSource === source) currentSource = null;
              cancelAnimationFrame(rafId);
              mouth.setAttribute("ry", 4);
              mouth.setAttribute("cy", 128 + 4 * 0.4);
              resolve();
            };
          })
      );
  }

  function enqueueAudio(b64) {
    if (!b64) return;
    audioQueue.push(b64);
    if (!isPlaying) playNextInQueue().catch((e) => console.warn("playback failed", e));
  }

  /** 立刻停止数字人说话，并让旧的播放循环失效 */
  function stopPlayback() {
    playbackSession += 1;
    audioQueue.length = 0;
    if (currentSource) {
      const source = currentSource;
      currentSource = null;
      try {
        source.stop();
      } catch (e) {
        /* 已经结束 */
      }
    }
    cancelAnimationFrame(rafId);
    mouth.setAttribute("ry", 4);
    mouth.setAttribute("cy", 128 + 4 * 0.4);
    isPlaying = false;
  }

  async function playNextInQueue() {
    const session = ++playbackSession;
    isPlaying = true;
    try {
      // 数字人说话期间持续监听人声，用于打断
      if (conversationMode && panel && !panel.isWatching()) {
        await panel.watch();
      }
      if (session !== playbackSession) return;

      while (audioQueue.length > 0) {
        const b64 = audioQueue.shift();
        setStatus("speaking", "正在说话…");
        // 单句解码/播放失败不能让整个队列卡死
        try {
          await playAudio(b64);
        } catch (e) {
          console.warn("audio sentence failed", e);
        }
      }
    } finally {
      if (session !== playbackSession) return; // 已被停止/打断，交给新流程接管
      isPlaying = false;
      // 还在流式生成时保持监听，避免句与句之间的空档无法打断
      if (!isStreaming && panel) panel.stopWatch();
      maybeRestartListening();
    }
  }

  function animateMouth() {
    const data = new Uint8Array(analyser.fftSize);
    analyser.getByteTimeDomainData(data);
    let sum = 0;
    for (let i = 0; i < data.length; i++) {
      const v = (data[i] - 128) / 128;
      sum += v * v;
    }
    const rms = Math.sqrt(sum / data.length);
    const open = Math.min(1, rms * 5);
    const ry = 2 + open * 12;
    mouth.setAttribute("ry", ry);
    mouth.setAttribute("cy", 128 + ry * 0.4);
    rafId = requestAnimationFrame(animateMouth);
  }

  function blink() {
    [eyeL, eyeR].forEach((e) => e.setAttribute("ry", 1));
    setTimeout(() => [eyeL, eyeR].forEach((e) => e.setAttribute("ry", 7)), 120);
  }
  setInterval(blink, 3000);

  // ---- 聊天请求 ----

  async function send() {
    if (isStreaming) return; // 防止 Enter / 连点造成两条并发流
    const text = input.value.trim();
    if (!text) return;

    // 语音输入正在进行时改用打字：取消本次录音，避免把录音内容也发出去
    if (panel && panel.isActive()) panel.cancel();
    if (panel && panel.isWatching()) panel.stopWatch();
    // 上一轮还在播报时直接发送新消息：先停掉旧声音
    if (isPlaying) stopPlayback();

    abortedByBarge = false;

    input.value = "";
    addBubble(text, "user");
    messages.push({ role: "user", content: text });

    sendBtn.disabled = true;
    input.disabled = true;
    if (!conversationMode) micBtn.disabled = true;
    isStreaming = true;
    setStatus("thinking", "正在思考…");

    const bubble = addBubble("", "assistant");
    const controller = new AbortController();
    chatAbort = controller;
    let fullText = "";
    let failed = false;
    let interrupted = false;

    try {
      const resp = await fetch("/api/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages }),
        signal: controller.signal,
      });
      if (!resp.ok) throw new Error(await resp.text());
      if (!resp.body) throw new Error("流式响应为空");

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop();
        for (const line of lines) {
          if (!line.trim()) continue;
          const chunk = JSON.parse(line);
          if (chunk.type === "error") {
            throw new Error(chunk.message || "生成失败，请稍后重试");
          }
          if (chunk.type && chunk.type !== "sentence") continue;
          fullText += chunk.text || "";
          bubble.textContent = fullText;
          chatBox.scrollTop = chatBox.scrollHeight;
          enqueueAudio(chunk.audio_base64);
        }
      }
      if (!fullText.trim()) throw new Error("没有收到回复");
      messages.push({ role: "assistant", content: fullText });
    } catch (e) {
      if (abortedByBarge) {
        // 用户开口打断：不是错误，保留用户消息、不还原输入
        interrupted = true;
        abortedByBarge = false;
        bubble.textContent = fullText ? fullText + "（已打断）" : "（已打断）";
      } else {
        failed = true;
        // 本轮失败：撤回刚加入的用户消息，避免下一轮把失败的问题重复发送
        const last = messages[messages.length - 1];
        if (last && last.role === "user" && last.content === text) messages.pop();
        input.value = text; // 还原输入，方便直接重试
        audioQueue.length = 0; // 丢弃尚未播放的后续音频
        bubble.textContent = fullText
          ? fullText + "【生成中断】" + (e.message || "请稍后重试")
          : "出错了：" + (e.message || "请稍后重试");
        if (conversationMode) exitConversation();
        setStatus("", "待机中");
      }
    } finally {
      if (chatAbort === controller) chatAbort = null;
      sendBtn.disabled = false;
      input.disabled = false;
      if (!conversationMode) micBtn.disabled = false;
      isStreaming = false;
      if (!failed && !interrupted) maybeRestartListening();
    }
  }

  // ---- 语音识别 ----

  function blobToBase64(blob) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onloadend = () => resolve(String(reader.result).split(",")[1]);
      reader.onerror = reject;
      reader.readAsDataURL(blob);
    });
  }

  async function handleRecord(blob) {
    setStatus("thinking", "正在识别…");
    try {
      const base64 = await blobToBase64(blob);
      const r = await fetch("/api/stt", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ audio_base64: base64 }),
      });
      if (!r.ok) {
        let detail = "语音识别失败";
        try {
          const err = await r.json();
          if (err && err.detail) detail = err.detail;
        } catch (e) {
          /* 非 JSON 错误体 */
        }
        throw new Error(detail);
      }
      const d = await r.json();

      // 识别期间用户可能已经退出语音输入，此时不要再自动发送
      if (!conversationMode) {
        panel.reset();
        return;
      }

      if (d.text) {
        // 识别完成后先收回语音面板，避免回复期间面板还显示「正在识别…」
        panel.reset();
        input.value = d.text;
        await send();
      } else {
        addBubble("（没有听清，请再说一遍）", "assistant");
        relistenOrIdle();
      }
    } catch (e) {
      if (!conversationMode) {
        panel.reset();
        return;
      }
      addBubble("语音识别出错：" + (e.message || "请重试"), "assistant");
      relistenOrIdle();
    }
  }

  // 识别失败/没听清：对话模式下重新聆听，否则回到待机
  function relistenOrIdle() {
    if (conversationMode) {
      setStatus("thinking", "请再说一遍…");
      panel.reset();
      panel.start();
    } else {
      panel.reset();
      setStatus("", "待机中");
    }
  }

  // ---- 语音输入开关 ----

  async function enterConversation() {
    if (!panel) return; // 页面刚加载、语音面板尚未就绪
    conversationMode = true;
    micBtn.textContent = "⏹";
    micBtn.classList.add("recording");
    setStatus("thinking", "正在打开麦克风…");
    const ok = await panel.start();
    if (!ok && conversationMode) {
      // 启动失败（无权限/无设备），onError 已提示
      conversationMode = false;
      micBtn.textContent = "🎤";
      micBtn.classList.remove("recording");
    }
  }

  function exitConversation() {
    conversationMode = false;
    micBtn.textContent = "🎤";
    micBtn.classList.remove("recording");
    if (panel) panel.reset();
    if (!isStreaming && !isPlaying && audioQueue.length === 0) {
      setStatus("", "待机中");
    }
  }

  // 回复播放结束后，若仍处对话模式则自动重新聆听（连续聊天）
  function maybeRestartListening() {
    if (bargeInPending) return; // 打断流程正在接管
    if (
      conversationMode &&
      panel &&
      !isStreaming &&
      !isPlaying &&
      audioQueue.length === 0 &&
      !panel.isActive()
    ) {
      setStatus("thinking", "请继续说话…");
      panel.stopWatch();
      panel.start();
    } else if (!conversationMode && !isPlaying && audioQueue.length === 0) {
      setStatus("", "待机中");
    }
  }

  /** 停止正在进行的播报/生成，视为一次主动打断 */
  function interruptAssistant() {
    if (!isPlaying && !isStreaming) return;
    abortedByBarge = true;
    if (chatAbort) {
      try {
        chatAbort.abort();
      } catch (e) {
        /* 已结束 */
      }
      chatAbort = null;
    }
    stopPlayback();
  }

  function toggleRecording() {
    if (conversationMode) {
      // 点 ⏹ 退出语音输入时，如果数字人还在说话，也一并停下
      interruptAssistant();
      exitConversation();
      setStatus("", "待机中");
    } else {
      // 从待机进入语音输入：先停掉残留的播报，避免麦克风把 TTS 当用户输入
      interruptAssistant();
      enterConversation();
    }
  }

  // ---- 语音面板事件 ----

  function handlePanelState(state) {
    if (state === "requesting") setStatus("thinking", "正在打开麦克风…");
    else if (state === "waiting") setStatus("thinking", "请开始说话…");
    else if (state === "speaking") setStatus("thinking", "正在聆听…");
    else if (state === "counting") setStatus("thinking", "停顿倒计时…");
    else if (state === "recognizing") setStatus("thinking", "正在识别…");
    else if (state === "monitoring") setStatus("speaking", "正在说话…（你开口可打断）");
    else if (state === "barged") setStatus("thinking", "检测到你的声音，停止回答…");
    else if (state === "idle" && !isStreaming && !isPlaying && audioQueue.length === 0) {
      setStatus("", "待机中");
    }
  }

  /** 数字人说话时检测到人声：立即停播、中断生成，并转入聆听 */
  function handleBargeIn() {
    if (!conversationMode) return;
    bargeInPending = true;
    abortedByBarge = true;

    if (chatAbort) {
      try {
        chatAbort.abort();
      } catch (e) {
        /* 已结束 */
      }
      chatAbort = null;
    }
    stopPlayback();

    if (panel) {
      panel.stopWatch(); // 保留麦克风流，下一步直接开录
      setStatus("thinking", "检测到你的声音，停止回答…");
      Promise.resolve(panel.start()).finally(() => {
        bargeInPending = false;
      });
    } else {
      bargeInPending = false;
    }
  }

  function handleIdleTimeout() {
    exitConversation();
    addBubble("很久没有听到声音，已退出语音输入。点 🎤 可以重新开始。", "assistant");
  }

  function handlePanelError(err) {
    const name = err && err.name;
    let msg = (err && err.message) || "无法使用麦克风";
    if (name === "NotAllowedError" || name === "SecurityError") {
      msg = "麦克风权限被拒绝，请在浏览器地址栏允许麦克风后重试";
    } else if (name === "NotFoundError" || name === "DevicesNotFoundError") {
      msg = "没有找到麦克风设备";
    }
    addBubble("无法访问麦克风：" + msg, "assistant");
    if (conversationMode) exitConversation();
  }

  // ---- 启动 ----

  async function loadVoiceConfig() {
    try {
      const r = await fetch("/api/voice/config");
      if (r.ok) return await r.json();
    } catch (e) {
      /* 后端未就绪时用默认值 */
    }
    return null;
  }

  async function boot() {
    try {
      const r = await fetch("/api/health");
      const d = await r.json();
      providerEl.textContent =
        "LLM: " + d.llm_provider + " · TTS: " + d.tts_provider + " · STT: " + d.stt_provider;
    } catch (e) {
      providerEl.textContent = "后端未连接";
    }

    const voiceConfig = await loadVoiceConfig();
    panel = new VoicePanel({
      config: voiceConfig || {},
      onStateChange: handlePanelState,
      onRecordReady: handleRecord,
      onIdleTimeout: handleIdleTimeout,
      onBargeIn: handleBargeIn,
      onError: handlePanelError,
    });
  }

  sendBtn.addEventListener("click", send);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") send();
  });
  micBtn.addEventListener("click", toggleRecording);

  boot();
})();
