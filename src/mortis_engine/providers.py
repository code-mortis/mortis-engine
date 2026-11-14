"""Connection defaults for the provider APIs supported by Mortis."""

from typing import NamedTuple


class Preset(NamedTuple):
    label: str
    base_url: str
    protocol: str = "openai"


PRESETS = {
    # https://platform.claude.com/docs/en/api/overview
    "anthropic": Preset("Anthropic", "https://api.anthropic.com", "anthropic"),
    # Bedrock resolves its endpoint from the AWS region.
    "bedrock": Preset("Amazon Bedrock", "", "anthropic-bedrock"),
    # https://cloud.baidu.com/doc/qianfan-api/s/wmwbl48n4
    "baidu-qianfan": Preset("Baidu Qianfan", "https://qianfan.baidubce.com/v2"),
    # https://help.aliyun.com/zh/model-studio/base-url
    "dashscope": Preset("Alibaba Cloud Model Studio", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    "dashscope-tokenplan": Preset("Alibaba Cloud Token Plan", "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"),
    # https://api-docs.deepseek.com/
    "deepseek": Preset("DeepSeek", "https://api.deepseek.com"),
    # https://github.com/edenai/pi-edenai
    "edenai": Preset("Eden AI", "https://api.edenai.run/v3"),
    # https://ai.google.dev/gemini-api/docs/openai
    "gemini": Preset("Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai"),
    # https://www.xfyun.cn/doc/spark/HTTP调用文档.html
    "iflytek": Preset("iFLYTEK Spark", "https://spark-api-open.xf-yun.com/v1"),
    # https://github.com/MoonshotAI/kimi-cli/blob/main/src/kimi_cli/auth/platforms.py
    "kimi": Preset("Kimi (China)", "https://api.moonshot.cn/v1"),
    "kimi-global": Preset("Kimi (international)", "https://api.moonshot.ai/v1"),
    # https://docs.litellm.ai/docs/proxy/client_setup/codex_cli
    "litellm": Preset("LiteLLM Proxy", "http://localhost:4000/v1"),
    # https://mimo.mi.com/docs/en-US/api/chat/openai-api
    "mimo": Preset("Xiaomi MiMo", "https://api.xiaomimimo.com/v1"),
    # https://platform.minimax.io/docs/api-reference/text-openai-api
    "minimax": Preset("MiniMax (international)", "https://api.minimax.io/v1"),
    "minimax-cn": Preset("MiniMax (China)", "https://api.minimaxi.com/v1"),
    # https://docs.mistral.ai/api/endpoint/chat
    "mistral": Preset("Mistral AI", "https://api.mistral.ai/v1"),
    # https://docs.novita.ai/api-reference/model-apis-llm-create-chat-completion
    "novita": Preset("Novita AI", "https://api.novita.ai/openai/v1"),
    # https://docs.ollama.com/api/openai-compatibility
    "ollama-cloud": Preset("Ollama Cloud", "https://ollama.com/v1"),
    # https://developers.openai.com/api/reference/
    "openai": Preset("OpenAI Chat Completions", "https://api.openai.com/v1"),
    "openai-responses": Preset("OpenAI Responses", "https://api.openai.com/v1", "openai-responses"),
    # https://opencode.ai/docs/zen/ and https://opencode.ai/docs/go/
    "opencode": Preset("OpenCode Zen", "https://opencode.ai/zen/v1", "openai-responses"),
    "opencode-go": Preset("OpenCode Go", "https://opencode.ai/zen/go/v1"),
    # https://openrouter.ai/docs/quickstart
    "openrouter": Preset("OpenRouter", "https://openrouter.ai/api/v1"),
    # https://docs.siliconflow.com/ and https://docs.siliconflow.cn/
    "siliconflow": Preset("SiliconFlow (international)", "https://api.siliconflow.com/v1"),
    "siliconflow-cn": Preset("SiliconFlow (China)", "https://api.siliconflow.cn/v1"),
    # https://cloud.tencent.com/document/product/1823/130079 and /130119
    "tencent-tokenhub": Preset("Tencent TokenHub", "https://tokenhub.tencentmaas.com/v1"),
    "hy-tokenplan": Preset("Tencent Hunyuan Token Plan", "https://tokenhub.tencentmaas.com/plan/v3"),
    # https://docs.volcengine.com/docs/ark/quick-start
    "volcengine": Preset("Volcengine Ark", "https://ark.cn-beijing.volces.com/api/v3"),
    # https://docs.x.ai/developers/advanced-api-usage/regions
    "xai": Preset("xAI", "https://api.x.ai/v1"),
    # https://zcode.z.ai/cn/docs/configuration
    "z-ai": Preset("Zhipu GLM", "https://open.bigmodel.cn/api/paas/v4"),
    "z-ai-coding": Preset("Zhipu GLM Coding Plan", "https://open.bigmodel.cn/api/coding/paas/v4"),
    "custom": Preset("Custom endpoint", ""),
}
