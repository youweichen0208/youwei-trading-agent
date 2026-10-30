import type { Metadata } from "next";
import "./style.css";

export const metadata: Metadata = { title: "有为 · 研究工作台", description: "带证据的研究，可追溯的预测。" };
export default function Layout({ children }: { children: React.ReactNode }) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
