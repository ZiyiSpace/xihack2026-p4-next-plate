import type { Metadata } from "next";
import Workspace from "@/components/workspace/workspace";

export const metadata: Metadata = { title: "管理端 · 红考拉" };
export default function Page() { return <Workspace mode="admin"/>; }
