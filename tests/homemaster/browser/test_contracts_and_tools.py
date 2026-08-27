from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

import pytest

from homemaster.browser.contracts import BrowserSession, BrowserSessionError
from homemaster.browser.playwright_session import PlaywrightBrowserSession
from homemaster.browser.policy import BrowserPolicy
from homemaster.browser.tools import build_browser_registered_tools, build_browser_run_registry
from homemaster.providers.transports.openai_chat import OpenAIChatTransport
from homemaster.tools.adapters import from_registered_tool
from homemaster.tools.base import ToolExecutionContext, ToolRegistry, ToolRegistryError
from homemaster.tools.browser import registry as browser_registry

SAFE_NAMES = (
    "browser_navigate",
    "browser_history",
    "browser_inspect",
    "browser_find",
    "browser_read",
    "browser_extract",
    "browser_screenshot",
    "browser_console",
    "browser_analyze",
    "browser_click",
    "browser_fill",
    "browser_type",
    "browser_select",
    "browser_check",
    "browser_uncheck",
    "browser_hover",
    "browser_focus",
    "browser_press",
    "browser_scroll",
    "browser_upload",
    "browser_drag",
    "browser_backfill",
    "browser_tabs",
    "browser_dialog",
    "browser_network",
    "browser_download",
    "browser_wait",
)

DESCRIPTION_SHA256 = {
    "browser_navigate": "faf1a4382ff28014c32981f6925997c31f5af70fe406315e2d2d033761fb0024",
    "browser_history": "84b6d10d5f2b3e9a282b4fd2203c3198a11aaab07524f0bb7a0a8d157e4140e3",
    "browser_inspect": "b4f3d6666c1c72c661265302f666e114f83ee8621eb9eebeb874ec7141cbabfb",
    "browser_find": "6c679e7126d622a8c74ed14903c930467201e2ba5b6f63694f76f4f50c2d5386",
    "browser_read": "bbcd708faab6ef7cc3307e731b34390368b43427061da2859cae3c9929197334",
    "browser_extract": "aae4286a39ae55397711b5340e2cbe341641d4a0312e14d206bd5cf46a865fd0",
    "browser_screenshot": "d07d8102c70720b1a0e0b09f1c3c99a508b8310f664419caa45def2bacb7d998",
    "browser_console": "24a447789861ec909477ecdded49f7bc9d6a2e50d3bf45e02d70bbc7f93a7370",
    "browser_analyze": "2e73dd420ebeaf3130364e5bf65f85a422f8b0d2f8af7ced13a099963e91153b",
    "browser_click": "21f77e28d8226c5e9982760e70a3d132252e2fd292846c9a90cf683c5e493f1e",
    "browser_fill": "8e1e50b71dd96cda88dc191d55e7c6e90cb9b9da36acec8739b34dbd9009ce48",
    "browser_type": "6ab0f86f13187b42742fd1a34d115f327171424e5c59cd74e533c25110cf6fb2",
    "browser_select": "6fc6538fc2a3cf2da47cb7130cd3fd83170aed7031a484e70e7af1b89d3b76d0",
    "browser_check": "da61d0baa42a06dcb9745d7ad2a40c8e67df01e46a279fc293f412ec6ac89ba3",
    "browser_uncheck": "599468a35fa1406ec0573ad12be1f9ace6823ee8e1cfdf83e2c90279a6b9b616",
    "browser_hover": "082b8dbff215207127a50393e0c2b8b70108c225dc4fffe09c133d3e4c767495",
    "browser_focus": "772ef71955878103f173ee0427c8e52e31cf8bd45326283c5b9ad3fbaef3972a",
    "browser_press": "e7a1bc9b3c0a2a3ede229acc9bb642599a2be826012d750bcc69f403527ffbe8",
    "browser_scroll": "9665cabd81ac53e345df38a10d15d7dcd3af8a86d412f7c6b3f5e55bc01e8656",
    "browser_upload": "7f45e3f84087ded0f61df323d633deda70682e25c09676f77a1ffadce34e867f",
    "browser_drag": "cee44e009fe19f1167727de1d4e1ac8918b2e449c518459e8ae2defac7be4e98",
    "browser_backfill": "073506d8016ea3c39da6b5332198318e4c693948583c66196cfad9bf43c76891",
    "browser_tabs": "9197319c51560dc9e934e29b39fdd92d596f511e23821af9b3b02aaa871cf4a9",
    "browser_dialog": "14320d0c2cd1caf112e01550e31176808b62ee6a082bb43bbdb6adbbc9d7694a",
    "browser_network": "6fb233136e4e2bc62ed65771131cdd3cc88f9c8934ad4990cc4e24a94130875c",
    "browser_download": "f9047ceac1788ba2da97745e179d02b5176908730bd73e3f2457cb6c6e96367c",
    "browser_wait": "dedf4b94372e9640b8241d30805371e7e73abc5dc90a2777e5677cf09f0e7eee",
    "browser_eval": "0e51bd183eefe018c60db67e5b08e8268d3a4d9e0b6c1cecf29738e4a2bcfb11",
}

