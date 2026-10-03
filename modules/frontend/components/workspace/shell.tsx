"use client";

import type { ReactNode } from "react";
import Image from "next/image";
import Link from "next/link";
import { LayoutDashboard, CookingPot, ChartNoAxesCombined, Settings2, MapPin } from "lucide-react";
import { Sidebar, SidebarHeader, SidebarContent, SidebarFooter, SidebarMenu, SidebarMenuItem, SidebarMenuButton, SidebarTrigger, useSidebar } from "@/components/ui/sidebar";
import { Button } from "@/components/ui/button";

export type WorkspaceMode = "kitchen" | "admin";
export type View = "overview" | "kitchen" | "analytics" | "settings";
export const navigation = [
  { id: "kitchen", icon: CookingPot, label: "后厨补菜", description: "任务与补菜记录" },
  { id: "overview", icon: LayoutDashboard, label: "菜品余量", description: "实时余量与供应状态" },
  { id: "analytics", icon: ChartNoAxesCombined, label: "经营分析", description: "趋势、报表与分析助手" },
  { id: "settings", icon: Settings2, label: "门店设置", description: "菜品规则与数据接入" },
] as const;

export function Navigation({ mode, view, onChange }: { mode: WorkspaceMode; view: View; onChange: (view: View) => void }) {
  const items = mode === "kitchen" ? navigation.filter(item => item.id === "kitchen" || item.id === "overview") : navigation;
  const { setOpenMobile } = useSidebar();
  return <Sidebar aria-label="主导航">
    <SidebarHeader className="brand"><div className="brand-logo"><Image src="/brand/kaola-farm.png" alt="KaolaFarm" width={1254} height={1254} unoptimized priority/></div><small>{mode === "kitchen" ? "厨房端 · 供菜工作台" : "管理端 · 餐饮经营工作台"}</small></SidebarHeader>
    <SidebarContent className="nav-content"><span className="nav-label">工作空间</span><SidebarMenu>{items.map(({ id, icon: Icon, label }) => <SidebarMenuItem key={id}><SidebarMenuButton isActive={view === id} aria-current={view === id ? "page" : undefined} className="nav-item" onClick={() => { onChange(id); setOpenMobile(false); }}><Icon/><span>{label}</span></SidebarMenuButton></SidebarMenuItem>)}</SidebarMenu></SidebarContent>
    <SidebarFooter className="sidebar-note"><MapPin size={17}/><div><span>西安门店</span><small>{mode === "kitchen" ? "补菜任务与供应状态" : "门店经营与供应管理"}</small></div></SidebarFooter>
  </Sidebar>;
}

export function Topbar({ mode, onNavigate, children }: { mode: WorkspaceMode; onNavigate: (view: View) => void; children: ReactNode }) {
  return <header className="topbar">
    <div className="topbar-left"><SidebarTrigger aria-label="展开或收起导航"/><span className="workspace-mode">{mode === "kitchen" ? "厨房端" : "管理端"}</span></div>

    <div className="topbar-actions">{children}<Button variant="ghost" asChild><Link href="/">选择工作端</Link></Button>{mode === "admin" && <Button variant="ghost" className="store-avatar" aria-label="打开门店设置" onClick={() => onNavigate("settings")}>西</Button>}</div>
  </header>;
}
