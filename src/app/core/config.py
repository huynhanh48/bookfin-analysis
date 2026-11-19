"""Cấu hình ứng dụng, đọc từ biến môi trường và file `.env`.

Mọi giá trị nhạy cảm (mật khẩu database, token, API key) chỉ đến từ môi trường;
repository chỉ chứa `.env.example` với giá trị giả.
"""

from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
AppEnv = Literal["development", "staging", "production", "test"]


class Settings(BaseSettings):
    """Cấu hình runtime của service.

    Attributes:
        app_name: Tên service, dùng cho log và OpenAPI title.
        app_env: Môi trường chạy; quyết định có bắt buộc admin key hay không.
        app_host: Host mặc định khi chạy bằng `python -m app`.
        app_port: Port mặc định khi chạy bằng `python -m app`.
        log_level: Mức log tối thiểu của ứng dụng.
        api_prefix: Tiền tố chung cho các router nghiệp vụ.
        database_url: DSN async SQLAlchemy (`postgresql+asyncpg://...`).
        db_echo: Bật log toàn bộ câu SQL, chỉ dùng khi debug.
        db_pool_size: Số connection giữ thường trực trong pool.
        db_max_overflow: Số connection tạm vượt pool size.
        redis_enabled: Bật/tắt Redis; hệ thống vẫn chạy đủ khi tắt.
        redis_url: DSN Redis, chỉ dùng khi `redis_enabled` là True.
        redis_quote_ttl_seconds: TTL cho snapshot giá realtime trong Redis.
        fcdata_base_url: Base URL của BookFin FCData Gateway.
        fcdata_ws_url: URL WebSocket realtime của gateway.
        fcdata_api_key: API key tuỳ chọn nếu gateway bật xác thực.
        fcdata_timeout_seconds: Timeout cho một request HTTP tới gateway.
        fcdata_max_concurrency: Số request đồng thời tối đa tới gateway.
        fcdata_max_retries: Số lần retry tối đa cho lỗi tạm thời.
        fcdata_ws_ping_seconds: Chu kỳ gửi ping WS; phải nhỏ hơn 90 giây idle.
        fcdata_ws_max_codes: Số mã tối đa mỗi lần subscribe WS.
        market_timezone: Múi giờ phiên giao dịch Việt Nam.
        history_default_limit: Số nến mặc định khi client không truyền limit.
        history_max_limit: Trần số nến trả về trong một request.
        compute_lookback: Số nến nạp để tính chỉ báo và cấu trúc thị trường.
        backfill_lookback_days: Số ngày lịch mặc định khi backfill.
        quote_stale_after_seconds: Ngưỡng đánh dấu snapshot giá là cũ.
        benchmark_symbol: Mã chỉ số dùng cho chỉ báo sức mạnh tương đối.
        admin_api_key: API key bắt buộc cho endpoint thao tác như backfill.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "stock-research"
    app_env: AppEnv = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    log_level: LogLevel = "INFO"
    api_prefix: str = "/api/v1"

    database_url: str = "postgresql+asyncpg://stock:change-me-local@localhost:5432/stock_research"
    db_echo: bool = False
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=10, ge=0)

    rag_enabled: bool = False
    openrouter_api_key: str | None = Field(default=None, repr=False)
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_timeout_seconds: float = Field(default=60, gt=0, le=300)
    openrouter_max_retries: int = Field(default=2, ge=0, le=5)
    rag_vision_model: str | None = None
    rag_llm_model: str = "google/gemini-2.5-flash"
    rag_text_embedding_model: str = "openai/text-embedding-3-small"
    rag_text_embedding_dimension: int = Field(default=1536, ge=1, le=2000)
    rag_multimodal_embedding_model: str = "mongodb/voyage-multimodal-3.5"
    rag_multimodal_embedding_dimension: int = Field(default=1024, ge=1, le=2000)
    rag_case_multimodal: bool = True
    rag_storage_dir: str = "./data/rag"
    rag_upload_dir: str = "./data/uploads"
    rag_max_upload_bytes: int = Field(default=10_485_760, ge=1024)
    rag_max_image_pixels: int = Field(default=25_000_000, ge=1)
    rag_case_graph_enabled: bool = False
    rag_case_index_image: bool = False
    rag_llm_max_async: int = Field(default=4, ge=1, le=16)
    rag_embedding_max_async: int = Field(default=4, ge=1, le=16)
    rag_max_parallel_insert: int = Field(default=2, ge=1, le=16)
    rag_remote_parser_url: str | None = None
    rag_job_timeout_seconds: int = Field(default=600, ge=10, le=3600)

    rag_enrichment_enabled: bool = False
    rag_news_enabled: bool = False
    tavily_api_key: str | None = Field(default=None, repr=False)
    firecrawl_api_key: str | None = Field(default=None, repr=False)
    firecrawl_base_url: str = "https://api.firecrawl.dev"
    rag_news_company_domains: list[str] = Field(default_factory=list)
    rag_news_max_sources: int = Field(default=5, ge=1, le=10)
    rag_news_lookback_days: int = Field(default=30, ge=1, le=365)
    rag_news_timeout_seconds: float = Field(default=20, gt=0, le=120)
    rag_news_min_relevance: float = Field(default=0.5, ge=0, le=1)
    # A known publication date is sufficient by default; unknown revision time is
    # recorded as a warning rather than discarding otherwise valid historical news.
    rag_news_allow_unknown_revision: bool = True
    rag_news_signal_window_enabled: bool = True
    rag_news_signal_buffer_days: int = Field(default=14, ge=0, le=365)
    # Recent confirmed divergence events are eligible for claim verification.
    rag_divergence_lookback_days: int = Field(default=60, ge=1, le=365)
    rag_rsi_overbought: float = Field(default=70.0, gt=0, lt=100)
    rag_rsi_oversold: float = Field(default=30.0, gt=0, lt=100)
    rag_provider_concurrency: int = Field(default=3, ge=1, le=10)
    rag_provider_cache_seconds: int = Field(default=86400, ge=60)
    rag_enrichment_max_attempts: int = Field(default=3, ge=1, le=10)
    rag_fetch_missing_market_data: bool = False
    rag_rerank_enabled: bool = False
    rag_rerank_provider: Literal["openrouter"] = "openrouter"
    rag_rerank_model: str = "mongodb/rerank-2.5-lite"
    rag_rerank_candidates: int = Field(default=20, ge=1, le=100)
    rag_rerank_top_n: int = Field(default=5, ge=1, le=50)
    rag_rerank_timeout_seconds: float = Field(default=10, gt=0, le=60)
    rag_rerank_fail_open: bool = True

    rag_redis_mirror_enabled: bool = False

    redis_enabled: bool = False
    redis_url: str = "redis://localhost:6379/0"
    redis_quote_ttl_seconds: int = Field(default=30, ge=1)

    fcdata_base_url: str = "https://fcdata.bookfin.vn"
    fcdata_ws_url: str = "wss://fcdata.bookfin.vn/ws"
    fcdata_api_key: str | None = None
    fcdata_timeout_seconds: float = Field(default=15.0, gt=0)
    fcdata_max_concurrency: int = Field(default=8, ge=1)
    fcdata_max_retries: int = Field(default=3, ge=0)
    fcdata_ws_ping_seconds: int = Field(default=30, ge=5, le=80)
    fcdata_ws_max_codes: int = Field(default=200, ge=1, le=200)

    market_timezone: str = "Asia/Ho_Chi_Minh"

    history_default_limit: int = Field(default=250, ge=1)
    history_max_limit: int = Field(default=1000, ge=1)
    indicator_unstable_periods: dict[str, int] = Field(default_factory=dict)
    compute_lookback: int = Field(default=400, ge=60)
    backfill_lookback_days: int = Field(default=400, ge=30, le=3650)
    quote_stale_after_seconds: int = Field(default=300, ge=1)
    benchmark_symbol: str = "VNINDEX"

    admin_api_key: str | None = None

    @field_validator("admin_api_key", "fcdata_api_key", mode="before")
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        """Coi chuỗi rỗng trong `.env` là chưa đặt, không phải một key hợp lệ.

        Docker Compose luôn truyền `ADMIN_API_KEY=` khi biến này để trống, nên
        nếu không chuẩn hoá thì `requires_admin_key` sẽ bật dù không có key nào.

        Args:
            value: Giá trị thô đọc từ biến môi trường.

        Returns:
            `None` khi giá trị rỗng hoặc chỉ có khoảng trắng; ngược lại giữ nguyên.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _validate_rsi_thresholds(self) -> "Settings":
        """Reject inverted RSI thresholds before any case is processed."""
        if self.rag_rsi_oversold >= self.rag_rsi_overbought:
            raise ValueError("RAG_RSI_OVERSOLD must be lower than RAG_RSI_OVERBOUGHT")
        return self

    @property
    def is_development(self) -> bool:
        """True khi chạy ở môi trường development hoặc test."""
        return self.app_env in {"development", "test"}

    @property
    def requires_admin_key(self) -> bool:
        """True khi endpoint quản trị bắt buộc phải có admin key."""
        return not self.is_development or self.admin_api_key is not None

    @property
    def market_tz(self) -> ZoneInfo:
        """Múi giờ phiên giao dịch dưới dạng `ZoneInfo`."""
        return ZoneInfo(self.market_timezone)


@lru_cache
def get_settings() -> Settings:
    """Trả về đối tượng `Settings` dùng chung cho cả process.

    Returns:
        Cấu hình đã được cache, đọc một lần từ môi trường và `.env`.
    """
    return Settings()
