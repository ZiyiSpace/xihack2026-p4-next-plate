import type { Dish, ImportData, State } from "./domain.ts";
export function emptyImports():ImportData { return {suppliers:[],ingredients:[],purchases:[],recipes:[],observations:{},receipts:{},status:{}}; }
export function normalizeState(s:State):State { s.imports={...emptyImports(),...s.imports}; for(const d of s.dishes)d.costSource??="manual"; return s; }
export function ingredientPrice(s:State,id:string):number|null {
 const items=s.imports?.purchases.flatMap(p=>p.items).filter(i=>i.ingredientId===id)||[];
 const weight=items.reduce((n,i)=>n+i.weightKg,0); return weight?items.reduce((n,i)=>n+i.amount,0)/weight:null;
}
export function recipeCost(s:State,dishId:string):number|null {
 const recipe=s.imports?.recipes.find(r=>r.dishId===dishId); if(!recipe?.items.length)return null;
 let total=0; for(const i of recipe.items){const price=ingredientPrice(s,i.ingredientId);if(price===null)return null;total+=price*i.weightKg;} return total;
}
export function effectiveCost(s:State,d:Dish):number|null { return d.costSource==="recipe"?recipeCost(s,d.id):d.confirmed?d.costPerKg:null; }
