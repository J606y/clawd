const CSS = `
.hp-chat { position: relative; }
.hp-chat.asking .composer,
.hp-chat.asking .jump { visibility: hidden; }
.hp-chat .scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  overflow-anchor: none;
}
.hp-chat .col {
  max-width: 780px;
  margin: 0 auto;
  padding: 24px 20px 8px;
}
.hp-chat .turn { margin-bottom: 24px; }
.hp-chat .past, .hp-chat .mount-row { display: flow-root; }
.hp-chat .user {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 6px;
}
.hp-chat .bubble {
  max-width: 85%;
  padding: 10px 14px;
  border-radius: var(--radius-l);
  background: var(--bg-active);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  line-height: 1.55;
}
.hp-chat .user-atts { max-width: 85%; }
.hp-chat .steer { margin-top: -16px; }
.hp-chat .steer .bubble {
  background: color-mix(in srgb, var(--accent) 12%, var(--bg-elevated));
  font-size: 14px;
}
.hp-chat .steer-tag { font-size: 11px; color: var(--text-tertiary); }
.hp-chat .steer-tag[data-s="failed"] { color: var(--danger); }
.hp-chat .assistant {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}
.hp-chat .run-status-slot { min-height: 24px; }
.hp-chat .took { font-size: 12px; color: var(--text-tertiary); }
.hp-chat details.reasoning {
  font-size: 13px;
  color: var(--text-secondary);
  min-width: 0;
  max-width: 100%;
}
.hp-chat details.reasoning[open]::details-content { content-visibility: visible; }
.hp-chat details.reasoning summary {
  cursor: pointer;
  list-style: none;
  display: inline-block;
  padding: 4px 6px;
  margin-left: -6px;
  border-radius: var(--radius-s);
}
.hp-chat details.reasoning summary:hover { background: var(--bg-hover); }
.hp-chat details.reasoning summary::-webkit-details-marker { display: none; }
.hp-chat details.bg-procs { font-size: 12px; color: var(--text-secondary); }
.hp-chat details.bg-procs summary {
  cursor: pointer;
  list-style: none;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px;
  margin-left: -6px;
  border-radius: var(--radius-s);
}
.hp-chat details.bg-procs summary:hover { background: var(--bg-hover); }
.hp-chat details.bg-procs summary::-webkit-details-marker { display: none; }
.hp-chat details.bg-procs summary::before {
  content: "";
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--success);
  animation: hp-bg-pulse 2s ease-in-out infinite;
}
@keyframes hp-bg-pulse { 50% { opacity: 0.35; } }
@media (prefers-reduced-motion: reduce) {
  .hp-chat details.bg-procs summary::before { animation: none; }
}
.hp-chat details.bg-procs ul { margin: 4px 0 0; padding-left: 18px; }
.hp-chat details.bg-procs code {
  font-family: var(--font-mono);
  font-size: 12px;
  overflow-wrap: anywhere;
}
.hp-chat details.sys-notice { font-size: 12px; color: var(--text-tertiary); }
.hp-chat details.sys-notice summary {
  cursor: pointer;
  list-style: none;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px;
  margin-left: -6px;
  border-radius: var(--radius-s);
}
.hp-chat details.sys-notice summary:hover { background: var(--bg-hover); }
.hp-chat details.sys-notice summary::-webkit-details-marker { display: none; }
.hp-chat details.sys-notice summary::before {
  content: "";
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}
.hp-chat details.sys-notice.failed summary { color: var(--danger); }
.hp-chat details.sys-notice > .md,
.hp-chat details.sys-notice > .hp-md {
  font-size: 13px;
  color: var(--text-secondary);
  padding: 4px 0 4px 12px;
  border-left: 2px solid var(--border);
  margin-top: 4px;
}
.hp-chat details.reasoning div {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
  min-width: 0;
  max-width: 100%;
  box-sizing: border-box;
  padding: 4px 0 4px 12px;
  border-left: 2px solid var(--border);
  margin-top: 4px;
  overflow-anchor: none;
  overscroll-behavior: contain;
}
.hp-chat details.reasoning[open] > div {
  height: 168px;
  overflow-x: hidden;
  overflow-y: auto;
}
.hp-chat .approval {
  border: 1px solid color-mix(in srgb, var(--warning) 45%, var(--border));
  background: color-mix(in srgb, var(--warning) 8%, var(--bg-elevated));
  border-radius: var(--radius-l);
  padding: 12px 14px;
}
.hp-chat .approval h3 { margin: 0 0 4px; font-size: 14px; }
.hp-chat .approval p { margin: 0 0 8px; font-size: 13px; color: var(--text-secondary); }
.hp-chat .approval pre {
  background: var(--code-bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-s);
  padding: 8px 10px;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0 0 10px;
}
.hp-chat .choices { display: flex; flex-wrap: wrap; gap: 8px; }
.hp-chat .choices button {
  font: inherit;
  font-size: 13px;
  border-radius: var(--radius-l);
  padding: 6px 14px;
  cursor: pointer;
  border: 1px solid var(--border);
  background: var(--bg-elevated);
  color: var(--text);
}
.hp-chat .choices button.primary {
  background: var(--accent);
  border-color: var(--accent);
  color: var(--accent-text);
}
.hp-chat .choices button.deny { color: var(--danger); }
.hp-chat .choices button:disabled { opacity: 0.5; cursor: default; }
.hp-chat .earlier-bar {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin: 0 auto 16px;
  min-height: 28px;
  font-size: 12px;
  color: var(--text-tertiary);
  user-select: none;
}
.hp-chat .earlier-btn {
  font: inherit;
  font-size: 12px;
  color: var(--text-secondary);
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius-l);
  padding: 4px 12px;
  cursor: pointer;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}
.hp-chat .earlier-btn:hover {
  background: var(--bg-hover);
  color: var(--text);
  border-color: var(--border);
}
.hp-chat .earlier-btn.retry { color: var(--danger); }
.hp-chat .earlier-spinner {
  width: 13px;
  height: 13px;
  border: 2px solid var(--border);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: hp-spin 0.75s linear infinite;
  flex-shrink: 0;
}
@keyframes hp-spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
.hp-chat .notice {
  font-size: 13px;
  color: var(--text-secondary);
  text-align: center;
  margin: 0 0 16px;
}
.hp-chat .notice.err { color: var(--danger); }
.hp-chat .notice button {
  font: inherit;
  color: var(--accent);
  background: none;
  border: none;
  cursor: pointer;
}
.hp-chat .empty {
  height: 100%;
  display: grid;
  place-items: center;
  text-align: center;
  padding: 24px;
}
.hp-chat .empty h1 {
  margin: 0;
  font-size: 28px;
  font-weight: 600;
  letter-spacing: -0.02em;
}
.hp-chat .center {
  padding: 48px 0;
  text-align: center;
  color: var(--text-secondary);
  font-size: 14px;
}
.hp-chat .center button {
  font: inherit;
  color: var(--accent);
  background: none;
  border: none;
  cursor: pointer;
}
.hp-chat .composer {
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  flex: none;
  max-width: 780px;
  width: 100%;
  margin: 0 auto;
  padding: 8px 20px calc(12px + var(--safe-bottom) + var(--keyboard-inset, 0px));
  transition: padding-bottom 0.1s ease-out;
}
.hp-chat .jump {
  position: absolute;
  left: 50%;
  bottom: calc(100px + var(--task-dock, 0px) + var(--safe-bottom));
  transform: translateX(-50%);
  width: 34px;
  height: 34px;
  border-radius: 50%;
  border: 1px solid var(--border);
  overflow: hidden;
  color: var(--text-secondary);
  box-shadow: var(--shadow);
  display: grid;
  place-items: center;
  cursor: pointer;
  padding: 0;
}
.hp-chat .box {
  position: relative;
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: var(--radius-l);
  box-shadow: var(--shadow);
  transition: border-color 0.15s var(--ease);
}
.hp-chat .box:focus-within { border-color: color-mix(in srgb, var(--accent) 50%, var(--border)); }
.hp-chat .box[data-drag] { border-color: var(--accent); border-style: dashed; }
.hp-chat .atts { display: flex; flex-wrap: wrap; gap: 8px; padding: 10px 12px 0; }
.hp-chat .att {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  height: 56px;
  max-width: 220px;
  border: 1px solid var(--border);
  border-radius: var(--radius-m);
  background: var(--bg);
  overflow: hidden;
}
.hp-chat .att img { width: 56px; height: 56px; object-fit: cover; }
.hp-chat .att .file { padding: 0 28px 0 10px; min-width: 0; font-size: 12px; }
.hp-chat .att .file b {
  display: block;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.hp-chat .att .file span { color: var(--text-tertiary); }
.hp-chat .att .x {
  position: absolute;
  top: 3px;
  right: 3px;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  border: none;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  display: grid;
  place-items: center;
  cursor: pointer;
  padding: 0;
}
.hp-chat .att.busy {
  width: 56px;
  justify-content: center;
  color: var(--text-tertiary);
  font-size: 12px;
}
.hp-chat .box > .row {
  display: flex;
  align-items: flex-end;
  gap: 6px;
  padding: 8px 8px 8px 6px;
}
.hp-chat .box textarea {
  flex: 1;
  resize: none;
  border: none;
  outline: none;
  background: none;
  color: var(--text);
  font: inherit;
  font-size: 16px;
  line-height: 24px;
  box-sizing: border-box;
  padding: 6px 4px;
  max-height: 240px;
  min-height: 36px;
  overflow-y: hidden;
}
.hp-chat .composer > textarea.measure {
  position: fixed;
  top: 0;
  left: 0;
  visibility: hidden;
  pointer-events: none;
  height: 0;
  min-height: 0;
  max-height: none;
  overflow: hidden;
  resize: none;
  border: none;
  margin: 0;
  padding: 6px 4px;
  box-sizing: border-box;
  font: inherit;
  font-size: 16px;
  line-height: 24px;
  white-space: pre-wrap;
}
.hp-chat .box textarea::placeholder { color: var(--text-tertiary); }
.hp-chat .box .btn {
  flex: none;
  width: 34px;
  height: 34px;
  margin-bottom: 1px;
  border-radius: 50%;
  border: none;
  display: grid;
  place-items: center;
  cursor: pointer;
  padding: 0;
  transition: opacity 0.15s var(--ease);
}
.hp-chat .box .btn:disabled { opacity: 0.35; cursor: default; }
.hp-chat .send { background: var(--accent); color: var(--accent-text); }
.hp-chat .stop { background: var(--text); color: var(--bg); }
.hp-chat .clip {
  position: relative;
  overflow: hidden;
  background: none;
  color: var(--text-secondary);
}
.hp-chat .clip:hover:not([data-disabled]) { background: var(--bg-hover); color: var(--text); }
.hp-chat .clip[data-disabled] { opacity: 0.35; cursor: default; }
.hp-chat .clip input[type="file"] {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  opacity: 0;
  cursor: pointer;
  font-size: 0;
}
.hp-chat .clip input[type="file"]:disabled { cursor: default; }
.hp-chat .foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 6px;
  min-height: 24px;
  font-size: 11px;
  color: var(--text-tertiary);
}
.hp-chat .foot .err, .hp-chat .foot .hint {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.hp-chat .foot .err { color: var(--danger); }
.hp-chat .tools {
  display: flex;
  align-items: center;
  gap: 2px;
  min-width: 0;
  margin-left: auto;
}
.hp-chat .model-picker, .hp-chat .effort-picker { position: relative; display: inline-block; min-width: 0; }
.hp-chat .effort-picker { flex: none; }
.hp-chat .context-ring { position: relative; display: inline-flex; align-items: center; flex: none; }
.hp-chat .model-picker .trigger, .hp-chat .effort-picker .trigger {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 100%;
  font: inherit;
  font-size: 12px;
  color: var(--text-secondary);
  background: none;
  border: none;
  border-radius: var(--radius-s);
  padding: 3px 6px;
  cursor: pointer;
}
.hp-chat .effort-picker .trigger { white-space: nowrap; }
.hp-chat .model-picker .trigger:hover, .hp-chat .effort-picker .trigger:hover,
.hp-chat .context-ring .wrap:hover { background: var(--bg-hover); color: var(--text); }
.hp-chat .model-picker .trigger span {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.hp-chat .model-picker .menu {
  position: absolute;
  transform-origin: bottom left;
  bottom: calc(100% + 6px);
  z-index: 30;
  min-width: 240px;
  max-width: calc(100vw - 32px);
  max-height: min(60vh, 420px);
  overflow: hidden;
  padding: 0;
  border: 1px solid var(--border);
  border-radius: var(--radius-l);
  box-shadow: var(--shadow);
}
.hp-chat .model-picker .menu .sheet {
  max-height: calc(min(60vh, 420px) - 2px);
  overflow-y: auto;
  padding: 6px;
}
.hp-chat .model-picker h4 {
  margin: 6px 8px 4px;
  font-size: 11px;
  font-weight: 500;
  color: var(--text-tertiary);
}
.hp-chat .model-picker .menu button {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font: inherit;
  font-size: 13px;
  color: var(--text);
  text-align: left;
  background: none;
  border: none;
  border-radius: var(--radius-s);
  padding: 7px 8px;
  cursor: pointer;
}
.hp-chat .model-picker .menu button:hover { background: var(--bg-hover); }
.hp-chat .model-picker .check { color: var(--accent); }
.hp-chat .model-picker .note { padding: 8px; font-size: 12px; color: var(--text-secondary); }
.hp-chat .effort-picker .panel {
  position: absolute;
  transform-origin: bottom right;
  bottom: calc(100% + 6px);
  z-index: 30;
  width: 264px;
  max-width: calc(100vw - 32px);
  padding: 12px 14px 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius-l);
  box-shadow: var(--shadow);
}
.hp-chat .effort-picker .head { display: flex; align-items: baseline; gap: 10px; font-size: 13px; }
.hp-chat .effort-picker .title { color: var(--text-secondary); }
.hp-chat .effort-picker .cur { font-weight: 600; color: var(--text); }
.hp-chat .effort-picker .cur.ultra {
  background: linear-gradient(90deg, #8f6bff, #ff7ad9, #6cc3ff, #8f6bff);
  background-size: 200% 100%;
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  animation: hp-effort-flow 3s linear infinite;
}
.hp-chat .effort-picker .ends {
  display: flex;
  justify-content: space-between;
  margin: 12px 0 6px;
  font-size: 11px;
  color: var(--text-tertiary);
}
.hp-chat .effort-picker .track {
  --thumb: 14px;
  position: relative;
  height: 28px;
  border-radius: var(--radius-l);
  background: var(--bg-hover);
  overflow: hidden;
}
.hp-chat .effort-picker .track::before {
  content: "";
  position: absolute;
  inset: 0 auto 0 0;
  width: calc(var(--thumb) / 2 + (100% - var(--thumb)) * var(--f));
  background: color-mix(in srgb, var(--accent) 28%, transparent);
  transition: width 0.12s ease-out;
}
.hp-chat .effort-picker .dots i {
  position: absolute;
  top: 50%;
  left: calc(var(--thumb) / 2 + (100% - var(--thumb)) * var(--p));
  width: 4px;
  height: 4px;
  margin: -2px 0 0 -2px;
  border-radius: 50%;
  background: var(--text-tertiary);
  opacity: 0.7;
}
.hp-chat .effort-picker input[type="range"] {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  margin: 0;
  padding: 0;
  background: transparent;
  appearance: none;
  -webkit-appearance: none;
  cursor: pointer;
  touch-action: pan-y;
  z-index: 2;
}
.hp-chat .effort-picker input[type="range"]:focus { outline: none; }
.hp-chat .effort-picker .track:has(input:focus-visible) { outline: 2px solid var(--accent); outline-offset: 2px; }
.hp-chat .effort-picker input[type="range"]::-webkit-slider-runnable-track { height: 100%; background: transparent; }
.hp-chat .effort-picker input[type="range"]::-moz-range-track { height: 100%; background: transparent; }
.hp-chat .effort-picker input[type="range"]::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: var(--thumb);
  height: 24px;
  margin-top: 2px;
  border: none;
  border-radius: var(--radius-l);
  background: #fff;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.35);
}
.hp-chat .effort-picker input[type="range"]::-moz-range-thumb {
  width: var(--thumb);
  height: 24px;
  border: none;
  border-radius: var(--radius-l);
  background: #fff;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.35);
}
.hp-chat .effort-picker .track.ultra {
  background: linear-gradient(90deg, #2a1c63, #5636c9, #b24bd6, #5636c9, #2a1c63);
  background-size: 200% 100%;
  box-shadow: 0 0 16px rgb(143 107 255 / 0.5);
  animation: hp-effort-flow 3.2s linear infinite;
}
.hp-chat .effort-picker .track.ultra::before { display: none; }
.hp-chat .effort-picker .track.ultra {
  --ultra-crab: 37.18px;
}
.hp-chat .effort-picker .track.ultra .dots i {
  background: #fff;
  opacity: 0.35;
  left: calc(var(--thumb) / 2 + (100% - var(--thumb) - var(--ultra-crab, 0px)) * var(--p));
}
.hp-chat .effort-picker .track.ultra::after {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
  background-image:
    radial-gradient(circle, rgb(255 255 255 / 0.95) 0 1px, transparent 1.6px),
    radial-gradient(circle, rgb(255 255 255 / 0.75) 0 1px, transparent 1.6px),
    radial-gradient(circle, rgb(225 195 255 / 0.9) 0 1px, transparent 1.6px);
  background-size: 23px 11px, 37px 17px, 17px 13px;
  background-position: 0 0, 7px 5px, 3px 2px;
  animation: hp-effort-sparkle 4s linear infinite;
  z-index: 1;
}
.hp-chat .effort-picker .track:has(.ultra-crab) input[type="range"]::-webkit-slider-thumb {
  width: 0;
  height: 0;
  margin-top: 0;
  border: 0;
  opacity: 0;
  background: transparent;
  box-shadow: none;
}
.hp-chat .effort-picker .track:has(.ultra-crab) input[type="range"]::-moz-range-thumb {
  width: 0;
  height: 0;
  border: 0;
  opacity: 0;
  background: transparent;
  box-shadow: none;
}
.hp-chat .effort-picker .track.ultra input[type="range"]::-moz-range-thumb {
  width: 0;
  height: 0;
  margin: 0;
  border: 0;
  opacity: 0;
  background: transparent;
  box-shadow: none;
  animation: none;
}
.hp-chat .effort-picker .hint {
  margin: 8px 0 0;
  height: 18px;
  font-size: 12px;
  line-height: 18px;
  color: var(--text-tertiary);
  text-align: center;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
@keyframes hp-effort-flow { to { background-position: -200% 0; } }
@keyframes hp-effort-sparkle {
  0% { background-position: 0 0, 7px 5px, 3px 2px; opacity: 0.55; }
  50% { opacity: 1; }
  100% { background-position: -46px 0, -74px 5px, -34px 2px; opacity: 0.6; }
}
@keyframes hp-effort-pulse { 50% { box-shadow: 0 0 18px 4px rgb(215 190 255 / 1); } }
@media (prefers-reduced-motion: reduce) {
  .hp-chat .effort-picker .cur.ultra,
  .hp-chat .effort-picker .track.ultra,
  .hp-chat .effort-picker .track.ultra::after,
  .hp-chat .effort-picker .track.ultra input[type="range"]::-webkit-slider-thumb,
  .hp-chat .effort-picker .track.ultra input[type="range"]::-moz-range-thumb { animation: none; }
  .hp-chat .effort-picker .track::before { transition: none; }
}
.hp-chat .effort-picker .track-slot { position: relative; overflow: visible; }
.hp-chat .effort-picker .ultra-crab {
  position: absolute;
  top: 0;
  left: calc((100% - var(--ultra-crab, 37.18px)) * var(--f, 1));
  height: 100%;
  width: var(--ultra-crab, 37.18px);
  pointer-events: auto;
  touch-action: none;
  z-index: 5;
  overflow: visible;
}
.hp-chat .effort-picker .ultra-crab-svg {
  position: absolute;
  overflow: visible;
  transform-origin: 0 0;
}
.hp-chat .effort-picker .ultra-notes {
  position: absolute;
  left: calc(100% - var(--ultra-crab, 37.18px) + var(--note-x, 10px));
  top: var(--note-y, 12px);
  width: 24px;
  height: 24px;
  overflow: visible;
  pointer-events: none;
  z-index: 4;
}
.hp-chat .effort-picker .bang { transform-origin: 190px 280px; animation: bang .5s ease-in-out infinite; }
@keyframes bang { 0%, 100% { transform: translateY(0) rotate(-5deg) scale(1, 1); } 25%, 75% { transform: translateY(7px) rotate(0deg) scale(1.03, .96); } 50% { transform: translateY(0) rotate(5deg) scale(1, 1); } }
.hp-chat .effort-picker .hat { transform-origin: 190px 100px; animation: hat .25s ease-in-out infinite alternate; }
@keyframes hat { from { transform: translateY(0) rotate(-3deg); } to { transform: translateY(-9px) rotate(3deg); } }
.hp-chat .effort-picker .guitar { transform-origin: 190px 230px; animation: gt .25s ease-in-out infinite alternate; }
@keyframes gt { from { transform: rotate(-4deg); } to { transform: rotate(4deg); } }
.hp-chat .effort-picker .strum { animation: st .11s ease-in-out infinite alternate; }
@keyframes st { from { transform: translateY(-9px); } to { transform: translateY(5px); } }
.hp-chat .effort-picker .la, .hp-chat .effort-picker .lb { animation: lg .25s ease-in-out infinite alternate; }
.hp-chat .effort-picker .lb { animation-delay: -.25s; }
@keyframes lg { from { transform: translateY(0); } to { transform: translateY(-9px); } }
.hp-chat .effort-picker .sl { animation: fl .16s steps(1) infinite alternate; }
@keyframes fl { from { opacity: 0; } to { opacity: 1; } }
.hp-chat .effort-picker .fly { opacity: 0; animation: fly var(--d, 2s) linear infinite; animation-delay: var(--l, 0s); }
@keyframes fly { 0% { opacity: 0; transform: translate(0px, 0px) rotate(0deg) scale(.3); } 2% { opacity: 1; transform: translate(calc(var(--dx) * .1), calc(var(--dy) * .1)) rotate(calc(var(--r) * .1)) scale(1); } 12% { opacity: 1; transform: translate(var(--dx), var(--dy)) rotate(var(--r)) scale(1.1); } 15% { opacity: 0; transform: translate(var(--dx), var(--dy)) rotate(var(--r)) scale(1.1); } 100% { opacity: 0; transform: translate(0px, 0px) rotate(0deg) scale(.3); } }
.hp-chat .effort-picker .wob { animation: wob .6s ease-in-out infinite alternate; }
@keyframes wob { from { transform: translateX(-9px); } to { transform: translateX(9px); } }
@media (prefers-reduced-motion: reduce) {
  .hp-chat .effort-picker .bang,
  .hp-chat .effort-picker .hat,
  .hp-chat .effort-picker .guitar,
  .hp-chat .effort-picker .strum,
  .hp-chat .effort-picker .la,
  .hp-chat .effort-picker .lb,
  .hp-chat .effort-picker .sl,
  .hp-chat .effort-picker .fly,
  .hp-chat .effort-picker .wob { animation: none; }
}
.hp-chat .context-ring .wrap {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 6px;
  font: inherit;
  font-size: 12px;
  color: var(--text-secondary);
  background: none;
  border: none;
  border-radius: var(--radius-s);
  cursor: pointer;
}
.hp-chat .context-ring svg { transform: rotate(-90deg); }
.hp-chat .context-ring .track { stroke: var(--border); }
.hp-chat .context-ring .fill { stroke: var(--accent, #0a84ff); transition: stroke-dashoffset 0.4s ease; }
.hp-chat .context-ring .fill[data-level="warn"] { stroke: var(--warning, #ff9f0a); }
.hp-chat .context-ring .fill[data-level="danger"],
.hp-chat .context-ring .fill[data-level="full"] { stroke: var(--danger, #ff453a); }
.hp-chat .context-ring .pop {
  box-sizing: border-box;
  position: absolute;
  transform-origin: top left;
  bottom: calc(100% + 6px);
  z-index: 30;
  width: min(320px, calc(100vw - 32px));
  padding: 12px 14px;
  display: grid;
  gap: 10px;
  font-size: 12px;
  color: var(--text-secondary);
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius-l);
  box-shadow: var(--shadow);
}
.hp-chat .context-ring .pop .row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}
.hp-chat .context-ring .pop .row b { font-weight: 600; color: var(--text); }
.hp-chat .context-ring .pop .row span:last-child { font-variant-numeric: tabular-nums; color: var(--text); }
.hp-chat .context-ring h4 { margin: 0; font-size: 11px; font-weight: 500; color: var(--text-tertiary); }
.hp-chat .context-ring .bar {
  display: flex;
  height: 6px;
  border-radius: var(--radius-l);
  overflow: hidden;
  background: var(--border);
}
.hp-chat .context-ring .bar i { display: block; height: 100%; }
.hp-chat .context-ring .bar .cached { background: var(--accent); opacity: 0.45; }
.hp-chat .context-ring .bar .fresh { background: var(--accent); }
.hp-chat .context-ring .bar[data-level="warn"] .fresh,
.hp-chat .context-ring .bar[data-level="warn"] .cached { background: var(--warning); }
.hp-chat .context-ring .bar[data-level="danger"] .fresh,
.hp-chat .context-ring .bar[data-level="danger"] .cached,
.hp-chat .context-ring .bar[data-level="full"] .fresh,
.hp-chat .context-ring .bar[data-level="full"] .cached { background: var(--danger, #ff453a); }
.hp-chat .context-ring .legend { display: flex; gap: 12px; color: var(--text-tertiary); }
.hp-chat .context-ring .legend i {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 4px;
  background: var(--accent);
}
.hp-chat .context-ring .legend i.c { opacity: 0.45; }
.hp-chat .context-ring .sec {
  display: grid;
  gap: 6px;
  padding-top: 10px;
  border-top: 1px solid var(--border);
}
.hp-chat .context-ring .note { color: var(--text-tertiary); line-height: 1.5; }
.hp-chat .context-ring code { font: 11px var(--font-mono); }
.hp-chat .turn-summary { display: block; margin-top: 8px; font: 12px var(--font-mono); color: var(--text-tertiary); }
.hp-chat .turn-summary .failed { color: var(--danger); }
.hp-chat .run-status { display: block; font: 14px var(--font-mono); }
.hp-chat .run-status .line { display: flex; flex-wrap: wrap; align-items: baseline; column-gap: 6px; row-gap: 2px; min-width: 0; }
.hp-chat .run-status .spinner { display: inline-block; color: var(--claude, #d97757); animation: hp-turn 2s steps(6) infinite; flex: 0 0 auto; }
@keyframes hp-turn { to { transform: rotate(360deg); } }
.hp-chat .run-status .main { display: inline-block; position: relative; min-width: 0; overflow-wrap: anywhere; color: var(--claude, #d97757); }
.hp-chat .run-status .main .glow {
  position: absolute;
  inset: 0;
  color: var(--claude-shimmer, #efb49f);
  pointer-events: none;
  user-select: none;
  opacity: .15;
  animation: hp-breathe 2.4s ease-in-out infinite;
}
@keyframes hp-breathe { 0%, 100% { opacity: .15; } 50% { opacity: .95; } }
.hp-chat .run-status .elapsed, .hp-chat .run-status .detail { font-size: .86em; color: var(--text-tertiary); font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.hp-chat .run-status .retry .main, .hp-chat .run-status .retry .spinner { color: var(--danger, #e85d66); animation: none; }
.hp-chat .run-status .retry .main .glow { display: none; }
.hp-chat .run-status .note { margin-top: 4px; font-size: .86em; line-height: 1.5; color: var(--text-tertiary); }
@media (prefers-reduced-motion: reduce) {
  .hp-chat .run-status .spinner, .hp-chat .run-status .main .glow { animation: none; }
  .hp-chat .run-status .main .glow { display: none; }
}
.hp-chat .bubble.msg-editor { display: block; position: relative; min-width: 0; }
.hp-chat .msg-editor .root { white-space: normal; }
.hp-chat .msg-editor .message-text { white-space: pre-wrap; overflow-wrap: anywhere; }
.hp-chat .msg-editor button {
  font: inherit;
  color: var(--text);
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius-s);
  cursor: pointer;
  padding: 6px 10px;
}
.hp-chat .msg-editor button:hover { background: var(--bg-hover); }
.hp-chat .msg-editor button:focus-visible, .hp-chat .msg-editor textarea:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.hp-chat .msg-editor button:disabled, .hp-chat .msg-editor button[aria-disabled="true"] { color: var(--text-tertiary); cursor: not-allowed; }
.hp-chat .msg-editor .trigger { position: absolute; right: -4px; top: -4px; width: 28px; height: 28px; padding: 0; opacity: 0; }
.hp-chat .msg-editor .trigger:focus { opacity: 1; }
.hp-chat .msg-editor .menu {
  position: absolute;
  transform-origin: bottom left;
  z-index: 40;
  width: 240px;
  max-width: calc(100vw - 32px);
  max-height: min(60vh, 320px);
  overflow-y: auto;
  padding: 6px;
  background: var(--bg-elevated);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: var(--radius-l);
  box-shadow: var(--shadow);
}
.hp-chat .msg-editor .menu button { display: block; width: 100%; border: 0; text-align: left; min-height: 36px; }
.hp-chat .msg-editor textarea {
  display: block;
  width: 100%;
  min-height: 64px;
  max-height: 240px;
  resize: none;
  font: inherit;
  font-size: 16px;
  line-height: 1.5;
  padding: 8px;
  border: 1px solid var(--border);
  border-radius: var(--radius-m);
  color: var(--text);
  background: var(--bg-elevated);
  -webkit-user-select: text;
  user-select: text;
}
.hp-chat .msg-editor .note, .hp-chat .msg-editor .error { font-size: 12px; line-height: 1.5; margin: 8px 0; overflow-wrap: anywhere; }
.hp-chat .msg-editor .note { color: var(--text-secondary); }
.hp-chat .msg-editor .menu .note, .hp-chat .msg-editor .menu .error { padding: 0 8px; }
.hp-chat .msg-editor .error { color: var(--danger); }
.hp-chat .msg-editor .send { margin-left: 6px; color: var(--accent); }
@media (pointer: coarse) {
  .hp-chat .hint { display: none; }
  .hp-chat .foot { margin-top: 2px; }
  .hp-chat .msg-editor .message-text { -webkit-touch-callout: none; -webkit-user-select: none; user-select: none; }
  .hp-chat .msg-editor .trigger { pointer-events: none; width: 36px; height: 36px; }
  .hp-chat .msg-editor .trigger:focus { pointer-events: auto; }
  .hp-chat .msg-editor .menu button { min-height: 44px; }
}
.hp-chat .user-atts { display: block; max-width: 85%; min-width: 0; }
.hp-chat .user-atts button { font: inherit; color: var(--accent); cursor: pointer; border: 0; background: none; }
.hp-chat .user-atts button:disabled { opacity: .5; cursor: default; }
.hp-chat .user-atts .imgs { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }
.hp-chat .user-atts .thumbnail {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  max-width: 160px;
  min-width: 44px;
  min-height: 44px;
  padding: 0;
  border-radius: var(--radius-m);
  overflow: hidden;
}
.hp-chat .user-atts .thumbnail img { display: block; max-width: 100%; max-height: 160px; width: auto; height: auto; object-fit: contain; }
.hp-chat .user-atts .thumbnail span { display: block; padding: 12px; font-size: 13px; }

.hp-chat .user-atts .files { display: flex; flex-direction: column; align-items: flex-end; gap: 6px; }
.hp-chat .user-atts .imgs + .files:not(:empty) { margin-top: 6px; }
.hp-chat .user-atts .file {
  max-width: 100%;
  font-size: 13px;
  border: 1px solid var(--border);
  border-radius: var(--radius-m);
  background: var(--bg-elevated);
  overflow-wrap: anywhere;
}
.hp-chat .user-atts .file > button, .hp-chat .user-atts summary, .hp-chat .user-atts .dropped { padding: 8px 12px; }
.hp-chat .user-atts .file > button { text-align: left; max-width: 100%; overflow-wrap: anywhere; }
.hp-chat .user-atts summary { cursor: pointer; color: var(--text-secondary); }
.hp-chat .user-atts .file pre {
  margin: 0;
  max-height: 320px;
  overflow: auto;
  padding: 8px 12px;
  border-top: 1px solid var(--border);
  font: 12px/1.5 var(--font-mono);
  white-space: pre-wrap;
  word-break: break-all;
  text-align: left;
}
.hp-chat .user-atts .dropped, .hp-chat .user-atts .status { color: var(--text-secondary); }
.hp-chat .user-atts .error { color: var(--danger, #ff6b6b); }
.hp-chat .user-atts .error, .hp-chat .user-atts .status { font-size: 13px; margin-top: 6px; overflow-wrap: anywhere; }
.hp-chat .user-atts dialog,
.hp-chat .assistant-body dialog {
  max-width: calc(100vw - 32px);
  max-height: calc(100dvh - 32px);
  margin: auto;
  padding: 16px;
  border: 1px solid var(--border);
  border-radius: var(--radius-l);
  background: var(--bg-elevated);
  color: var(--text);
  overflow: auto;
}
.hp-chat .user-atts dialog::backdrop,
.hp-chat .assistant-body dialog::backdrop { background: rgba(0, 0, 0, .75); }
.hp-chat .user-atts dialog img,
.hp-chat .assistant-body dialog img { display: block; max-width: 100%; max-height: calc(100dvh - 140px); object-fit: contain; }
.hp-chat .user-atts dialog img[hidden], .hp-chat .user-atts [hidden] { display: none; }
.hp-chat .user-atts .actions,
.hp-chat .assistant-body dialog .actions { display: flex; justify-content: flex-end; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.hp-chat .user-atts .actions button,
.hp-chat .assistant-body dialog .actions button { border: 1px solid var(--border); border-radius: var(--radius-m); padding: 8px 12px; color: var(--text); background: var(--bg); }
.hp-chat .tool-steps { display: block; max-width: 100%; min-width: 0; font-size: 13px; }
.hp-chat .tool-steps .summary {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
  min-width: 0;
  font: inherit;
  color: var(--text-secondary);
  background: none;
  border: none;
  padding: 4px 6px;
  margin-left: -6px;
  border-radius: var(--radius-s);
  cursor: pointer;
  text-align: left;
}
.hp-chat .tool-steps .summary .now { display: inline-flex; gap: 6px; min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.hp-chat .tool-steps .summary .dot, .hp-chat .tool-steps .summary .fail { flex: none; }
.hp-chat .tool-steps .watch {
  font: inherit;
  font-size: 12px;
  color: var(--accent);
  background: color-mix(in srgb, var(--accent) 12%, transparent);
  border: none;
  border-radius: var(--radius-s);
  padding: 2px 8px;
  margin-left: 4px;
  cursor: pointer;
}
@media (hover: hover) {
  .hp-chat .tool-steps .summary:hover { background: var(--bg-hover); color: var(--text); }
  .hp-chat .tool-steps .row:hover { background: var(--bg-hover); }
}
.hp-chat .tool-steps .fail { color: var(--danger); }
.hp-chat .tool-steps ol { list-style: none; margin: 4px 0 0; padding: 0 0 0 10px; border-left: 2px solid var(--border); }
.hp-chat .tool-steps li { margin: 2px 0; }
.hp-chat .tool-steps .fold { display: grid; grid-template-rows: 0fr; transition: grid-template-rows 0.28s cubic-bezier(0.32, 0.72, 0, 1); }
.hp-chat .tool-steps .fold[data-open] { grid-template-rows: 1fr; }
.hp-chat .tool-steps .fold > div { overflow: hidden; min-height: 0; }
.hp-chat .tool-steps .row {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  font: inherit;
  color: inherit;
  text-align: left;
  background: none;
  border: none;
  border-radius: var(--radius-s);
  padding: 4px 6px;
  cursor: pointer;
  min-width: 0;
}
.hp-chat .tool-steps .dot { width: 7px; height: 7px; border-radius: 50%; flex: none; background: var(--success); }
.hp-chat .tool-steps .dot[data-s="running"] { background: var(--accent); animation: hp-step-pulse 1.2s ease-in-out infinite; }
.hp-chat .tool-steps .dot[data-s="failed"] { background: var(--danger); }
@keyframes hp-step-pulse { 50% { opacity: 0.3; } }
.hp-chat .tool-steps .name { flex: 0 0 auto; min-width: 4.5em; max-width: 42%; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.hp-chat .tool-steps .hint { flex: none; color: var(--text-tertiary); font-size: 11px; }
.hp-chat .tool-steps .preview { display: flex; gap: 8px; margin: 0 0 4px 21px; color: var(--text-secondary); font: 12px/1.5 var(--font-mono); min-width: 0; }
.hp-chat .tool-steps .preview pre { margin: 0; min-width: 0; max-height: 360px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; }
.hp-chat .tool-steps .preview[data-s="failed"] { color: var(--danger); }
.hp-chat .tool-steps .branch { flex: none; color: var(--text-tertiary); }
.hp-chat .tool-steps .arg { flex: 1 1 0; color: var(--text-secondary); font-family: var(--font-mono); font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.hp-chat .tool-steps .note { color: var(--text-secondary); padding: 4px 6px; min-width: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.hp-chat .tool-steps .detail { margin: 2px 0 6px 21px; border: 1px solid var(--border); border-radius: var(--radius-s); background: var(--code-bg); overflow: hidden; }
.hp-chat .tool-steps .detail summary { padding: 4px 10px; font: 11px var(--font-mono); color: var(--text-tertiary); cursor: pointer; }
.hp-chat .tool-steps .detail h4 { margin: 0; padding: 4px 10px; font-size: 11px; font-weight: 500; color: var(--text-tertiary); border-bottom: 1px solid var(--border); }
.hp-chat .tool-steps .detail pre { margin: 0; padding: 8px 10px; max-height: 360px; overflow: auto; font: 12px/1.5 var(--font-mono); white-space: pre-wrap; word-break: break-all; }
.hp-chat .tool-steps .partial { padding: 0 10px 8px; font-size: 11px; color: var(--text-tertiary); }
.hp-chat .tool-steps .more { font: inherit; font-size: 12px; color: var(--accent); background: none; border: none; padding: 4px 10px 8px; cursor: pointer; }
@media (prefers-reduced-motion: reduce) {
  .hp-chat .tool-steps .dot[data-s="running"] { animation: none; }
  .hp-chat .tool-steps .fold { transition: none; }
}
.hp-chat .composer-panels:has(.bar),
.hp-chat .browser-task { margin-bottom: 8px; }
.hp-chat .browser-task { display: block; min-width: 0; }
.hp-chat .browser-task button {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-width: 0;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-l);
  background: var(--bg-elevated);
  color: var(--text);
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.hp-chat .browser-task button:hover { background: var(--bg-hover); }
.hp-chat .browser-task svg { flex: none; color: var(--accent); }
.hp-chat .browser-task .copy { flex: 1; min-width: 0; }
.hp-chat .browser-task b { display: block; font-size: 13px; font-weight: 550; }
.hp-chat .browser-task small { display: block; margin-top: 3px; font-size: 11px; line-height: 1.4; color: var(--text-secondary); overflow-wrap: anywhere; }
.hp-chat .browser-task .action { flex: none; font-size: 12px; color: var(--accent); }
.hp-chat .subagents,
.hp-chat .task-list { position: relative; z-index: 4; min-height: 32px; }
.hp-chat .subagents .frame,
.hp-chat .task-list .frame {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 5;
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
  max-height: calc(100dvh - 80px);
  padding: 0 10px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-l);
  box-shadow: var(--shadow);
  overflow: hidden;
}
.hp-chat .subagents .frame .fold,
.hp-chat .task-list .frame .fold {
  position: relative;
  left: auto;
  right: auto;
  bottom: auto;
  display: grid;
  grid-template-rows: 0fr;
  min-height: 0;
  transform: translate3d(0, 8px, 0);
  opacity: 0;
  pointer-events: none;
  transition: transform 0.28s cubic-bezier(0.32, 0.72, 0, 1), opacity 0.28s cubic-bezier(0.32, 0.72, 0, 1);
}
.hp-chat .subagents .frame .fold[data-open],
.hp-chat .task-list .frame .fold[data-open] {
  grid-template-rows: 1fr;
  transform: translate3d(0, 0, 0);
  opacity: 1;
  pointer-events: auto;
}
.hp-chat .subagents .frame .fold > div,
.hp-chat .task-list .frame .fold > div {
  overflow: hidden;
  min-height: 0;
  background: none;
  border: none;
  border-radius: 0;
  box-shadow: none;
}
.hp-chat .subagents .frame .list {
  margin: 0;
  padding: 8px 0 4px;
  max-height: min(56vh, 520px);
}
.hp-chat .subagents .bar,
.hp-chat .task-list .bar {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  font: inherit;
  font-size: 12px;
  color: var(--text-secondary);
  background: none;
  border: none;
  padding: 4px 2px;
  cursor: pointer;
  text-align: left;
}
.hp-chat .subagents .bar:hover,
.hp-chat .task-list .bar:hover { color: var(--text); }
.hp-chat .task-list .bar > span,
.hp-chat .subagents .bar > span:not(.spin) { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hp-chat .subagents .bar > span.spin { flex: none; min-width: 10px; overflow: visible; text-overflow: clip; white-space: normal; }
.hp-chat .subagents .bar > .spin,
.hp-chat .task-list .chev,
.hp-chat .subagents .chev { flex: none; }
.hp-chat .subagents .chev,
.hp-chat .task-list .chev { margin-left: auto; transform: rotate(0deg); transform-origin: center; will-change: transform; transition: transform 0.28s cubic-bezier(0.32, 0.72, 0, 1); }
.hp-chat .subagents .bar[aria-expanded="true"] .chev,
.hp-chat .task-list .bar[aria-expanded="true"] .chev { transform: rotate(180deg); }
.hp-chat .composer-panels { position: relative; z-index: 4; }
.hp-chat .composer-panels .task-dock,
.hp-chat .composer-panels .agent-slot { height: 34px; min-height: 34px; }
.hp-chat .composer-panels .agent-slot { display: none; }
.hp-chat .composer-panels:has(.subagents .bar):not(:has(.task-dock)) .agent-slot { display: block; }
.hp-chat .composer-panels .panel-shell {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 5;
  display: flex;
  flex-direction: row;
  align-items: stretch;
  box-sizing: border-box;
  gap: 0;
  max-height: calc(100dvh - 80px);
  padding: 0;
  background: transparent;
  border: none;
  box-shadow: none;
  overflow: visible;
  pointer-events: none;
  transition: gap 0.28s cubic-bezier(0.32, 0.72, 0, 1);
}
.hp-chat .composer-panels .panel-shell:not(:has(.bar)) { display: none; }
.hp-chat .composer-panels .panel-shell:has(.slot-tasks .bar):has(.slot-agents .bar) { gap: 8px; }
.hp-chat .composer-panels .slot-tasks,
.hp-chat .composer-panels .slot-agents {
  flex: 0 0 0px;
  max-width: 0;
  min-width: 0;
  opacity: 0;
  margin: 0;
  padding: 0;
  border: 0 solid transparent;
  border-radius: var(--radius-l);
  background: transparent;
  box-shadow: none;
  overflow: visible;
  pointer-events: none;
  box-sizing: border-box;
  transition: flex-grow 0.28s cubic-bezier(0.32, 0.72, 0, 1), flex-basis 0.28s cubic-bezier(0.32, 0.72, 0, 1), max-width 0.28s cubic-bezier(0.32, 0.72, 0, 1), opacity 0.28s cubic-bezier(0.32, 0.72, 0, 1), margin 0.28s cubic-bezier(0.32, 0.72, 0, 1), padding 0.28s cubic-bezier(0.32, 0.72, 0, 1), transform 0.28s cubic-bezier(0.32, 0.72, 0, 1), border-width 0.28s cubic-bezier(0.32, 0.72, 0, 1);
}
.hp-chat .composer-panels .slot-tasks:has(.bar),
.hp-chat .composer-panels .slot-agents:has(.bar) {
  flex: 1 1 0px;
  max-width: 100%;
  opacity: 1;
  pointer-events: auto;
  height: 34px;
  min-height: 34px;
  border: 1px solid var(--border);
  background: var(--bg);
  box-shadow: var(--shadow);
}
.hp-chat .composer-panels .panel-shell .task-list,
.hp-chat .composer-panels .panel-shell .subagents {
  display: flex;
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  position: static;
  overflow: visible;
}
.hp-chat .composer-panels .panel-shell .frame {
  position: static;
  left: auto;
  right: auto;
  bottom: auto;
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  width: 100%;
  min-width: 0;
  max-height: none;
  padding: 0 10px;
  background: transparent;
  border: none;
  border-radius: 0;
  box-shadow: none;
  overflow: visible;
}
.hp-chat .composer-panels .panel-shell .fold {
  position: absolute;
  left: 0;
  right: 0;
  bottom: calc(100% + 8px);
  z-index: 2;
  display: block;
  height: auto;
  grid-template-rows: none;
  min-height: 0;
  overflow: visible;
  transform-origin: var(--fold-ox, 50%) 100%;
  transform: scale(0.08);
  opacity: 0;
  pointer-events: none;
  transition: transform 0.2s linear, opacity 0.2s linear;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-l);
  box-shadow: var(--shadow);
  padding: 0 10px;
  box-sizing: border-box;
}
.hp-chat .composer-panels .panel-shell .fold[data-open] {
  z-index: 3;
  transform: scale(1);
  opacity: 1;
  pointer-events: auto;
}
.hp-chat .composer-panels .panel-shell .task-list .fold { --fold-ox: calc(25% - 2px); }
.hp-chat .composer-panels .panel-shell .subagents .fold { --fold-ox: calc(75% + 2px); }
.hp-chat .composer-panels .panel-shell:not(:has(.slot-agents .bar)) .task-list .fold,
.hp-chat .composer-panels .panel-shell:not(:has(.slot-tasks .bar)) .subagents .fold { --fold-ox: 50%; }
.hp-chat .composer-panels .panel-shell .fold > div {
  display: block;
  height: auto;
  min-height: 0;
  overflow: visible;
  background: none;
  border: none;
  border-radius: 0;
  box-shadow: none;
}
.hp-chat .composer-panels .bar {
  order: 1;
  flex: 1 1 auto;
  min-width: 0;
  height: 32px;
  min-height: 32px;
  padding: 0;
  line-height: 16px;
  box-sizing: border-box;
  position: relative;
  z-index: 1;
  background: var(--bg);
}
@media (prefers-reduced-motion: reduce) {
  .hp-chat .composer-panels .slot-tasks,
  .hp-chat .composer-panels .slot-agents,
  .hp-chat .composer-panels .panel-shell,
  .hp-chat .composer-panels .panel-shell .fold { transition: none; }
}
.hp-chat .subagents .list { display: grid; grid-template-columns: minmax(0, 1fr); gap: 8px; margin: 4px 0 8px; max-height: min(38vh, 340px); overflow-y: auto; }
.hp-chat .subagents .card { min-width: 0; overflow: hidden; background: var(--bg-elevated); border: 1px solid var(--border); border-radius: var(--radius-l); padding: 10px 12px; display: grid; gap: 4px; }
.hp-chat .subagents .top { display: flex; align-items: center; gap: 8px; min-width: 0; }
.hp-chat .subagents .title { flex: 1; min-width: 0; font-size: 13px; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.hp-chat .subagents .st { flex: none; font-size: 11px; padding: 1px 8px; border-radius: var(--radius-l); background: var(--bg-hover); color: var(--text-secondary); }
.hp-chat .subagents .st[data-s="running"] { color: var(--accent); }
.hp-chat .subagents .st[data-s="stalled"] { color: var(--warning); }
.hp-chat .subagents .st[data-s="done"] { color: var(--success); }
.hp-chat .subagents .spin { flex: none; width: 10px; height: 10px; border-radius: 50%; border: 2px solid var(--border); border-top-color: var(--accent); animation: hp-spin 0.9s linear infinite; }
.hp-chat .subagents .dot { flex: none; width: 8px; height: 8px; border-radius: 50%; background: var(--text-tertiary); }
.hp-chat .subagents .dot[data-s="done"] { background: var(--success); }
.hp-chat .subagents .dot[data-s="stalled"] { background: var(--warning); }
.hp-chat .subagents .meta, .hp-chat .subagents .tool, .hp-chat .subagents .snip { min-width: 0; line-height: 16px; min-height: 16px; font-size: 12px; color: var(--text-tertiary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.hp-chat .subagents .tool { color: var(--text-secondary); }
.hp-chat .subagents .tool code { font: 12px var(--font-mono); line-height: 16px; margin-left: 6px; color: var(--text-tertiary); }
.hp-chat .subagents .view { font: inherit; font-size: 12px; color: var(--accent); background: none; border: none; padding: 0; cursor: pointer; flex: none; }
.hp-chat .clarify {
  position: absolute;
  z-index: 30;
  inset: 0;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  pointer-events: none;
}
.hp-chat .clarify.collapsed { inset: auto 0 calc(96px + var(--safe-bottom) + var(--keyboard-inset, 0px)) 0; }
.hp-chat .clarify .scrim, .hp-chat .clarify .card, .hp-chat .clarify .reopen { pointer-events: auto; }
.hp-chat .clarify .scrim { position: absolute; inset: 0; background: rgba(0, 0, 0, 0.28); }
.hp-chat .clarify .card {
  position: relative;
  width: min(480px, calc(100% - 24px));
  max-height: min(78%, 640px);
  margin: 0 0 calc(12px + var(--safe-bottom));
  overflow: auto;
  border-radius: var(--radius-l);
  background: var(--bg-elevated);
  color: var(--text);
  box-shadow: var(--shadow);
  padding: 4px 16px 16px;
}
.hp-chat .clarify .head, .hp-chat .clarify .nav { display: flex; align-items: center; min-height: 44px; gap: 4px; }
.hp-chat .clarify .title, .hp-chat .clarify .question { margin: 0; font-size: 17px; font-weight: 600; line-height: 1.35; overflow-wrap: anywhere; }
.hp-chat .clarify .title { flex: 1; }
.hp-chat .clarify .nav .title { text-align: center; }
.hp-chat .clarify .question { margin: 2px 0 8px; }
.hp-chat .clarify .icon, .hp-chat .clarify .close {
  width: 44px;
  height: 44px;
  flex: none;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: var(--text-secondary);
  display: grid;
  place-items: center;
  cursor: pointer;
  padding: 0;
}
.hp-chat .clarify .icon:disabled { opacity: 0.35; cursor: default; }
.hp-chat .clarify .hint { margin: 0 0 4px; color: var(--text-secondary); font-size: 12px; }
.hp-chat .clarify .options { border-top: 1px solid var(--border); }
.hp-chat .clarify .option {
  width: 100%;
  min-height: 44px;
  padding: 12px 2px;
  border: 0;
  border-bottom: 1px solid var(--border);
  background: transparent;
  color: inherit;
  font: inherit;
  font-size: 16px;
  text-align: left;
  cursor: pointer;
  overflow-wrap: anywhere;
}
.hp-chat .clarify .option[aria-checked="true"] { background: var(--bg-active); }
.hp-chat .clarify .tag { margin-left: 8px; color: var(--text-secondary); font-size: 12px; }
.hp-chat .clarify input {
  width: 100%;
  min-height: 44px;
  margin-top: 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-l);
  background: var(--bg);
  color: var(--text);
  font: inherit;
  font-size: 16px;
  padding: 10px 14px;
}
.hp-chat .clarify input::placeholder { color: var(--text-secondary); }
.hp-chat .clarify input:focus { outline: none; border-color: color-mix(in srgb, var(--text) 35%, var(--border)); }
.hp-chat .clarify .foot { display: flex; align-items: center; justify-content: flex-end; gap: 12px; margin-top: 12px; min-height: 0; font-size: inherit; color: inherit; }
.hp-chat .clarify .error { margin: 0; color: var(--danger); font-size: 13px; overflow-wrap: anywhere; }
.hp-chat .clarify .next, .hp-chat .clarify .reopen {
  min-height: 44px;
  border: 0;
  border-radius: var(--radius-l);
  background: var(--text);
  color: var(--bg-elevated);
  font: inherit;
  font-size: 15px;
  font-weight: 600;
  padding: 0 18px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.hp-chat .clarify .next:disabled { opacity: 0.45; cursor: default; }
.hp-chat .clarify .reopen { margin: 0 auto; }
.hp-chat .hp-md, .hp-chat .md { overflow-wrap: anywhere; line-height: 1.65; }
.hp-chat .hp-md > :first-child, .hp-chat .md > :first-child { margin-top: 0; }
.hp-chat .hp-md > :last-child, .hp-chat .md > :last-child { margin-bottom: 0; }
.hp-chat .hp-md p, .hp-chat .hp-md ul, .hp-chat .hp-md ol, .hp-chat .hp-md blockquote, .hp-chat .hp-md table, .hp-chat .hp-md .code,
.hp-chat .md p, .hp-chat .md ul, .hp-chat .md ol, .hp-chat .md blockquote, .hp-chat .md table, .hp-chat .md .code { margin: 0 0 12px; }
.hp-chat .hp-md h1, .hp-chat .hp-md h2, .hp-chat .hp-md h3, .hp-chat .hp-md h4,
.hp-chat .md h1, .hp-chat .md h2, .hp-chat .md h3, .hp-chat .md h4 { margin: 20px 0 8px; line-height: 1.3; letter-spacing: -0.01em; }
.hp-chat .hp-md h1, .hp-chat .md h1 { font-size: 1.4em; }
.hp-chat .hp-md h2, .hp-chat .md h2 { font-size: 1.25em; }
.hp-chat .hp-md h3, .hp-chat .md h3 { font-size: 1.1em; }
.hp-chat .hp-md ul, .hp-chat .hp-md ol, .hp-chat .md ul, .hp-chat .md ol { padding-left: 1.4em; }
.hp-chat .hp-md li + li, .hp-chat .md li + li { margin-top: 4px; }
.hp-chat .hp-md a, .hp-chat .md a { color: var(--accent); text-decoration: none; }
.hp-chat .hp-md a:hover, .hp-chat .md a:hover { text-decoration: underline; }
.hp-chat .hp-md blockquote, .hp-chat .md blockquote { padding: 2px 0 2px 12px; border-left: 3px solid var(--border); color: var(--text-secondary); }
.hp-chat .hp-md hr, .hp-chat .md hr { border: none; border-top: 1px solid var(--border); margin: 16px 0; }
.hp-chat .hp-md img, .hp-chat .md img { max-width: 100%; border-radius: var(--radius-m); }
.hp-chat .hp-md table, .hp-chat .md table { display: block; overflow-x: auto; border-collapse: collapse; font-size: 14px; }
.hp-chat .hp-md th, .hp-chat .hp-md td, .hp-chat .md th, .hp-chat .md td { border: 1px solid var(--border); padding: 6px 10px; text-align: left; }
.hp-chat .hp-md th, .hp-chat .md th { background: var(--bg-hover); font-weight: 600; }
.hp-chat .hp-md :not(pre) > code, .hp-chat .md :not(pre) > code { font-family: var(--font-mono); font-size: 0.88em; padding: 1px 5px; border-radius: var(--radius-l); background: var(--bg-hover); }
.hp-chat .hp-md .code, .hp-chat .md .code { border: 1px solid var(--border); border-radius: var(--radius-m); overflow: hidden; background: var(--code-bg); }
.hp-chat .hp-md .code-head, .hp-chat .md .code-head { display: flex; align-items: center; justify-content: space-between; padding: 4px 6px 4px 12px; font-size: 12px; color: var(--text-secondary); border-bottom: 1px solid var(--border); }
.hp-chat .hp-md .code-head button, .hp-chat .md .code-head button { font: inherit; color: var(--text-secondary); background: none; border: none; border-radius: var(--radius-s); padding: 2px 8px; cursor: pointer; }
.hp-chat .hp-md .code-head button:hover, .hp-chat .md .code-head button:hover { background: var(--bg-hover); color: var(--text); }
.hp-chat .hp-md pre, .hp-chat .md pre { margin: 0; padding: 12px; overflow-x: auto; font: 13px/1.55 var(--font-mono); tab-size: 4; }
.hp-chat .assistant-body { display: block; min-width: 0; }
.hp-chat .assistant-body .artifacts { display: grid; gap: 12px; margin: 12px 0; min-width: 0; }
.hp-chat .assistant-body .artifact { min-width: 0; border: 1px solid var(--border); border-radius: var(--radius-l); padding: 12px; overflow-wrap: anywhere; }
.hp-chat .assistant-body .name { display: block; font-weight: 600; margin-bottom: 8px; }
.hp-chat .assistant-body .artifact a:has(img) {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  min-width: 44px;
  min-height: 44px;
  max-width: 100%;
}
.hp-chat .assistant-body .artifact img { display: block; max-width: 100%; max-height: 360px; height: auto; object-fit: contain; border-radius: var(--radius-l); }
.hp-chat .assistant-body .actions { display: flex; flex-wrap: wrap; gap: 16px; margin-top: 10px; }
.hp-chat .assistant-body .artifact a, .hp-chat .assistant-body .artifact button.save { color: var(--accent); background: none; border: 0; padding: 0; font: inherit; cursor: pointer; }
.hp-chat .assistant-body .error { color: var(--text-secondary); }
.hp-word { display: inline; white-space: pre-wrap; }
.hp-word-in { animation: hp-word-in 380ms cubic-bezier(.22,1,.36,1) both; }
.hp-word-blur { animation-name: hp-word-blur; }
.hp-block-in { animation: hp-block-in 380ms cubic-bezier(.22,1,.36,1) both; }
@keyframes hp-word-in { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }
@keyframes hp-word-blur { from { opacity: 0; transform: translateY(4px); filter: blur(3px); } to { opacity: 1; transform: translateY(0); filter: blur(0); } }
@keyframes hp-block-in { from { opacity: 0; } to { opacity: 1; } }
@media (prefers-reduced-motion: reduce) {
  .hp-word-in, .hp-word-blur, .hp-block-in { animation: none; }
}
@media (max-width: 768px) {
  .hp-chat .col { padding: 16px 14px 8px; }
  .hp-chat .composer { padding: 6px 10px calc(max(4px, calc(var(--safe-bottom) - 26px)) + var(--keyboard-inset, 0px)); }
  .hp-chat .bubble { max-width: 92%; }
  .hp-chat .user-atts { max-width: 92%; }
}
@media (max-width: 560px) {
  .hp-chat .hint { display: none; }
  .hp-chat .foot { margin-top: 2px; }
}
@media (max-width: 460px) {
  .hp-chat .browser-task button { padding: 9px 10px; gap: 8px; }
}
.hp-chat .task-list ul { list-style: none; margin: 0; padding: 8px 0 4px; max-height: min(56vh, 520px); overflow-y: auto; font-size: 13px; color: var(--text-secondary); }
.hp-chat .task-list li { display: flex; align-items: baseline; gap: 8px; margin: 2px 0; min-width: 0; }
.hp-chat .task-list li[data-nested] { margin-left: 16px; }
.hp-chat .task-list .mark { flex: none; width: 1.2em; line-height: 1; color: var(--text-tertiary); }
.hp-chat .task-list li[data-s="in_progress"] .mark { color: var(--accent); }
.hp-chat .task-list li[data-s="completed"] .mark { color: var(--success); }
.hp-chat .task-list li[data-s="in_progress"] .text { color: var(--text); }
.hp-chat .task-list .text { min-width: 0; overflow-wrap: anywhere; }
.hp-chat .task-list li[data-s="completed"] .text { text-decoration: line-through; color: var(--text-tertiary); }
.hp-chat .task-list li[data-s="cancelled"] .text { color: var(--text-tertiary); }
.hp-chat .task-list .tag { flex: none; font-size: 11px; color: var(--text-tertiary); }
.hp-chat .box,
.hp-chat .jump,
.hp-chat .model-picker .menu,
.hp-chat .effort-picker .panel,
.hp-chat .context-ring .pop {
  background-color: var(--glass-solid);
  background-image: var(--glass-fill);
  backdrop-filter: var(--glass-filter);
  -webkit-backdrop-filter: var(--glass-filter);
  isolation: isolate;
  border-color: var(--glass-border);
  box-shadow: var(--shadow), var(--glass-inset);
}
.hp-chat .context-ring .pop {
  color: var(--context-ink, #000);
  background-color: var(--context-glass-solid, rgb(255 255 255 / 0.72));
  backdrop-filter: var(--context-glass-filter, blur(10px) saturate(170%));
  -webkit-backdrop-filter: var(--context-glass-filter, blur(10px) saturate(170%));
}
.hp-chat .context-ring .pop .row span,
.hp-chat .context-ring .pop code {
  color: var(--context-ink, #000);
}
.hp-chat .context-ring .pop h4,
.hp-chat .context-ring .pop .legend,
.hp-chat .context-ring .pop .note {
  color: var(--context-ink-muted, #000);
}
.hp-chat .context-ring .pop .row b,
.hp-chat .context-ring .pop .row span:last-child {
  color: var(--context-ink-strong, #000);
}
.hp-chat .widget-card {
  display: block;
  max-width: 100%;
  min-width: 0;
  margin: 8px 0;
  padding: 12px;
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-l);
  background-color: var(--glass-solid);
  background-image: var(--glass-fill);
  backdrop-filter: var(--glass-filter);
  -webkit-backdrop-filter: var(--glass-filter);
  isolation: isolate;
  box-shadow: var(--shadow), var(--glass-inset);
  color: var(--text);
}
.hp-chat .widget-card header { margin: 0 0 8px; font-size: 13px; font-weight: 600; }
.hp-chat .widget-card .widget-error { margin: 0; color: var(--danger); }
.hp-chat .widget-card .widget-skeleton {
  height: 72px;
  border-radius: var(--radius-s);
  background: var(--bg-hover);
}
.hp-chat .widget-card .widget-svg { min-width: 0; overflow: auto; }
.hp-chat .widget-card .widget-svg svg { display: block; max-width: 100%; height: auto; }
.hp-chat .widget-card iframe {
  display: block;
  width: 100%;
  border: 0;
  background: transparent;
}
`;

