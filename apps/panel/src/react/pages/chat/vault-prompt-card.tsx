import { useEffect, useRef, useState } from "react";
import { encodeVaultAnswer, vaultMethodLabel, type VaultPrompt } from "@/lib/vault";

// 保险库安全提示卡：保存登录 / 一次性验证码 / 解锁密码管理器。
// 与 ClarifySheet 同一套底部弹层语言（scrim + 卡片），但 z-index 更高、带琥珀色安全标识。
// 秘密只进答案帧：提交后立刻清掉本地输入，绝不写入对话记录、历史或日志。
function formatRemaining(total: number): string {
  const left = Math.max(0, total);
  return `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
}

export function VaultPromptCard({
  prompt,
  onSubmit,
  onDecline,
}: {
  prompt: VaultPrompt;
  onSubmit: (answer: string) => void;
  onDecline: () => void;
}) {
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [remaining, setRemaining] = useState(() =>
    Math.max(0, prompt.timeoutSeconds - Math.floor((Date.now() - prompt.receivedAt) / 1000)),
  );
  const firstRef = useRef<HTMLInputElement>(null);
  const declinedRef = useRef(false);
  const clearSecrets = () => {
    setIdentifier("");
    setPassword("");
    setCode("");
  };
  const decline = () => {
    if (declinedRef.current) return;
    declinedRef.current = true;
    clearSecrets();
    onDecline();
  };
  const declineRef = useRef(decline);
  declineRef.current = decline;
  useEffect(() => {
    firstRef.current?.focus();
  }, []);
  useEffect(() => {
    const timer = window.setInterval(() => {
      const left = Math.max(0, prompt.timeoutSeconds - Math.floor((Date.now() - prompt.receivedAt) / 1000));
      setRemaining(left);
      if (left <= 0) declineRef.current();
    }, 1000);
    return () => window.clearInterval(timer);
  }, [prompt.timeoutSeconds, prompt.receivedAt]);

  const isSave = prompt.method === "vault.save_login";
  const isCode = prompt.method === "vault.code";
  const ready = prompt.pending
    ? false
    : isSave
      ? Boolean(identifier.trim() && password)
      : isCode
        ? Boolean(code.trim())
        : Boolean(password);
  const submit = () => {
    if (!ready) return;
    const answer = encodeVaultAnswer(prompt, { identifier, password, code: code.trim() });
    clearSecrets();
    onSubmit(answer);
  };
  const site = prompt.site || prompt.origin;
  const submitLabel = prompt.pending
    ? "提交中…"
    : isSave
      ? "保存并填写"
      : isCode
        ? "提交验证码"
        : "解锁";

  return (
    <div className="vault">
      <div className="scrim" aria-hidden="true" />
      <section
        className="card"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="vault-title"
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            decline();
          }
        }}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="head">
          <span className="lock" aria-hidden="true">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="4" y="11" width="16" height="10" rx="2" />
              <path d="M8 11V7a4 4 0 0 1 8 0v4" />
            </svg>
          </span>
          <h2 className="title" id="vault-title">{vaultMethodLabel(prompt.method)}</h2>
          <button className="close" type="button" aria-label="取消" disabled={prompt.pending} onClick={decline}>
            <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
              <path d="M3 3l10 10M13 3 3 13" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
            </svg>
          </button>
        </div>
        <p className="origin">
          {isSave ? (
            <>为以下网站保存登录信息，并立即为这个页面填写：</>
          ) : isCode ? (
            <>以下网站要求输入一次性验证码：</>
          ) : (
            <>解锁以下密码管理器，本回合可用：</>
          )}
          <br />
          <code>{isCode || isSave ? prompt.origin || site : prompt.displayName || prompt.backend || site}</code>
        </p>
        {isCode && prompt.hint ? <p className="hint">{prompt.hint}</p> : null}
        <p className="countdown">
          <span>剩余 {formatRemaining(remaining)}</span>
          <span className="bar" aria-hidden="true">
            <i style={{ width: `${(remaining / prompt.timeoutSeconds) * 100}%` }} />
          </span>
        </p>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          {isSave ? (
            <>
              <div className="field">
                <label htmlFor="vault-identifier">用户名 / 邮箱 / 手机号</label>
                <input
                  id="vault-identifier"
                  ref={firstRef}
                  type="text"
                  autoComplete="username"
                  placeholder="用户名 / 邮箱 / 手机号"
                  value={identifier}
                  disabled={prompt.pending}
                  onChange={(event) => setIdentifier(event.target.value)}
                />
              </div>
              <div className="field">
                <label htmlFor="vault-password">密码</label>
                <div className="row">
                  <input
                    id="vault-password"
                    type={showPassword ? "text" : "password"}
                    autoComplete="new-password"
                    placeholder="密码"
                    value={password}
                    disabled={prompt.pending}
                    onChange={(event) => setPassword(event.target.value)}
                  />
                  <button
                    className="show"
                    type="button"
                    disabled={prompt.pending}
                    onClick={() => setShowPassword((v) => !v)}
                  >
                    {showPassword ? "隐藏" : "显示"}
                  </button>
                </div>
              </div>
            </>
          ) : isCode ? (
            <div className="field">
              <label htmlFor="vault-code">一次性验证码</label>
              <input
                id="vault-code"
                ref={firstRef}
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                placeholder="6 位验证码"
                value={code}
                disabled={prompt.pending}
                onChange={(event) => setCode(event.target.value)}
              />
            </div>
          ) : (
            <div className="field">
              <label htmlFor="vault-master">主密码</label>
              <div className="row">
                <input
                  id="vault-master"
                  ref={firstRef}
                  type={showPassword ? "text" : "password"}
                  autoComplete="current-password"
                  placeholder="主密码"
                  value={password}
                  disabled={prompt.pending}
                  onChange={(event) => setPassword(event.target.value)}
                />
                <button
                  className="show"
                  type="button"
                  disabled={prompt.pending}
                  onClick={() => setShowPassword((v) => !v)}
                >
                  {showPassword ? "隐藏" : "显示"}
                </button>
              </div>
            </div>
          )}
          {prompt.error ? <p className="error">{prompt.error}</p> : null}
          <p className="note">密码 / 验证码只会发给本机加密保险库，不会出现在对话记录里。</p>
          <div className="foot">
            <button className="cancel" type="button" disabled={prompt.pending} onClick={decline}>
              取消
            </button>
            <button className="next" type="submit" disabled={!ready}>
              {submitLabel}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
