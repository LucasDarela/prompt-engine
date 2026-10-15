"""Hierarquia de exceções de domínio. `code` é estável e exposto pela API (SPEC-001 §3)."""

from __future__ import annotations

from collections.abc import Sequence


class PromptEngineError(Exception):
    """Base de todas as exceções do domínio."""

    code: str = "internal_error"
    public_message: str = "Ocorreu um erro inesperado. Tente novamente."


class ShortCircuit(PromptEngineError):
    """Interrompe o pipeline de forma esperada (entrada recusada)."""


class InputValidationError(ShortCircuit):
    code = "invalid_input"
    public_message = "A mensagem enviada é inválida."

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class PromptInjectionDetected(ShortCircuit):
    code = "unsafe_input"
    public_message = "A mensagem não pôde ser processada com segurança."

    def __init__(self, score: float, signals: Sequence[str] = ()) -> None:
        super().__init__(f"prompt injection detectado (score={score:.2f})")
        self.score = score
        self.signals = list(signals)


class ProviderError(PromptEngineError):
    """Falha ao falar com um provedor de LLM."""

    retryable: bool = False

    def __init__(self, provider: str, detail: str = "") -> None:
        super().__init__(f"{provider}: {detail}" if detail else provider)
        self.provider = provider
        self.detail = detail


class ProviderUnavailableError(ProviderError):
    code = "provider_unavailable"
    public_message = "O serviço de IA está indisponível no momento. Tente novamente."
    retryable = True


class ProviderRateLimitError(ProviderError):
    code = "rate_limited"
    public_message = "Muitas requisições. Aguarde alguns instantes e tente novamente."
    retryable = True

    def __init__(self, provider: str, detail: str = "", retry_after: float | None = None) -> None:
        super().__init__(provider, detail)
        self.retry_after = retry_after


class ProviderBadRequestError(ProviderError):
    code = "provider_bad_request"


class ProviderConfigError(PromptEngineError):
    """Provider mal configurado (chave ausente, extra não instalado, modelo não definido)."""

    code = "provider_config"


class OutputValidationError(PromptEngineError):
    """Saída do modelo inválida. Tratada internamente (reparo/fallback); não chega ao usuário."""

    code = "output_invalid"

    def __init__(self, step: str, errors: Sequence[str]) -> None:
        super().__init__(f"{step}: {'; '.join(errors)}")
        self.step = step
        self.errors = list(errors)


class PromptNotFoundError(PromptEngineError):
    code = "prompt_not_found"

    def __init__(self, ref: str) -> None:
        super().__init__(ref)
        self.ref = ref


class PromptLibraryError(PromptEngineError):
    """Biblioteca de prompts inválida (detectado no startup)."""

    code = "prompt_library_invalid"


class TemplateRenderError(PromptEngineError):
    code = "template_error"

    def __init__(self, prompt_id: str, detail: str) -> None:
        super().__init__(f"{prompt_id}: {detail}")
        self.prompt_id = prompt_id


class FlowNotFoundError(PromptEngineError):
    code = "flow_not_found"
    public_message = "Fluxo não encontrado."

    def __init__(self, flow: str) -> None:
        super().__init__(flow)
        self.flow = flow


class PipelineMisconfigured(PromptEngineError):
    code = "pipeline_misconfigured"
