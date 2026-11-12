import { useRef, useState, type FormEvent } from "react";
import { ApiError, api } from "@/lib/api.js";
import { clearCredential, credentialFor, renewCredential, storeCredential } from "@/lib/auth.js";
import "./login.css";

/** 原 hp-login 的表单、文案、样式及凭据协议；仅改用 React 承载。产品名 clawd。 */
export function Login({ onSuccess }: { onSuccess: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pending = useRef(false);
  const username = useRef<HTMLInputElement>(null);
  const password = useRef<HTMLInputElement>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending.current) return;
    const user = username.current?.value ?? "";
    const secret = password.current?.value ?? "";
    if (!user.trim()) { setError("请输入用户名"); username.current?.focus(); return; }
    if (!secret) { setError("请输入密码"); password.current?.focus(); return; }
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      storeCredential(await credentialFor(user, secret));
      await api.health();
      await renewCredential(true);
      onSuccess();
    } catch (err) {
      if (err instanceof ApiError && err.kind === "unauthorized") {
        clearCredential();
        setError("用户名或密码不正确");
      } else {
        setError(!(err instanceof ApiError) ? "登录失败" : err.kind === "rate-limited" ? "尝试次数过多，请稍后再试" : "暂时连不上服务器");
      }
      password.current?.select();
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }

  return <div className="login-page">
    <form onSubmit={submit} noValidate>
      <div className="mark" aria-hidden="true"><svg width="28" height="28" viewBox="0 0 32 32"><path d="M10 9v14M22 9v14M10 16h12" stroke="white" strokeWidth="3" strokeLinecap="round" fill="none" /></svg></div>
      <h1>clawd</h1>
      <input ref={username} className="user" name="username" type="text" autoComplete="username" autoCapitalize="none" autoCorrect="off" spellCheck={false} placeholder="用户名" aria-label="用户名" disabled={busy} autoFocus />
      <div className="field">
        <input ref={password} name="password" type="password" autoComplete="current-password" placeholder="密码" aria-label="密码" disabled={busy} />
        <button type="submit" aria-label="登录" disabled={busy}><svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8h9M8.5 4.5 12 8l-3.5 3.5" stroke="currentColor" strokeWidth="1.8" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg></button>
      </div>
      <div className="error" role="alert">{error}</div>
    </form>
  </div>;
}
