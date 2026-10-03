declare namespace Cloudflare {
  interface Env {
    DB?: D1Database;
    BUCKET?: R2Bucket;
    MODEL_KEY_ENCRYPTION_KEY?: string;
    DEEPSEEK_API_KEY?: string;
    DEEPSEEK_MODEL?: string;
    DEEPSEEK_BASE_URL?: string;
    INGEST_API_KEY?: string;
  }
}
