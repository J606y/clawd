import { useEffect, useRef, useState, type FormEvent } from "react";
import { Head, LoadError, Loading } from "./bits";
import "./account-tab.css";

// 固定 /panel-auth/account，不经 panel-api。续期标记只删 localStorage 的这一键
// （apps/panel/src/react/lib/auth.ts 与 lib/auth.ts 的 RENEW_KEY）。不写 Cookie，不调用注销接口。
const RENEW_STORAGE_KEY = "hp_auth_renewed_at";
const READ_TIMEOUT_MS = 15000;
const WRITE_TIMEOUT_MS = 15000;
const USERNAME_MAX = 64;
const PASSWORD_MAX = 1024;
const PASSWORD_MIN = 12;

let accountSubmitUnresolved = false;

type LoadPhase = "loading" | "ready" | "error" | "expired";

type ShownError = {
  message: string;
  detail?: string;
  code?: string;
};

function codePointCount(value: string): number {
  return Array.from(value).length;
}

const PYTHON_SPACE = new Set<number>([
  0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x1c, 0x1d, 0x1e, 0x1f, 0x20, 0x85, 0xa0,
  0x1680, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007,
  0x2008, 0x2009, 0x200a, 0x2028, 0x2029, 0x202f, 0x205f, 0x3000,
]);

function isPythonSpace(ch: string): boolean {
  return PYTHON_SPACE.has(ch.codePointAt(0) ?? -1);
}

function pythonStrip(value: string): string {
  const chars = Array.from(value);
  let start = 0;
  let end = chars.length;
  while (start < end && isPythonSpace(chars[start])) start += 1;
  while (end > start && isPythonSpace(chars[end - 1])) end -= 1;
  return chars.slice(start, end).join("");
}

function hasLoneSurrogate(value: string): boolean {
  for (let i = 0; i < value.length; i += 1) {
    const unit = value.charCodeAt(i);
    if (unit < 0xd800 || unit > 0xdfff) continue;
    const next = i + 1 < value.length ? value.charCodeAt(i + 1) : 0;
    if (unit <= 0xdbff && next >= 0xdc00 && next <= 0xdfff) {
      i += 1;
      continue;
    }
    return true;
  }
  return false;
}

function normalizeUsername(value: string): string {
  return pythonStrip(value).toLowerCase();
}

function isControlOrDel(ch: string): boolean {
  const cp = ch.codePointAt(0) ?? 0;
  return cp === 0x7f || cp <= 0x1f || (cp >= 0x80 && cp <= 0x9f);
}

function containsNul(value: string): boolean {
  return Array.from(value).some((ch) => (ch.codePointAt(0) ?? -1) === 0);
}

function usernameProblem(raw: string): string {
  if (hasLoneSurrogate(raw)) return "用户名不能包含无效字符";
  const name = normalizeUsername(raw);
  if (hasLoneSurrogate(name)) return "用户名不能包含无效字符";
  const count = codePointCount(name);
  if (count < 1) return "请输入用户名";
  if (count > USERNAME_MAX) return "用户名须为 1 到 64 个字符";
  for (const ch of Array.from(name)) {
    if (isControlOrDel(ch)) return "用户名不能包含控制字符";
    if (isPythonSpace(ch)) return "用户名不能包含空白字符";
  }
  return "";
}

function currentPasswordProblem(value: string): string {
  if (hasLoneSurrogate(value)) return "当前密码不能包含无效字符";
  const count = codePointCount(value);
  if (count < 1) return "请输入当前密码";
  if (count > PASSWORD_MAX) return "当前密码不能超过 1024 个字符";
  return "";
}

function newPasswordProblem(value: string): string {
  if (value === "") return "";
  if (hasLoneSurrogate(value)) return "新密码不能包含无效字符";
  if (containsNul(value)) return "新密码不能包含空字符";
  const count = codePointCount(value);
  if (count < PASSWORD_MIN || count > PASSWORD_MAX) return "新密码须为 12 到 1024 个字符，或留空只改用户名";
  return "";
}

function confirmProblem(next: string, confirm: string): string {
  if (next === confirm) return "";
  if (next === "") return "未设置新密码时请不要填写确认";
  return "两次输入的新密码不一致";
}

function isAccountPayload(body: unknown): body is { username: string } {
  if (!body || typeof body !== "object") return false;
  const username = (body as { username?: unknown }).username;
  return typeof username === "string" && codePointCount(username) >= 1;
}

function isStrictSuccess(body: unknown): boolean {
  if (!body || typeof body !== "object") return false;
  const record = body as { ok?: unknown; reauthenticate?: unknown };
  return record.ok === true && record.reauthenticate === true;
}

