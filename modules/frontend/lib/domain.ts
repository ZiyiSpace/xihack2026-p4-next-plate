export type Source = "demo" | "live" | "test";
export type Unit = "g" | "件";
export interface Dish { id:string; name:string; category:string; unit:Unit; capacity:number; batch:number; target:number; leadMinutes:number; safetyMinutes:number; costPerKg:number; confirmed:boolean; costSource?:"manual"|"recipe"; }
export interface Plate { id:string; dishId:string; quantity:number; weightG:number; seenAt:string; quality:number; issue?:string; refillArrivalAt?:string; removed:boolean; }
export interface Ledger { id:string; at:string; dishId:string; plateId:string; kind:"opening"|"take"|"refill"|"waste"|"withdraw"; quantity:number; weightG:number; reason?:string; costPerKg:number|null; }
export interface Task { id:string; dishId:string; quantity:number; createdAt:string; dueAt:string; status:"pending"|"preparing"|"completed"|"cancelled"; reason:string; actual:number; refillBaseline:number; availableAt?:string; note?:string; updatedAt:string; }
export interface Order { id:string; at:string; guests:number; revenue:number; groupSize:number; }
export interface Settings { autoEnabled:boolean; closeTime:string; staleMinutes:number; }
export interface State { source:Source; clock:string; dishes:Dish[]; plates:Record<string,Plate>; ledger:Ledger[]; tasks:Task[]; orders:Order[]; seenEventIds:string[]; actionIds:string[]; settings:Settings; revision:number; imports:ImportData; }
export interface Observation { eventId:string; timestamp:string; plateId:string; dishId:string; kind:"observe"|"add"|"remove"; netWeightG:number; count?:number; confidence:number; disposition?:"discard"|"other_loss"|"retain"; reason?:string; refillArrivalAt?:string; }
export interface DishSummary extends Dish { stock:number; weightG:number; plates:number; rate:number; minutesLeft:number|null; incoming:number; status:"urgent"|"watch"|"okay"|"anomaly"; takeG:number; takeQuantity:number; wasteG:number; wasteCost:number; refillCount:number; refillQuantity:number; expense:number; costReady:boolean; }
export interface Summary { source:Source; clock:string; dishes:DishSummary[]; tasks:Task[]; ledger:Ledger[]; orders:Order[]; settings:Settings; revision:number; metrics:{ guests:number; revenue:number; takenG:number; wastedG:number; wasteCost:number; foodExpense:number; perGuestCost:number|null; perGuestWaste:number|null; foodMargin:number|null; pending:number; anomaly:number; costReady:boolean; }; hourly:{hour:string;guests:number;takeG:number}[]; integrations:{deepseekConfigured:boolean;ingestConfigured:boolean;model:string}; imports:ImportData; captures?:Capture[]; samples?:SampleList; }

export interface CaptureBindingCheck { recognized?:string; confidence?:number; expected?:string; ok?:boolean; below_floor?:boolean; skipped?:boolean; note?:string; source?:string; }
export interface Capture { eventId:string; at:string; plateId:string; stationId:string; dishId:string|null; dishName:string|null; claimedDishId:string|null; netWeightG:number|null; quality:string; imageUrl:string|null; classification:string|null; note:string|null; bindingCheck:CaptureBindingCheck|null; simulated:boolean; }

export type SampleStatus = "pending"|"identified"|"need_dish"|"done"|"failed";
export interface SampleResult { error?:string|null; classification?:string|null; note?:string|null; taken_g?:number|null; delta_g?:number|null; binding_check?:CaptureBindingCheck|null; trusted_net_g?:number|null; new_tasks?:string[]; }
export interface SampleRow { sample_id:string; image_name:string; image_ref:string; bytes:number; plate_id:string; station_id:string; net_weight_g:number|null; dish_id:string|null; observed_at:string; status:SampleStatus; recognized:CaptureBindingCheck|null; result:SampleResult|null; created_at:string; }
export interface SampleList { samples:SampleRow[]; counts:{total:number;pending:number;done:number;no_weight:number}; }
/** 入账返回的每条结果，字段与 `SampleResult` 相同，外加它属于哪条样例、哪个盘。 */
export interface SampleIngestOutcome extends SampleResult { sample_id:string; image_name:string; plate_id:string; dish_id:string|null; net_weight_g:number|null; }
export interface SampleSkip { sample_id:string; image_name:string; reason:string; }
export interface SampleIngestResult extends SampleList { ingested:number; plates:string[]; blocked:SampleSkip[]; failed:SampleSkip[]; results:SampleIngestOutcome[]; }

export type ImportKind = "suppliers"|"ingredients"|"purchases"|"recipes"|"orders"|"observations";
export interface Supplier { id:string; name:string; contact?:string; }
export interface Ingredient { id:string; name:string; }
export interface Purchase { id:string; at:string; supplierId:string; items:{ingredientId:string; weightKg:number; amount:number}[]; }
export interface Recipe { id:string; dishId:string; items:{ingredientId:string; weightKg:number}[]; }
export interface ImportReceipt { requestId:string; fingerprint:string; added:number; updated:number; duplicate:number; at:string; }
export interface ImportStatus { receivedAt?:string; attemptedAt:string; count:number; error?:string; }
export interface ImportData { suppliers:Supplier[]; ingredients:Ingredient[]; purchases:Purchase[]; recipes:Recipe[]; observations:Record<string,string>; receipts:Record<string,ImportReceipt>; status:Partial<Record<ImportKind,ImportStatus>>; }
