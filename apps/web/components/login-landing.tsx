"use client";

import { FeishuLogin } from "./feishu-login";
import { useEffect, useRef, useState, type FormEvent } from "react";

type Props = {
  register: boolean;
  busy: boolean;
  error: string;
  onMode: (register: boolean) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
};

function Mark() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="m12 2 10 20-10-5-10 5L12 2Z" fill="currentColor" />
      <path d="M12 8v9" stroke="#070909" strokeWidth="1.5" />
    </svg>
  );
}
function Icon({ kind }: { kind: number }) {
  const paths = [
    "M10.5 17a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13Zm4.5-2 6 6M8 11h5m-2.5-2.5v5",
    "m13 2-9 12h7l-1 8 10-12h-7l1-8Z",
    "m12 3 10 5-10 5L2 8l10-5Zm-9 9 9 5 9-5M3 16l9 5 9-5",
    "M20 7a9 9 0 0 0-15-2L2 8m0-6v6h6M4 17a9 9 0 0 0 15 2l3-3m0 6v-6h-6",
    "M4 3v17h17M7 14l4-5 4 3 5-7",
    "m12 2 8 3v7c0 5-8 10-8 10S4 17 4 12V5l8-3Zm-4 9 3 3 5-6",
  ];
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={paths[kind]} />
    </svg>
  );
}
const features = [
  [
    "Agent 深度研究",
    "从一个问题出发，让研究、预测和组合专家协作，把证据与结论带回同一段对话。",
  ],
  [
    "行情与指标，一起看",
    "查看美股价格走势、移动均线与 RSI。每一份行情都保留来源、日期和价格口径。",
  ],
  [
    "让每个结论有出处",
    "图表、数据与计算结果相互关联。沿着引用回看，让你的判断有据可循。",
  ],
  [
    "研究可以继续",
    "关闭页面，研究仍在后台进行。再次回来，从保存的对话和画布接着探索。",
  ],
  [
    "用基线检验预期",
    "查看固定统计方法的预测与历史评估，理解样本和误差，不把预测当作承诺。",
  ],
  [
    "看清模拟组合",
    "按手工记录的持仓分析估值、权重与集中度。数据缺失时明确展示，不用猜测填补。",
  ],
];