PROVIDER_SCHEMA_SHA256 = {
    "browser_navigate": "6fb9350d2f30f9281f4d231fe869447b0aac100b6436b078bb2b9f0df98848a1",
    "browser_history": "65b7168916c2c4809b18ad2dd0dda2a77206658c1c29e5fdd262c051d174f64d",
    "browser_inspect": "7607992d0d41950fdabc30ef19eb43cbbef0644ec5177a6bd6edcbb3bed04bfc",
    "browser_find": "d39ea1b6dff2cc93f48ebfe9de621ef63044c9e2107468a6d7c9d94dd9f15ab1",
    "browser_read": "fa4fab69f597a6f721a14e043862b9e7087c0888800cca3c074446754425773c",
    "browser_extract": "9880fbaa8a8726d1b7723f5d3adb0bfd1133c1ccda74e8443521f6d7e01be679",
    "browser_screenshot": "57ce3af5922a3742bd2a8fe5f8357a0a229d5414ff811d0e37f318e52e5ae57e",
    "browser_console": "00591c5ea4d2c9685be52f1a18f6398e94c87caa4503889979326305c0ad9235",
    "browser_analyze": "5c7f4114f8dbf947f2b6ebd8b0bb67915a56a73dee3c4ff145bd5c920d5cd417",
    "browser_click": "eee50e368ccdc1c47a74353d44637b652ab53a8439bb3716816b332175869cbd",
    "browser_fill": "a6dadd45bfb905005178c3bd0eac9cc61a012a09e99c9b08873eab8dc308303a",
    "browser_type": "6a58ab1470263fdefa2c33cb083950e334750cb7f387aff2882f575bc371d8b4",
    "browser_select": "e106e4a9c63c2c8da34169f1e627fd470a8105e78ceb5853377769f113a6bb87",
    "browser_check": "6aa3abda933ad554df562c38021e9684025b44c92de424bf408a39dd4302eff2",
    "browser_uncheck": "6aa3abda933ad554df562c38021e9684025b44c92de424bf408a39dd4302eff2",
    "browser_hover": "35697b9ab4fa6153efeb8e80402537df072d3c0208c1681b3e88f44e17ffbf48",
    "browser_focus": "6aa3abda933ad554df562c38021e9684025b44c92de424bf408a39dd4302eff2",
    "browser_press": "a6a0818b5250cceddda33eb06bf7155f45e26bd7bcf727170024bf86be892540",
    "browser_scroll": "08a9f55230232b5e9b5eb2b43c655a31094f1bbc39b7859301d522528966887b",
    "browser_upload": "7e471d873455727c334c1b9483349d2dafa2460c7959d4f6d2fd75de4a94a489",
    "browser_drag": "5a9e231ae15de4b537249ab360a3f15043a450881183546ded06a9fb60ac6c05",
    "browser_backfill": "9fb25434fbe879c651a779b79d0cab8d0d5e95c839d2e4d63822f2f99376358b",
    "browser_tabs": "4336a59051dfda7fc060f4d4741e7482d46f52bd119f182bc66d32edf0115987",
    "browser_dialog": "5df865d1c6cf012a6fb1c63157c9a34c8439eb667ba7a39163dc515dd525e8d0",
    "browser_network": "210d1e007916985cbf21bb723cdf2841140d52a627329435b980b0349beb0ca8",
    "browser_download": "c75d58054e823adbb78f8416483bb2d3da5e58f91cb7835b2144c34303aa3d55",
    "browser_wait": "867a84d28eab552a6c27f9b2b7a58c65f91eb818d72d8092a34f7b29b183a00a",
    "browser_eval": "58659c8921630d1fe2a6d0faf63d4cf9cfa8ce49636750208c0823df1d62f629",
}


