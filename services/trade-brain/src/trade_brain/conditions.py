"""Backend-owned, machine-readable WAIT condition registry."""

WAIT_CONDITION_REGISTRY: dict[str, dict[str, str]] = {
    "WAIT_FOR_NEXT_CLOSED_15M": {
        "predicate_version": "wait-v1",
        "description_vi": "Chờ nến 15m tiếp theo đóng rồi tạo snapshot mới.",
    },
    "WAIT_FOR_PRICE_RECHECK": {
        "predicate_version": "wait-v1",
        "description_vi": "Chờ giá quay lại vùng candidate rồi tính lại toàn bộ điều kiện.",
    },
}


def allowed_wait_condition_ids() -> tuple[str, ...]:
    """Return stable condition IDs exposed to Claude and validation."""
    return tuple(WAIT_CONDITION_REGISTRY)
