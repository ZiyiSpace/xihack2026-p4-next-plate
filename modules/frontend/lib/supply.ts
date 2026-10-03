import type { Plate, Source } from "./domain";

export type Arrival = { plateId: string; at: number; seconds: number; simulated: boolean };
// Device timestamps describe the next pass at the refill station, not task deadlines.
export function nextArrival(plates: Plate[], dishId: string, source: Source, now: number, staleMinutes: number): Arrival | null {
 const candidates = plates.filter(p => p.dishId === dishId && !p.removed && !p.issue && p.quality >= .85 && now - Date.parse(p.seenAt) <= staleMinutes * 60000 && Date.parse(p.seenAt) <= now);
 const arrivals = candidates.flatMap(p => {
  let at = p.refillArrivalAt ? Date.parse(p.refillArrivalAt) : NaN;
  if (source === "demo") {
   // Illustrative 3-minute loop with staggered plates; no measured belt speed is implied.
   const phase = [...p.id].reduce((n, c) => (n * 31 + c.charCodeAt(0)) % 180000, 0);
   at = now + ((phase - now % 180000 + 180000) % 180000);
  }
  return Number.isFinite(at) && at >= now - 10000 ? [{ plateId: p.id, at, seconds: Math.max(0, Math.ceil((at - now) / 1000)), simulated: source === "demo" }] : [];
 });
 return arrivals.sort((a, b) => a.at - b.at)[0] ?? null;
}
export function arrivalText(seconds: number): string {
 if (seconds === 0) return "预计已到口";
 const minutes = Math.floor(seconds / 60), remainder = seconds % 60;
 return minutes ? `${minutes}分${String(remainder).padStart(2, "0")}秒` : `${seconds}秒`;
}
