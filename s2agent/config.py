import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Config:
    base_url: str = os.getenv("LLM_BASE_URL", "")
    api_key: str = os.getenv("LLM_API_KEY", "")
    model: str = os.getenv("LLM_MODEL", "glm-5.3-flash")
    reasoning_effort: str = os.getenv("LLM_REASONING_EFFORT", "low")
    data_dir: Path = (ROOT / os.getenv("DATA_DIR", "data/stage2")).resolve()
    max_spend: float = float(os.getenv("MAX_SPEND", "12"))
    # Agent loop limits — protect the 15 USD budget.
    max_turns_per_stage: int = int(os.getenv("MAX_TURNS_PER_STAGE", "4"))
    recursion_limit: int = int(os.getenv("RECURSION_LIMIT", "80"))
    max_tool_chars: int = int(os.getenv("MAX_TOOL_CHARS", "6000"))
    image_max_side: int = int(os.getenv("IMAGE_MAX_SIDE", "1280"))


CFG = Config()
