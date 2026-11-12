import type { Metadata } from "next";
import "./globals.css";
import "./login.css";
import "./workspace.css";
import "./research.css";
export const metadata: Metadata = {
  title: "Hermes Alpha Lab",
  description: "有依据的美股研究工作台",
};
export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
