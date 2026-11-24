"""Deterministic replies for basic conversation.

Greetings and meta questions must not depend on the knowledge index: answering
"xin chào" with "no matching documents" makes the chat feel broken and spends a
paid retrieval call for something that has no financial context.
"""

import re
import unicodedata

GREETING_ANSWER = (
    "Chào bạn! Mình là BookFin AI, trợ lý phân tích cổ phiếu Việt Nam.\n\n"
    "Bạn có thể:\n"
    "- Hỏi về mã đã nạp trong kho kiến thức (ví dụ SSI, KDH).\n"
    "- Yêu cầu phân tích kỹ thuật: xu hướng, RSI, MACD, MA, nến, phân kỳ.\n"
    "- Chuyển chế độ **Phân tích cổ phiếu** để chạy research agent.\n"
    "- Nạp thêm tài liệu/chart ở mục **Kho kiến thức RAG**."
)

THANKS_ANSWER = (
    "Rất vui được hỗ trợ! Bạn có thể hỏi tiếp về mã cổ phiếu, "
    "hoặc nạp thêm tài liệu vào Kho kiến thức RAG khi cần."
)

FAREWELL_ANSWER = "Tạm biệt bạn! Khi cần phân tích cổ phiếu, cứ quay lại nhé."

CAPABILITY_ANSWER = (
    "Mình là BookFin AI, trợ lý phân tích cổ phiếu Việt Nam.\n\n"
    "- Trả lời dựa trên tài liệu, ghi chú và chart đã nạp trong kho kiến thức.\n"
    "- Hỗ trợ phân tích kỹ thuật: xu hướng, RSI, MACD, MA, nến, phân kỳ.\n"
    "- Chế độ **Phân tích cổ phiếu** chạy research agent để tổng hợp báo cáo.\n"
    "- Bạn có thể nạp text, ảnh chart hoặc PDF ở mục **Kho kiến thức RAG**."
)

WELLBEING_ANSWER = "Mình ổn, cảm ơn bạn! Mình sẵn sàng hỗ trợ phân tích cổ phiếu hoặc tìm tài liệu trong kho kiến thức."

# Matching is done on a casefolded, accent-free, punctuation-free form so both
# "xin chào" and "xin chao" work without maintaining duplicate phrase lists.
_GREETING = re.compile(
    r"^(?:(?:xin )?chao(?: (?:ban|em|anh|chi|ca nha|moi nguoi))?|hello|hi|hey|alo(?: ban)?)$"
)
_THANKS = re.compile(r"^(?:cam on|thank you|thanks|tks)(?: (?:ban|nhieu|nhe|a|rat nhieu))*$")
_FAREWELL = re.compile(r"^(?:tam biet|bye|goodbye|hen gap lai)(?: (?:ban|nhe))*$")
_WELLBEING = re.compile(r"^(?:(?:ban )?khoe khong(?: ban)?|ban the nao|ban co khoe khong)$")
_CAPABILITY = re.compile(
    r"^(?:ban (?:la ai|ten (?:la )?gi|lam duoc gi|co the lam gi|giup duoc gi|biet gi)"
    r"|huong dan(?: su dung)?|tro giup|help|gioi thieu(?: ve ban)?)(?: vay| the| nhi)?$"
)


def _normalize(text: str) -> str:
    """Fold case, Vietnamese accents and punctuation before phrase matching."""
    decomposed = unicodedata.normalize("NFD", text.casefold())
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    plain = plain.replace("đ", "d")
    return " ".join(re.sub(r"[^a-z0-9\s]", " ", plain).split())


def smalltalk_answer(question: str) -> str | None:
    """Return a canned reply only for whole-phrase small talk, else ``None``.

    Whole-phrase matching matters: "Chào bạn, phân tích SSI" is a real question
    and must still reach retrieval instead of returning a greeting script.
    """
    normalized = _normalize(question)
    if not normalized:
        return None
    if _GREETING.fullmatch(normalized):
        return GREETING_ANSWER
    if _THANKS.fullmatch(normalized):
        return THANKS_ANSWER
    if _FAREWELL.fullmatch(normalized):
        return FAREWELL_ANSWER
    if _WELLBEING.fullmatch(normalized):
        return WELLBEING_ANSWER
    if _CAPABILITY.fullmatch(normalized):
        return CAPABILITY_ANSWER
    return None