function readFailure(body: unknown): { message: string; code?: string } {
  if (!body || typeof body !== "object" || !("error" in body)) return { message: "" };
  const error = (body as { error: unknown }).error;
  if (!error || typeof error !== "object") return { message: "" };
  const record = error as { message?: unknown; code?: unknown };
  const message = typeof record.message === "string" ? record.message : "";
  const code = typeof record.code === "string" && record.code ? record.code : undefined;
  return { message, code };
}

function fallbackFor(status: number): string {
  if (status === 403) return "当前密码不正确";
  if (status === 409) return "用户名已被使用";
  if (status === 429) return "操作过于频繁";
  if (status === 503) return "暂时无法保存";
  return "保存失败";
}

function retryAfterText(header: string | null): string {
  if (!header) return "";
  const seconds = Number(header);
  if (header.trim() !== "" && Number.isFinite(seconds) && seconds >= 0) {
    return `请 ${Math.ceil(seconds)} 秒后再试`;
  }
  const at = Date.parse(header);
  if (!Number.isFinite(at)) return "";
  const left = Math.max(0, Math.ceil((at - Date.now()) / 1000));
  return `请 ${left} 秒后再试`;
}

async function readBody(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return null;
  }
}

function clearRenewalMarker(): void {
  try {
    localStorage.removeItem(RENEW_STORAGE_KEY);
  } catch {
    // 存储不可用时不改用其他键，也不写 Cookie。刷新仍回到原登录门。
  }
}

function reloadToLogin(): void {
  window.location.reload();
}

function FailureText({ message, detail, code }: ShownError) {
  return (
    <div className="error" role="alert">
      <span>{message}</span>
      {detail ? <span className="detail">{detail}</span> : null}
      {code ? <span className="code">错误码：{code}</span> : null}
    </div>
  );
}

