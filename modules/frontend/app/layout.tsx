import type { Metadata } from "next";
import "./globals.css";
export const metadata:Metadata={title:"红考拉经营工作台",description:"旋转小火锅实时菜品监控、自动补菜与经营分析",icons:{icon:"/favicon.svg",shortcut:"/favicon.svg"}};
export default function RootLayout({children}:{children:React.ReactNode}){return <html lang="zh-CN"><body>{children}</body></html>}
