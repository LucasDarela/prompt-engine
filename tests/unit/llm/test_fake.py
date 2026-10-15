import pytest

from prompt_engine.core.errors import ProviderBadRequestError, ProviderRateLimitError
from prompt_engine.llm.base import LLMRequest
from prompt_engine.llm.fake import FakeProvider
from tests.unit.llm._support import make_request


async def test_script_by_step_and_call_log() -> None:
    fake = FakeProvider({"classify": "A", "draft_reply": "B"})
    assert (await fake.generate(make_request())).text == "A"
    draft = make_request(metadata={"step": "draft_reply"})
    assert (await fake.generate(draft)).text == "B"
    assert [c.metadata["step"] for c in fake.calls] == ["classify", "draft_reply"]


async def test_sequence_is_consumed_in_order() -> None:
    fake = FakeProvider({"classify": [ProviderRateLimitError("fake"), "ok"]})
    with pytest.raises(ProviderRateLimitError):
        await fake.generate(make_request())
    assert (await fake.generate(make_request())).text == "ok"
    with pytest.raises(ProviderBadRequestError, match="esgotado"):
        await fake.generate(make_request())


async def test_callable_receives_request() -> None:
    def echo_model(request: LLMRequest) -> str:
        return request.model

    fake = FakeProvider({"classify": echo_model})
    assert (await fake.generate(make_request(model="m1"))).text == "m1"


async def test_missing_script_is_bad_request() -> None:
    with pytest.raises(ProviderBadRequestError, match="roteiro"):
        await FakeProvider({}).generate(make_request())


async def test_usage_is_word_count() -> None:
    fake = FakeProvider({"classify": "um dois três"})
    response = await fake.generate(make_request())
    assert response.usage.output_tokens == 3
    assert response.usage.input_tokens > 0