def _session(*, eval_allowed: bool = False):
    class Session:
        policy = BrowserPolicy(allowed_origins=("http://example.test",), eval_allowed=eval_allowed)

    return Session()


def test_browser_session_protocol_public_method_audit() -> None:
    methods = {
        name
        for name, value in inspect.getmembers(BrowserSession, inspect.isfunction)
        if not name.startswith("_")
    }
    assert methods == {
        "navigate",
        "history",
        "inspect",
        "find",
        "read",
        "extract",
        "fill",
        "type",
        "select",
        "check",
        "uncheck",
        "click",
        "hover",
        "focus",
        "press",
        "scroll",
        "upload",
        "drag",
        "backfill",
        "tabs",
        "dialog",
        "network",
        "download",
        "wait",
        "screenshot",
        "eval",
        "analyze",
        "aclose",
    }


def test_playwright_session_implements_every_public_protocol_method(tmp_path: Path) -> None:
    session = PlaywrightBrowserSession(
        session_id="audit",
        policy=BrowserPolicy(allowed_origins=("http://example.test",)),
        video_dir=tmp_path,
    )
    for name in inspect.getmembers(BrowserSession, inspect.isfunction):
        if not name[0].startswith("_"):
            assert callable(getattr(session, name[0], None))


def test_browser_core_has_no_benchmark_imports() -> None:
    root = Path(__file__).parents[3] / "src" / "homemaster" / "browser"
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
    assert "homemaster.benchmarking" not in source


def test_browser_registered_tools_lock_v31_surface_and_eval_gate() -> None:
    safe = build_browser_registered_tools(_session())
    assert tuple(tool.definition.model_alias for tool in safe) == SAFE_NAMES
    assert "observe" not in SAFE_NAMES
    assert all(tool.definition.version == "3.1.0" for tool in safe)
    assert all(tool.definition.input_schema["additionalProperties"] is False for tool in safe)
    assert all(tool.definition.description for tool in safe)

    allowed = build_browser_registered_tools(_session(eval_allowed=True))
    assert tuple(tool.definition.model_alias for tool in allowed) == (*SAFE_NAMES, "browser_eval")
    eval_definition = allowed[-1].definition
    assert eval_definition.required_capabilities == ("browser.eval",)
    assert tuple(eval_definition.input_schema["required"]) == ("script", "expected_postcondition")

    target_tools = {
        "browser_click",
        "browser_fill",
        "browser_type",
        "browser_select",
        "browser_check",
        "browser_uncheck",
        "browser_hover",
        "browser_focus",
        "browser_upload",
        "browser_backfill",
    }
    definitions = {tool.definition.model_alias: tool.definition for tool in safe}
    for name in target_tools:
        target = definitions[name].input_schema["properties"]["target"]
        assert set(target["properties"]) == {
            "role",
            "name",
            "label",
            "text",
            "testid",
            "match",
            "nth",
            "frame_ref",
            "tab_ref",
            "target_ref",
        }
        assert "snapshot_id" not in definitions[name].input_schema["properties"]
    assert all(not tool.definition.requires_model_observation for tool in safe)


