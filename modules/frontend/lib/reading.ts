/**
 * 判读结果与读数的展示词汇：抓拍卡片、样例接入表、工作台共用同一份。
 *
 * 后端 `classification` 的取值在界面上只该有这一处说法。之前抓拍卡片和样例接入
 * 各抄了一遍（内容一致但没有约束），加上工作台自己的时间格式化，同一个东西
 * 一共三份——改一处就会两边不一致。
 */

/** 判读分类的中文说法；没列出的原样显示，不静默丢掉。 */
export const CLASSIFICATION_TEXT:Record<string,string>={
 baseline:"首次上盘基准",
 no_change:"无明显变化",
 removal:"取用",
 refill_confirmed:"补菜已确认",
 late_refill:"补菜（记录迟到）",
 unexplained_increase:"未解释突增",
 unexplained_decrease:"读数存疑待复测",
 anomaly_resolved:"异常已恢复",
 merged_pass:"同一次经过",
 out_of_order:"迟到旧事件",
 no_weight:"仅记录，无称重",
};

/**
 * 判读分类对应的状态配色。
 *
 * 只有真正需要人处理的才上色：补菜完成是好的，未解释突增要显眼，
 * 其余（首次上盘、无变化、合帧……）都是正常流转，保持中性。
 * 早先这里让没配色的分类一律回落到 `watch`，于是「首次上盘基准」被画成黄色告警。
 */
export const CLASSIFICATION_TONE:Record<string,string>={
 refill_confirmed:"okay",
 late_refill:"okay",
 anomaly_resolved:"okay",
 unexplained_increase:"urgent",
 unexplained_decrease:"urgent",
};

export const classificationLabel=(code?:string|null):string=>
 code?(CLASSIFICATION_TEXT[code]||code):"未判读";

export const classificationTone=(code?:string|null):string=>
 CLASSIFICATION_TONE[code||""]||"";

/** 门店时区的时刻（HH:MM）。 */
export const clock=(iso:string):string=>
 new Date(iso).toLocaleTimeString("zh-CN",{timeZone:"Asia/Shanghai",hour:"2-digit",minute:"2-digit",hour12:false});

/** 克数；没有读数显示破折号，不要显示成 0 克。 */
export const grams=(n:number|null|undefined):string=>
 n===null||n===undefined?"—":`${n.toLocaleString("zh-CN",{maximumFractionDigits:1})} 克`;

/** 置信度：模型结果里没有就显示破折号。 */
export const confidence=(n?:number):string=>
 typeof n==="number"?n.toFixed(2):"—";
