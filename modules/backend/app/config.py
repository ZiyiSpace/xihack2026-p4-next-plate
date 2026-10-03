"""集中配置：环境变量优先，.env 兜底，全部有默认值。"""
import os

def _load_dotenv(path: str = None) -> None:
    path = path or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

_load_dotenv()

def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default

def _i(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---- Jev 视觉服务器 ----
JEV_BASE_URL = os.environ.get("JEV_BASE_URL", "https://u1201184-80c0-dda3c400.westb.seetacloud.com:8443")
# 密钥留空 => 离线规则模式：不调用模型，纯称重规则运行（模型挂了业务流程仍可用）
JEV_API_KEY = os.environ.get("JEV_API_KEY", "")
JEV_TIMEOUT_S = _f("JEV_TIMEOUT_S", 120)
# 单卡串行推理，全局并发上限（官方建议 2-3）
JEV_MAX_CONCURRENCY = _i("JEV_MAX_CONCURRENCY", 2)
IDENTIFY_CONFIDENCE_FLOOR = _f("IDENTIFY_CONFIDENCE_FLOOR", 0.6)

# ---- 数据与存储 ----
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "data", "hotpot.db"))
IMAGE_DIR = os.environ.get("IMAGE_DIR", os.path.join(BASE_DIR, "data", "images"))
DATASET_DIR = os.environ.get(
    "DATASET_DIR", os.path.join(os.path.dirname(BASE_DIR), "hotpot_dataset_v0_1")
)

# ---- 业务规则阈值（对齐 scenario_config.json）----
# 相邻两次正常读数最多相差 4g 而没有真实变化 => 噪声界
NOISE_BOUND_G = _f("NOISE_BOUND_G", 4)
# 同盘同站 20 秒内的多次读数视为一次经过（连续视频帧合并为一次有效事件）
PASS_MERGE_WINDOW_S = _f("PASS_MERGE_WINDOW_S", 20)
# 余量占比低于该值触发补菜评估
REFILL_RATIO_THRESHOLD = _f("REFILL_RATIO_THRESHOLD", 0.3)
# 最近窗口内取用速度低于该值(g/min) => 低需求
LOW_DEMAND_VELOCITY = _f("LOW_DEMAND_VELOCITY", 0.5)
# 滞留告警：无取用超过该分钟数 => 关注
STAGNATION_WARN_MIN = _f("STAGNATION_WARN_MIN", 15)
# 菜品保鲜窗口（分钟），超时未售罄 => 报废风险；可被菜品目录覆盖
FRESHNESS_WINDOW_MIN = _f("FRESHNESS_WINDOW_MIN", 30)
# 补菜任务保留时长：已完成任务不再重复触发的静默期（分钟）
TASK_QUIET_MIN = _f("TASK_QUIET_MIN", 10)
