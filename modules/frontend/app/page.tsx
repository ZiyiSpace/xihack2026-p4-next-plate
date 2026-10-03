import Link from "next/link";
import { CookingPot, LayoutDashboard, ArrowRight } from "lucide-react";

export default function Home() {
 return <main className="workspace-entry">
  <span className="label-kicker">红考拉 · 西安门店</span>
  <h1>选择工作端</h1>
  <p className="muted">按岗位进入工作台，共享门店供菜数据。</p>
  <div className="entry-grid">
   <Link href="/kitchen" className="entry-card"><CookingPot size={30}/><h2>厨房端</h2><p>查看补菜任务与菜品余量，处理制作、补菜和撤盘记录。</p><span>进入厨房端 <ArrowRight size={18}/></span></Link>
   <Link href="/admin" className="entry-card"><LayoutDashboard size={30}/><h2>管理端</h2><p>查看全部供应与经营信息，管理菜品规则、运行设置和数据接入。</p><span>进入管理端 <ArrowRight size={18}/></span></Link>
  </div>
 </main>;
}