def test_provider_serialized_browser_contract_is_exact() -> None:
    registered = build_browser_registered_tools(_session(eval_allowed=True))
    normalized = [from_registered_tool(tool).to_api_schema() for tool in registered]
    request = OpenAIChatTransport().build_create_kwargs(
        model="contract-model", messages=[], tools=normalized
    )
    functions = [item["function"] for item in request["tools"]]
    assert tuple(item["name"] for item in functions) == (*SAFE_NAMES, "browser_eval")
    assert all(set(item) == {"name", "description", "parameters"} for item in functions)
    assert {
        item["name"]: hashlib.sha256(item["description"].encode()).hexdigest()
        for item in functions
    } == DESCRIPTION_SHA256
    assert {
        item["name"]: hashlib.sha256(
            json.dumps(
                item["parameters"], sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        for item in functions
    } == PROVIDER_SCHEMA_SHA256


def test_browser_schema_required_alternatives_fail_closed() -> None:
    tools = {
        tool.definition.model_alias: from_registered_tool(tool)
        for tool in build_browser_registered_tools(_session(eval_allowed=True))
    }
    with pytest.raises(ValueError, match="not valid under any"):
        tools["browser_select"].input_model(target={"name": "Region"})
    with pytest.raises(ValueError, match="valid under each"):
        tools["browser_select"].input_model(
            target={"name": "Region"}, option="US", options=["US"]
        )
    with pytest.raises(ValueError, match="request_ref"):
        tools["browser_network"].input_model(mode="detail")
    with pytest.raises(ValueError, match="tab_ref"):
        tools["browser_tabs"].input_model(action="close")
    with pytest.raises(ValueError, match="target"):
        tools["browser_scroll"].input_model(mode="into_view")
    with pytest.raises(ValueError, match="target"):
        tools["browser_read"].input_model(kind="value")
    with pytest.raises(ValueError, match="Additional properties"):
        tools["browser_wait"].input_model(
            condition={"kind": "text_present", "value": "ready", "unknown": True}
        )


def test_each_safe_browser_tool_is_owned_by_a_dedicated_module() -> None:
    builders = {
        builder(_session()).definition.model_alias: builder.__module__
        for builder in browser_registry._BUILDERS
    }
    assert tuple(builders) == SAFE_NAMES
    assert len(set(builders.values())) == len(SAFE_NAMES)


def test_browser_run_registry_is_frozen_and_removes_observe() -> None:
    registry = build_browser_run_registry(ToolRegistry(), _session())
    assert registry.frozen is True
    assert registry.get("observe") is None
    assert registry.get("browser_screenshot") is not None
    with pytest.raises(ToolRegistryError, match="frozen"):
        registry.register_many(())


@pytest.mark.asyncio
async def test_failed_browser_action_preserves_structured_error(tmp_path: Path) -> None:
    class ObscuredSession:
        policy = BrowserPolicy(allowed_origins=("http://example.test",))

        async def click(self, target, **kwargs):
            del target, kwargs
            raise BrowserSessionError("target_obscured", "target is obscured")

    registered = next(
        tool
        for tool in build_browser_registered_tools(ObscuredSession())
        if tool.definition.model_alias == "browser_click"
    )
    tool = from_registered_tool(registered)
    arguments = tool.input_model(target={"role": "button", "name": "Apply"})
    result = await tool.execute(arguments, ToolExecutionContext(tmp_path))
    assert result.is_error is True
    assert result.metadata["error_code"] == "target_obscured"


@pytest.mark.asyncio
async def test_semantic_browser_action_returns_receipt_without_forced_inspect(
    tmp_path: Path,
) -> None:
    class ClickableSession:
        policy = BrowserPolicy(allowed_origins=("http://example.test",))

        async def click(self, target, **kwargs):
            return {"target": target, "kwargs": kwargs, "interaction_verified": True}

    registered = next(
        tool
        for tool in build_browser_registered_tools(ClickableSession())
        if tool.definition.model_alias == "browser_click"
    )
    tool = from_registered_tool(registered)
    arguments = tool.input_model(target={"role": "button", "name": "Apply"})
    result = await tool.execute(arguments, ToolExecutionContext(tmp_path))
    assert result.is_error is False
    assert result.metadata["interaction_verified"] is True
    assert result.metadata["target"] == {"role": "button", "name": "Apply"}