export function ChatStyles() {
  return <style>{CSS}</style>;
}

const PROJECT_HOME_DELETE_CSS = `
.project-home .session-line {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}
.project-home .session-line > .row {
  flex: 1 1 auto;
  width: auto;
  min-width: 0;
}
.project-home .session-delete {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  min-width: 44px;
  min-height: 44px;
  margin: 0;
  padding: 0 10px;
  border: 0;
  border-radius: 10px;
  background: transparent;
  color: var(--danger, #e85d66);
  font: inherit;
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
  opacity: 1;
  visibility: visible;
}
.project-home .session-delete:disabled {
  opacity: 0.45;
  cursor: default;
}
.project-home .session-delete-confirm {
  margin: 0 2px 10px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--bg-elevated);
}
.project-home .session-delete-confirm p {
  margin: 0;
  color: var(--text);
  font-size: 14px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.project-home .session-delete-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}
.project-home .session-delete-actions button {
  min-height: 40px;
  padding: 0 12px;
  border-radius: 10px;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--text);
  font: inherit;
  font-size: 14px;
  cursor: pointer;
}
.project-home .session-delete-actions button:first-child {
  color: var(--danger, #e85d66);
}
.project-home .session-delete-actions button:disabled {
  opacity: 0.45;
  cursor: default;
}
.project-home .session-delete-error {
  margin: 8px 0 0;
  color: var(--danger, #e85d66);
  font-size: 13px;
  overflow-wrap: anywhere;
}
.project-home .session-delete:focus-visible,
.project-home .session-delete-actions button:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.project-home-wrap {
  width: 100%;
  max-width: 1080px;
  margin: 0 auto;
  padding: 24px 32px 48px;
}
.project-home-crumbs {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
  color: var(--text-secondary);
  font-size: 14px;
}
.project-home-crumbs button {
  padding: 0;
  border: 0;
  background: none;
  color: inherit;
  font: inherit;
  cursor: pointer;
}
.project-home-crumbs button:hover {
  color: var(--text);
  text-decoration: underline;
}
.project-home-crumbs [aria-current="page"] {
  color: var(--text);
}
.project-home-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 24px;
}
.project-home-head h1 {
  margin: 0;
  font-size: 28px;
  line-height: 1.2;
  overflow-wrap: anywhere;
}
.project-home-rename {
  display: flex;
  flex: 1;
  gap: 8px;
}
.project-home-rename input {
  flex: 1;
  min-width: 0;
  padding: 8px 12px;
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-s);
  background: var(--bg);
  color: var(--text);
  font: inherit;
  font-size: 20px;
}
.project-home-menu {
  position: relative;
  flex: none;
}
.project-home-menu-button {
  width: 36px;
  height: 36px;
  border: 0;
  border-radius: 50%;
  background: none;
  color: var(--text);
  font-size: 20px;
  line-height: 1;
  cursor: pointer;
}
.project-home-menu-button:hover {
  background: var(--bg-hover);
}
.project-home-menu-backdrop {
  position: fixed;
  inset: 0;
  z-index: 40;
  border: 0;
  background: transparent;
  cursor: default;
}
.project-home-menu-list {
  position: absolute;
  top: 40px;
  right: 0;
  z-index: 41;
  display: flex;
  flex-direction: column;
  min-width: 160px;
  padding: 4px;
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-l);
  background: var(--glass-solid);
  box-shadow: var(--shadow);
}
.project-home-menu-list button {
  padding: 8px 12px;
  border: 0;
  border-radius: var(--radius-s);
  background: none;
  color: var(--text);
  font: inherit;
  font-size: 14px;
  text-align: left;
  cursor: pointer;
}
.project-home-menu-list button:hover {
  background: var(--bg-hover);
}
.project-home-error {
  margin: 0 0 12px;
  color: var(--danger, #e85d66);
  font-size: 13px;
}
.project-home-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  gap: 32px;
  align-items: start;
}
.project-home-composer {
  margin-bottom: 32px;
}
.project-home-main h2 {
  margin: 0 0 12px;
  font-size: 15px;
  font-weight: 600;
}
.project-home-main > ul {
  list-style: none;
  margin: 0;
  padding: 0;
}
.project-home-main .session-line {
  border-top: 1px solid var(--glass-border);
}
.project-home-main .session-line .row:hover .title {
  text-decoration: underline;
}
@media (max-width: 900px) {
  .project-home-grid {
    grid-template-columns: minmax(0, 1fr);
  }
  .project-home-wrap {
    padding: 16px 16px 32px;
  }
}
`;

export function ProjectHomeDeleteStyles() {
  return <style>{PROJECT_HOME_DELETE_CSS}</style>;
}