function Preview() {
  return (
    <section
      id="preview"
      className="entry-preview"
      aria-label="研究工作台界面示例，图表为示意数据"
    >
      <div className="entry-window-bar">
        <span className="entry-window-dots" aria-hidden="true">
          <i />
          <i />
          <i />
        </span>
        <span className="entry-window-address">
          hermes / research workspace
        </span>
        <span>界面示例</span>
      </div>
      <div className="entry-window-body">
        <aside className="entry-watchlist">
          <span className="entry-micro">研究列表</span>
          {["NVDA", "AAPL", "MSFT", "TSLA", "AMZN", "META"].map((symbol, i) => (
            <div key={symbol} className={i === 0 ? "is-selected" : ""}>
              <span>{symbol}</span>
              <span className="entry-spark" aria-hidden="true">
                {i % 2 ? "▁▃▂▅▄▆" : "▂▁▄▃▆▇"}
              </span>
            </div>
          ))}
          <div className="entry-agent-states">
            <span className="entry-micro">AGENT 协作</span>
            <p>
              <i />
              研究 · 梳理市场证据
            </p>
            <p>
              <i />
              预测 · 评估统计基线
            </p>
            <p>
              <i />
              组合 · 分析模拟持仓
            </p>
          </div>
        </aside>
        <div className="entry-chart-panel">
          <div className="entry-chart-title">
            <div>
              <h3>NVDA</h3>
              <p>英伟达 · 美股研究</p>
            </div>
            <span className="entry-chart-badge">
              趋势概览 <span aria-hidden="true">↗</span>
            </span>
          </div>
          <div className="entry-chart-metrics">
            <div>
              <span>价格走势</span>
              <strong>OHLCV</strong>
            </div>
            <div>
              <span>趋势指标</span>
              <strong>MA20</strong>
            </div>
            <div>
              <span>动量指标</span>
              <strong>RSI14</strong>
            </div>
            <div>
              <span>证据来源</span>
              <strong className="entry-accent">可追溯</strong>
            </div>
          </div>
          <svg
            className="entry-chart"
            viewBox="0 0 640 270"
            role="img"
            aria-label="示意折线图：青绿色历史走势与虚线预测区间，不代表实际行情"
          >
            <defs>
              <linearGradient id="entry-chart-fill" x1="0" y1="0" x2="0" y2="1">
                <stop stopColor="#00d6c4" stopOpacity=".15" />
                <stop offset="1" stopColor="#00d6c4" stopOpacity="0" />
              </linearGradient>
            </defs>
            {[35, 80, 125, 170, 215].map((y) => (
              <line key={y} x1="12" x2="628" y1={y} y2={y} stroke="#202424" />
            ))}
            <path
              d="M14 220 45 197 76 211 107 169 138 153 169 171 200 137 231 159 262 115 293 94 324 119 355 72 386 93 417 61 448 53 448 245 14 245Z"
              fill="url(#entry-chart-fill)"
            />
            <path
              d="m14 220 31-23 31 14 31-42 31-16 31 18 31-34 31 22 31-44 31-21 31 25 31-47 31 21 31-32 31-8"
              fill="none"
              stroke="#00d6c4"
              strokeWidth="2.5"
              strokeLinejoin="round"
            />
            <path
              d="m448 53 31-11 31-16 31 10 31-5 42-22"
              fill="none"
              stroke="#bbbfc0"
              strokeWidth="2"
              strokeDasharray="5 5"
            />
            {[
              [14, 220],
              [45, 197],
              [107, 169],
              [138, 153],
              [200, 137],
              [262, 115],
              [293, 94],
              [355, 72],
              [417, 61],
              [448, 53],
            ].map(([x, y]) => (
              <circle key={x} cx={x} cy={y} r="3" fill="#27ead9" />
            ))}
            <text x="12" y="265">
              历史走势
            </text>
            <text x="485" y="265">
              统计预测示意
            </text>
          </svg>
          <div className="entry-chart-foot">
            <span>价格 · 指标 · 预测</span>
            <span>示意数据，非实时行情</span>
          </div>
        </div>
        <aside className="entry-assistant">
          <div className="entry-micro">
            HERMES AGENT <span>研究示例</span>
          </div>
          <p className="entry-bubble">
            把 NVDA 的走势、指标和模拟组合放在一起，帮我看清这次判断的依据。
          </p>
          <p className="entry-bubble entry-bubble-agent">
            <span className="entry-accent">✧</span>{" "}
            从行情开始，连接预测与组合分析。所有结果会保存在研究画布中。
          </p>
          <div className="entry-evidence">
            <span className="entry-micro">一个问题，多维证据</span>
            <strong>
              3 <small>位研究伙伴</small>
            </strong>
            <div>
              <span>研究</span>
              <span>预测</span>
              <span>组合</span>
            </div>
          </div>
          <div className="entry-demo-input">
            向 Hermes 提问… <span aria-hidden="true">↑</span>
          </div>
        </aside>
      </div>
    </section>
  );
}

