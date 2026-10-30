"use client";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { api } from "../../lib/api";

export default function Login() {
  const router = useRouter(); const [error, setError] = useState(""); const [pending, setPending] = useState(false);
  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setPending(true); setError("");
    const values = new FormData(event.currentTarget);
    try { await api("/auth/login", { body: { email: values.get("email"), password: values.get("password") } }); router.replace("/research"); }
    catch (e) { setError(e instanceof Error ? e.message : "登录未完成"); }
    finally { setPending(false); }
  }
  return <main className="login-layout"><section className="login-intro"><div className="brand">有为<span>YTA / RESEARCH</span></div><div><p className="eyebrow">研究有据 / 判断留痕</p><h1>让每一次判断，<br/>都有回看的依据。</h1><p>从市场证据到冻结预测，<br/>在时间到来时，认真核对结果。</p></div><div className="login-process"><span>研究</span><i/><span>冻结</span><i/><span>验证</span></div></section>
    <section className="login-form"><div className="form-wrap"><p className="eyebrow">个人研究空间</p><h2>进入工作台</h2><p className="muted">使用本地配置的账户登录。</p><form onSubmit={login}>
      <label>邮箱<input name="email" type="email" autoComplete="username" required placeholder="youwei@local.yta"/></label>
      <label>密码<input name="password" type="password" autoComplete="current-password" required maxLength={256}/></label>
      {error && <p className="alert" role="alert">{error}</p>}<button className="primary" disabled={pending}>{pending ? "正在登录…" : "进入研究工作台 →"}</button>
    </form><p className="login-note">研究记录保留数据来源、模型版本与信息截点。<br/>预测不等同于保证，也不自动执行交易。</p></div></section></main>;
}
