import type { Metadata } from "next";
import "./globals.css";
import "./login.css";
import "./workspace.css";
import "./workspace-redesign.css";
import "./research.css";
import "./yuque.css";
import "./knowledge.css";
import "./notes.css";
export const metadata: Metadata = {
  title: "Youwei Agent · 金融研究工作台",
  description: "有依据的美股研究工作台",
};
export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