export function AccountTab() {
  const [loadPhase, setLoadPhase] = useState<LoadPhase>("loading");
  const [loadMessage, setLoadMessage] = useState("");
  const [loadCode, setLoadCode] = useState<string | undefined>(undefined);
  const [currentUsername, setCurrentUsername] = useState("");
  const [username, setUsername] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [secretEpoch, setSecretEpoch] = useState(0);
  const [busy, setBusy] = useState(false);
  const [unknown, setUnknown] = useState(accountSubmitUnresolved);
  const [authExpired, setAuthExpired] = useState(false);
  const [attempted, setAttempted] = useState(false);
  const [saveError, setSaveError] = useState<ShownError | null>(null);
  const [retryNonce, setRetryNonce] = useState(0);

  const busyRef = useRef(false);
  const unknownRef = useRef(accountSubmitUnresolved);
  const draftDirtyRef = useRef(false);
  const loadGen = useRef(0);
  const submitGen = useRef(0);
  const submitInFlight = useRef(false);
  const reloadingRef = useRef(false);
  const mountedRef = useRef(false);
  const submitAbortRef = useRef<AbortController | null>(null);
  const userRef = useRef<HTMLInputElement>(null);
  const currentRef = useRef<HTMLInputElement>(null);
  const nextRef = useRef<HTMLInputElement>(null);
  const confirmRef = useRef<HTMLInputElement>(null);

  function clearSecrets(): void {
    if (!mountedRef.current) return;
    setCurrentPassword("");
    setNewPassword("");
    setConfirmPassword("");
    setSecretEpoch((value) => value + 1);
  }

  function lockUnknown(): void {
    accountSubmitUnresolved = true;
    unknownRef.current = true;
    clearSecrets();
    if (mountedRef.current) setUnknown(true);
  }

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      submitGen.current += 1;
      submitAbortRef.current?.abort();
      if (submitInFlight.current && !reloadingRef.current) {
        accountSubmitUnresolved = true;
        unknownRef.current = true;
      }
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const generation = ++loadGen.current;
    let timedOut = false;
    const timer = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, READ_TIMEOUT_MS);
    setLoadPhase("loading");
    setLoadMessage("");
    setLoadCode(undefined);

    void (async () => {
      try {
        const res = await fetch("/panel-auth/account", {
          credentials: "same-origin",
          cache: "no-store",
          signal: controller.signal,
          headers: { Accept: "application/json", "Cache-Control": "no-store" },
        });
        if (generation !== loadGen.current) return;
        const body = await readBody(res);
        if (generation !== loadGen.current) return;
        if (res.status === 401) {
          const failure = readFailure(body);
          setLoadPhase("expired");
          setLoadMessage(failure.message);
          setLoadCode(failure.code);
          return;
        }
        if (!res.ok) {
          const failure = readFailure(body);
          setLoadPhase("error");
          setLoadMessage(failure.message || "无法读取账户");
          setLoadCode(failure.code);
          return;
        }
        if (!isAccountPayload(body)) {
          setLoadPhase("error");
          setLoadMessage("账户信息格式不正确");
          setLoadCode(undefined);
          return;
        }
        setCurrentUsername(body.username);
        if (!draftDirtyRef.current) setUsername(body.username);
        setLoadPhase("ready");
      } catch {
        // 读取失败不能当成空用户名。不输出请求体。
        if (generation !== loadGen.current) return;
        setLoadPhase("error");
        setLoadMessage(timedOut ? "读取超时" : "无法读取账户");
        setLoadCode(undefined);
      } finally {
        window.clearTimeout(timer);
      }
    })();

    return () => {
      loadGen.current += 1;
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [retryNonce]);

  useEffect(() => {
    userRef.current?.setCustomValidity(usernameProblem(username));
    currentRef.current?.setCustomValidity(currentPasswordProblem(currentPassword));
    nextRef.current?.setCustomValidity(newPasswordProblem(newPassword));
    confirmRef.current?.setCustomValidity(confirmProblem(newPassword, confirmPassword));
  }, [username, currentPassword, newPassword, confirmPassword, secretEpoch, loadPhase]);

  const userError = usernameProblem(username);
  const currentError = currentPasswordProblem(currentPassword);
  const nextError = newPasswordProblem(newPassword);
  const confirmError = confirmProblem(newPassword, confirmPassword);
  const normalized = normalizeUsername(username);
  const noChange = loadPhase === "ready" && normalized === normalizeUsername(currentUsername) && newPassword === "";
  const fieldsLocked = busy || unknown || loadPhase !== "ready" || authExpired;
  const submitLocked = fieldsLocked || noChange || currentUsername === "";

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busyRef.current || unknownRef.current || reloadingRef.current) return;
    setAttempted(true);
    userRef.current?.setCustomValidity(usernameProblem(username));
    currentRef.current?.setCustomValidity(currentPasswordProblem(currentPassword));
    nextRef.current?.setCustomValidity(newPasswordProblem(newPassword));
    confirmRef.current?.setCustomValidity(confirmProblem(newPassword, confirmPassword));
    if (event.currentTarget.reportValidity() === false) return;
    if (loadPhase !== "ready" || authExpired || unknown) return;
    const nextUsername = normalizeUsername(username);
    if (usernameProblem(username) || currentPasswordProblem(currentPassword) || newPasswordProblem(newPassword) || confirmProblem(newPassword, confirmPassword)) return;
    if (nextUsername === normalizeUsername(currentUsername) && newPassword === "") return;
    if (!currentUsername) return;

    const payload: { username: string; currentPassword: string; newPassword: string } = {
      username: nextUsername,
      currentPassword,
      newPassword,
    };
    busyRef.current = true;
    submitInFlight.current = true;
    setBusy(true);
    setSaveError(null);
    const generation = ++submitGen.current;
    const controller = new AbortController();
    submitAbortRef.current = controller;
    let timedOut = false;
    const timer = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, WRITE_TIMEOUT_MS);

    void (async () => {
      let reloading = false;
      try {
        const res = await fetch("/panel-auth/account", {
          method: "PUT",
          credentials: "same-origin",
          cache: "no-store",
          signal: controller.signal,
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
            "Cache-Control": "no-store",
          },
          body: JSON.stringify(payload),
        });
        const body = await readBody(res);
        if (res.status === 200 && isStrictSuccess(body)) {
          reloading = true;
          reloadingRef.current = true;
          submitInFlight.current = false;
          accountSubmitUnresolved = false;
          unknownRef.current = false;
          clearSecrets();
          clearRenewalMarker();
          window.location.reload();
          return;
        }
        const stale = generation !== submitGen.current || !mountedRef.current;
        // 2xx 但不是严格成功，或写请求已超时：都不能再立刻重写。
        if (timedOut || res.ok) {
          lockUnknown();
          return;
        }
        if (stale) {
          accountSubmitUnresolved = false;
          unknownRef.current = false;
          return;
        }
        clearSecrets();
        const failure = readFailure(body);
        if (res.status === 401) {
          setAuthExpired(true);
          setSaveError({
            message: "登录已过期",
            detail: failure.message && failure.message !== "登录已过期" ? failure.message : "",
            code: failure.code,
          });
          return;
        }
        setSaveError({
          message: failure.message || fallbackFor(res.status),
          detail: res.status === 429 ? retryAfterText(res.headers.get("Retry-After")) : "",
          code: failure.code,
        });
      } catch {
        // 超时、断网或卸载取消。不读取错误对象，避免夹带请求体。不自动重试。
        lockUnknown();
      } finally {
        window.clearTimeout(timer);
        if (submitAbortRef.current === controller) submitAbortRef.current = null;
        if (!reloading) {
          submitInFlight.current = false;
          busyRef.current = false;
          if (mountedRef.current && generation === submitGen.current) setBusy(false);
        }
      }
    })();
  }

  const expiredDetail = loadMessage && loadMessage !== "登录已过期" ? loadMessage : "";

  return (
    <div className="account-settings">
      <Head title="账户与安全" />
      <p className="sub">保存成功后，当前账户在其他浏览器的下次请求也须重新登录。</p>
      {unknown && loadPhase !== "expired" ? (
        <div className="card">
          <FailureText message="无法确认是否保存，请重新登录核对" />
          <div className="acts">
            <button type="button" className="btn" onClick={reloadToLogin}>刷新并重新登录</button>
          </div>
        </div>
      ) : null}
      {loadPhase === "loading" ? <Loading /> : null}
      {loadPhase === "error" ? (
        <>
          <LoadError message={loadMessage || "无法读取账户"} onRetry={() => setRetryNonce((value) => value + 1)} />
          {loadCode ? <div className="error"><span className="code">错误码：{loadCode}</span></div> : null}
        </>
      ) : null}
      {loadPhase === "expired" ? (
        <div className="empty">
          登录已过期
          {expiredDetail ? <div className="error">{expiredDetail}</div> : null}
          {loadCode ? <div className="error"><span className="code">错误码：{loadCode}</span></div> : null}
          <br />
          <button type="button" className="link" onClick={reloadToLogin}>刷新并重新登录</button>
        </div>
      ) : null}
      {loadPhase === "ready" ? (
        <form className="card" method="post" autoComplete="on" onSubmit={onSubmit} aria-busy={busy}>
          <p className="current"><span className="muted">当前用户名 </span>{currentUsername}</p>
          <label htmlFor="account-username">新用户名</label>
          <input
            ref={userRef}
            id="account-username"
            name="username"
            type="text"
            autoComplete="username"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            required
            disabled={fieldsLocked}
            value={username}
            onChange={(event) => {
              draftDirtyRef.current = true;
              setUsername(event.target.value);
            }}
          />
          <p className="hint">保存时会去掉首尾空白并转为小写。与当前用户名相同且新密码留空，则不会提交。</p>
          {userError && (attempted || username !== currentUsername) ? <p className="error field-error">{userError}</p> : null}

          <label htmlFor="account-current-password">当前密码</label>
          <input
            key={`current-${secretEpoch}`}
            ref={currentRef}
            id="account-current-password"
            name="currentPassword"
            type="password"
            autoComplete="current-password"
            spellCheck={false}
            required
            disabled={fieldsLocked}
            value={currentPassword}
            onChange={(event) => setCurrentPassword(event.target.value)}
          />
          {attempted && currentError ? <p className="error field-error">{currentError}</p> : null}

          <label htmlFor="account-new-password">新密码</label>
          <input
            key={`next-${secretEpoch}`}
            ref={nextRef}
            id="account-new-password"
            name="newPassword"
            type="password"
            autoComplete="new-password"
            spellCheck={false}
            disabled={fieldsLocked}
            value={newPassword}
            onChange={(event) => setNewPassword(event.target.value)}
          />
          <p className="hint">留空则只修改用户名。填写时须为 12 到 1024 个字符，且不能包含空字符。</p>
          {nextError ? <p className="error field-error">{nextError}</p> : null}

          <label htmlFor="account-confirm-password">确认新密码</label>
          <input
            key={`confirm-${secretEpoch}`}
            ref={confirmRef}
            id="account-confirm-password"
            type="password"
            autoComplete="new-password"
            spellCheck={false}
            disabled={fieldsLocked}
            value={confirmPassword}
            onChange={(event) => setConfirmPassword(event.target.value)}
          />
          <p className="hint">仅用于本地核对，不会提交到服务器。</p>
          {confirmError ? <p className="error field-error">{confirmError}</p> : null}

          {noChange && !unknown ? <p className="hint">没有修改，不会提交。</p> : null}
          {!unknown && saveError ? <FailureText message={saveError.message} detail={saveError.detail} code={saveError.code} /> : null}
          {authExpired ? (
            <div className="acts">
              <button type="button" className="btn" onClick={reloadToLogin}>刷新并重新登录</button>
            </div>
          ) : (
            <div className="acts">
              <button type="submit" className="btn primary" disabled={submitLocked}>{busy ? "保存中…" : "保存"}</button>
            </div>
          )}
        </form>
      ) : null}
    </div>
  );
}
