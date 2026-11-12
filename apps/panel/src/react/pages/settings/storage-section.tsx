import { useEffect, useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { getAdvanced, setAdvanced, type AdvancedRead } from "@/lib/admin-api";
import { queryKeys } from "@/lib/queries";
import { errText, Notice, Switch } from "./bits";

const KEY = "uploads.s3";
const MASK = "***";
const DEFAULT_ENDPOINT =
  "https://axedi45jm72n.compat.objectstorage.us-chicago-1.oraclecloud.com";
const DEFAULT_BUCKET = "hermes-uploads";
const DEFAULT_REGION = "us-chicago-1";

interface S3Form {
  enabled: boolean;
  endpoint: string;
  bucket: string;
  region: string;
  access_key: string;
  access_key_set: boolean;
  secret_key: string;
  secret_set: boolean;
  clear_secret: boolean;
}

const EMPTY_FORM: S3Form = {
  enabled: false,
  endpoint: "",
  bucket: "",
  region: "",
  access_key: "",
  access_key_set: false,
  secret_key: "",
  secret_set: false,
  clear_secret: false,
};

function readForm(value: unknown): S3Form {
  const obj =
    value && typeof value === "object" && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : {};
  const storedSecret = typeof obj.secret_key === "string" ? obj.secret_key : "";
  const storedAccess = typeof obj.access_key === "string" ? obj.access_key : "";
  return {
    enabled: obj.enabled === true,
    endpoint: typeof obj.endpoint === "string" ? obj.endpoint : "",
    bucket: typeof obj.bucket === "string" ? obj.bucket : "",
    region: typeof obj.region === "string" ? obj.region : "",
    access_key: "",
    access_key_set: storedAccess !== "",
    secret_key: "",
    secret_set: storedSecret !== "",
    clear_secret: false,
  };
}

export function StorageSection() {
  const client = useQueryClient();
  const [form, setForm] = useState<S3Form>(EMPTY_FORM);
  const [meta, setMeta] = useState<AdvancedRead | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let cancelled = false;
    setBusy(true);
    setError("");
    getAdvanced(KEY)
      .then((view) => {
        if (cancelled) return;
        setMeta(view);
        setForm(readForm(view.value));
        setBusy(false);
      })
      .catch((loadError: unknown) => {
        if (cancelled) return;
        setMeta(null);
        setError(errText(loadError, "读取对象存储设置失败"));
        setBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function patch(next: Partial<S3Form>): void {
    setForm((current) => ({ ...current, ...next }));
  }

  async function save(event: FormEvent): Promise<void> {
    event.preventDefault();
    if (busy || !meta) return;
    setBusy(true);
    setError("");
    setNotice("");
    const payload: Record<string, unknown> = {
      enabled: form.enabled,
      endpoint: form.endpoint.trim(),
      bucket: form.bucket.trim(),
      region: form.region.trim(),
      access_key: form.access_key !== "" ? form.access_key.trim() : MASK,
      secret_key: form.clear_secret
        ? null
        : form.secret_key !== ""
          ? form.secret_key
          : MASK,
    };
    try {
      await setAdvanced(KEY, payload);
      await Promise.allSettled([
        client.invalidateQueries({ queryKey: queryKeys.settings }),
        client.invalidateQueries({ queryKey: queryKeys.config }),
      ]);
      const fresh = await getAdvanced(KEY);
      setMeta(fresh);
      setForm(readForm(fresh.value));
      setNotice("已保存。上传接口每次读取最新配置，无需重启。");
    } catch (saveError: unknown) {
      setError(errText(saveError, "保存失败"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <h2>对象存储 (S3)</h2>
      <form className="card" onSubmit={(event) => void save(event)}>
        <div className="ctl">
          <Switch
            checked={form.enabled}
            disabled={busy || !meta}
            label="启用对象存储"
            onClick={() => patch({ enabled: !form.enabled })}
          />
          <span>启用对象存储{form.enabled ? "（已开启）" : "（已关闭）"}</span>
        </div>
        <p className="sub">
          默认关闭。开启后对话上传的文件直接存入 S3，不再落本地盘。agent 读取 S3
          文件尚未支持，开发板请保持关闭。
        </p>
        <label htmlFor="s3-endpoint">Endpoint</label>
        <input
          id="s3-endpoint"
          type="text"
          placeholder={DEFAULT_ENDPOINT}
          autoComplete="off"
          spellCheck={false}
          disabled={busy || !meta}
          value={form.endpoint}
          onChange={(event) => patch({ endpoint: event.target.value })}
        />
        <label htmlFor="s3-bucket">桶名</label>
        <input
          id="s3-bucket"
          type="text"
          placeholder={DEFAULT_BUCKET}
          autoComplete="off"
          spellCheck={false}
          disabled={busy || !meta}
          value={form.bucket}
          onChange={(event) => patch({ bucket: event.target.value })}
        />
        <label htmlFor="s3-region">区域</label>
        <input
          id="s3-region"
          type="text"
          placeholder={DEFAULT_REGION}
          autoComplete="off"
          spellCheck={false}
          disabled={busy || !meta}
          value={form.region}
          onChange={(event) => patch({ region: event.target.value })}
        />
        <label htmlFor="s3-access-key">Access Key</label>
        <input
          id="s3-access-key"
          type="text"
          placeholder={
            form.access_key_set ? "已设置，留空保持不变" : "Customer Secret Key 的 Access Key"
          }
          autoComplete="off"
          spellCheck={false}
          disabled={busy || !meta}
          value={form.access_key}
          onChange={(event) => patch({ access_key: event.target.value })}
        />
        <label htmlFor="s3-secret-key">Secret Key</label>
        <input
          id="s3-secret-key"
          type="password"
          placeholder={
            form.secret_set ? "已设置，留空保持不变" : "Customer Secret Key 的 Secret Key"
          }
          autoComplete="off"
          spellCheck={false}
          disabled={busy || !meta}
          value={form.secret_key}
          onChange={(event) => patch({ secret_key: event.target.value })}
        />
        {form.secret_set ? (
          <p className="sub">
            密钥已保存，不会回显。
            <button
              type="button"
              className="btn"
              disabled={busy}
              onClick={() => patch({ clear_secret: !form.clear_secret })}
            >
              {form.clear_secret ? "已选清除，点此取消" : "清除已保存的密钥"}
            </button>
          </p>
        ) : null}
        <p className="sub">
          密钥在 OCI 控制台新建：用户 -&gt; Customer Secret Keys。旧密钥的值已删除，找不回来，需要新建一把。
        </p>
        {error ? (
          <div className="out error" role="alert">
            {error}
          </div>
        ) : null}
        {notice ? <Notice text={notice} /> : null}
        <div className="actions">
          <button className="btn primary" type="submit" disabled={busy || !meta}>
            {busy ? "保存中…" : "保存"}
          </button>
        </div>
      </form>
    </section>
  );
}