export function LoginLanding({
  register,
  busy,
  error,
  onMode,
  onSubmit,
}: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [opened, setOpened] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const errorRef = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (!opened) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [opened]);
  useEffect(() => {
    if (error) errorRef.current?.focus();
  }, [error]);
  function open(registerMode = false) {
    onMode(registerMode);
    setShowPassword(false);
    dialog.current?.showModal();
    dialog.current
      ?.querySelector<HTMLInputElement>('input[name="email"]')
      ?.focus();
    setOpened(true);
  }
  return (
    <div className="entry">
      <a className="entry-skip" href="#entry-main">
        跳到主要内容
      </a>
      <header className="entry-header">
        <div className="entry-container entry-nav">
          <a
            className="entry-brand"
            href="#entry-main"
            aria-label="Hermes 首页"
          >
            <Mark />
            <span>Hermes</span>
          </a>
          <nav aria-label="产品导航">
            <a href="#features">研究能力</a>
            <a href="#workflow">工作流程</a>
            <a href="#preview">产品预览</a>
          </nav>
          <div className="entry-auth-actions">
            <button onClick={() => open()}>登录</button>
            <button
              className="entry-button entry-button-white entry-button-small"
              onClick={() => open(true)}
            >
              注册 <span aria-hidden="true">↗</span>
            </button>
          </div>
        </div>
      </header>
      <main id="entry-main">
        <section className="entry-hero entry-container">
          <a className="entry-release" href="#features">
            <span />
            HERMES ALPHA LAB <b>三角色协作研究</b>
            <span aria-hidden="true">→</span>
          </a>
          <h1>
            研究，洞察，进化<span>。</span>
          </h1>
          <p>
            为美股研究打造的 Agent 工作台。
            <br className="entry-mobile-break" />
            连接行情、预测与模拟组合，
            <br className="entry-desktop-break" />
            让每一个判断，都有据可循。
          </p>
          <div className="entry-hero-actions">
            <button
              className="entry-button entry-button-white"
              onClick={() => open()}
            >
              开始研究 <span aria-hidden="true">↗</span>
            </button>
            <a className="entry-button" href="#preview">
              探索工作台 <span aria-hidden="true">↓</span>
            </a>
          </div>
          <div className="entry-symbol-strip" aria-label="美股研究对象示例">
            <span>美股研究</span>
            <b>NVDA</b>
            <i />
            <b>AAPL</b>
            <i />
            <b>TSLA</b>
            <i />
            <b>MSFT</b>
            <span className="entry-symbol-end">
              从一个问题开始 <span aria-hidden="true">↗</span>
            </span>
          </div>
        </section>
        <div className="entry-container">
          <Preview />
          <div className="entry-principles">
            <span className="entry-micro">让研究回到证据本身</span>
            <div>
              <span>真实来源</span>
              <span>独立研究空间</span>
              <span>三角色协作</span>
              <span>结果持续保存</span>
            </div>
          </div>
        </div>
        <section id="features" className="entry-features entry-container">
          <div className="entry-section-heading">
            <span className="entry-kicker">核心能力</span>
            <h2>一个工作台，连接你的判断。</h2>
            <p>从问题到证据，让研究的每一步都清晰可见。</p>
          </div>
          <div className="entry-feature-grid">
            {features.map(([title, description], i) => (
              <article key={title}>
                <span className="entry-icon-box">
                  <Icon kind={i} />
                </span>
                <h3>{title}</h3>
                <p>{description}</p>
                <span className="entry-card-tag">
                  {
                    [
                      "研究 / 协作",
                      "行情 / 指标",
                      "数据 / 引用",
                      "对话 / 恢复",
                      "预测 / 评估",
                      "组合 / 分析",
                    ][i]
                  }
                </span>
              </article>
            ))}
          </div>
        </section>
        <section id="workflow" className="entry-workflow">
          <div className="entry-container">
            <div className="entry-workflow-top">
              <div className="entry-workflow-copy">
                <span className="entry-kicker">HERMES 研究循环</span>
                <h2>
                  让每次研究，
                  <br />
                  成为下次的起点。
                </h2>
                <p>
                  提出问题，整理证据，检验想法。
                  <br />
                  你的对话与结果，始终在同一个地方。
                </p>
                <ul>
                  <li>行情与计算结果保留来源</li>
                  <li>预测与模拟组合协同分析</li>
                  <li>回来时，继续上一次研究</li>
                </ul>
              </div>
              <div className="entry-loop">
                <div className="entry-loop-steps">
                  {[
                    ["研究", "提出问题，收集行情", 0],
                    ["分析", "连接指标与预测", 4],
                    ["验证", "查看来源与评估", 5],
                    ["积累", "保存对话与结果", 2],
                  ].map(([title, desc, icon]) => (
                    <div key={title}>
                      <Icon kind={Number(icon)} />
                      <strong>{title}</strong>
                      <span>{desc}</span>
                    </div>
                  ))}
                </div>
                <div className="entry-loop-return">
                  <span>下一次提问，从已有证据出发</span>
                </div>
              </div>
            </div>
            <div className="entry-facts">
              <div>
                <strong>3</strong>
                <span>独立协作角色</span>
              </div>
              <div>
                <strong>5</strong>
                <span>研究结果组件</span>
              </div>
              <div>
                <strong>IEX</strong>
                <span>美股行情来源</span>
              </div>
              <div>
                <strong>持续保存</strong>
                <span>对话、证据与研究画布</span>
              </div>
            </div>
          </div>
        </section>
        <section className="entry-cta entry-container">
          <span className="entry-kicker">你的下一次研究，从这里开始</span>
          <h2>把想法，变成有依据的判断。</h2>
          <p>连接市场数据与自己的问题，留下一份可以回看的研究。</p>
          <div className="entry-hero-actions">
            <button
              className="entry-button entry-button-white"
              onClick={() => open()}
            >
              进入工作台 <span aria-hidden="true">↗</span>
            </button>
            <button className="entry-button" onClick={() => open(true)}>
              创建账户
            </button>
          </div>
          <small>研究与模拟分析 · 不执行真实交易</small>
        </section>
      </main>
      <footer className="entry-footer">
        <div className="entry-container">
          <div className="entry-footer-top">
            <div>
              <a className="entry-brand" href="#entry-main">
                <Mark />
                Hermes
              </a>
              <p>让每个判断，都有依据。</p>
            </div>
            <div>
              <span>探索</span>
              <a href="#features">研究能力</a>
              <a href="#preview">工作台预览</a>
            </div>
            <div>
              <span>了解</span>
              <a href="#workflow">研究流程</a>
              <a href="#features">数据与证据</a>
            </div>
            <div>
              <span>开始</span>
              <button onClick={() => open()}>登录工作台</button>
              <button onClick={() => open(true)}>创建研究账户</button>
            </div>
          </div>
          <div className="entry-footer-bottom">
            <span>© {new Date().getFullYear()} Hermes Alpha Lab</span>
            <span>RESEARCH. UNDERSTAND. EVOLVE.</span>
            <a href="#entry-main" aria-label="回到顶部">
              ↑
            </a>
          </div>
        </div>
      </footer>
      <dialog
        ref={dialog}
        className="entry-login-dialog"
        aria-labelledby="entry-login-title"
        onClose={() => setOpened(false)}
        onKeyDown={(event) => {
          if (event.key !== "Tab") return;
          const items = Array.from(
            event.currentTarget.querySelectorAll<HTMLElement>(
              'button:not(:disabled),input:not(:disabled),a[href],[tabindex="0"]',
            ),
          );
          const first = items[0],
            last = items.at(-1);
          if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last?.focus();
          } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first?.focus();
          }
        }}
        onClick={(e) => {
          if (e.target === e.currentTarget && !busy) {
            const box = e.currentTarget.getBoundingClientRect();
            if (
              e.clientX < box.left ||
              e.clientX > box.right ||
              e.clientY < box.top ||
              e.clientY > box.bottom
            )
              dialog.current?.close();
          }
        }}
      >
        <button
          className="entry-dialog-close"
          aria-label="关闭登录窗口"
          onClick={() => dialog.current?.close()}
        >
          <span aria-hidden="true">×</span>
        </button>
        <div className="entry-brand">
          <Mark />
          Hermes
        </div>
        <span className="entry-kicker">YOUR RESEARCH SPACE</span>
        <h2 id="entry-login-title">
          {register ? "开始你的研究旅程" : "欢迎回来。"}
        </h2>
        <p>让每一次思考，都有迹可循。</p>
        <form onSubmit={onSubmit} aria-busy={busy}>
          <label htmlFor="entry-email">
            邮箱
            <input
              id="entry-email"
              name="email"
              type="email"
              autoComplete="email"
              placeholder="you@example.com"
              required
            />
          </label>
          <label htmlFor="entry-password">密码</label>
          <div className="entry-password">
            <input
              id="entry-password"
              name="password"
              type={showPassword ? "text" : "password"}
              minLength={register ? 12 : 1}
              maxLength={256}
              autoComplete={register ? "new-password" : "current-password"}
              aria-describedby={register ? "entry-register-hint" : undefined}
              required
              placeholder={register ? "至少 12 个字符" : "输入你的密码"}
            />
            <button
              type="button"
              aria-label={showPassword ? "隐藏密码" : "显示密码"}
              aria-pressed={showPassword}
              onClick={() => setShowPassword(!showPassword)}
            >
              <svg
                aria-hidden="true"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
              >
                <path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z" />
                <circle cx="12" cy="12" r="3" />
                {showPassword && <path d="m3 3 18 18" />}
              </svg>
            </button>
          </div>
          {register && (
            <small id="entry-register-hint">
              密码至少 12 个字符。注册后需开通研究环境。
            </small>
          )}
          {error && (
            <p
              ref={errorRef}
              tabIndex={-1}
              className="entry-login-error"
              role="alert"
            >
              {error}
            </p>
          )}
          <button
            className="entry-button entry-button-white entry-submit"
            disabled={busy}
          >
            {busy ? "正在处理…" : register ? "创建账户" : "进入工作台"}
            <span aria-hidden="true">→</span>
          </button>
          <button
            className="entry-mode-switch"
            type="button"
            disabled={busy}
            onClick={() => onMode(!register)}
          >
            {register ? "已有账户？登录" : "还没有账户？注册"}
          </button>
        </form>
        <FeishuLogin />
        <div className="entry-login-foot">
          独立研究空间 · 对话与结果持续保存
        </div>
      </dialog>
    </div>
  );
}
